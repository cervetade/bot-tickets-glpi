"""
orquestador.py — Módulo "Orquestador" del bot (el director de orquesta).

Recibe (telefono, mensaje) y devuelve la respuesta del bot. Por cada mensaje:
  1. Lee de persistencia en qué estado venía ese teléfono (o arranca de cero).
  2. Según el estado, decide qué hacer.
  3. Llama a la pieza que corresponda (clasificador / relevamiento).
  4. Guarda el nuevo estado en persistencia.
  5. Devuelve el texto para el usuario.

El flujo apunta a armar los 4 CAMPOS del ticket de GLPI:
  1) Lugar        -> se le pregunta al usuario (lista de plantas).
  2) Categoría    -> clasificación (motivo + categoría) + relevamiento de datos.
  3) Título       -> lo deduce la IA del detalle.
  4) Descripción  -> el detalle que contó el usuario.

NO sabe de WhatsApp: se prueba por consola igual que andará en producción.
(Sin identificación de usuario ni creación real del ticket todavía.)

Probarlo por consola:  python orquestador.py
"""

import json
import re
import unicodedata
from difflib import SequenceMatcher

import persistencia
from clasificador import clasificar, clasificar_intencion, relevar, opciones_de_glpi
from glpi_client import GLPIClient

ARCHIVO_GUIA = "formulario_guia.json"

# Plantas de Axion (campo "Lugar"). Hardcodeadas a propósito: cambian poquísimo.
# Editá esta lista si falta o sobra alguna.
PLANTAS = [
    "Axion 1",
    "Axion 2",
    "Axion 3",
    "Axion Neuquén",
    "Axion Buenos Aires",
    "Axion Brasil",
    "InnovAx",
    "Home Office",
    "Externo",
]

_SI = ("si", "sí", "se", "dale", "ok", "okay", "confirm", "correcto", "perfecto", "sip", "obvio")
_NO = ("no", "nop", "cambi", "corregi", "corrige", "mal")


def _sin_tildes(s):
    """Saca acentos: 'neuquén' -> 'neuquen'. Para comparar sin tropezar con tildes."""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _norm(s):
    """Minúsculas + sin tildes + sin espacios de más."""
    return " ".join(_sin_tildes(s.strip().lower()).split())


def _colapsar(s):
    """Colapsa 3+ letras repetidas a una: 'seeee' -> 'se', 'siii' -> 'si'."""
    return re.sub(r"(.)\1{2,}", r"\1", s)

# Marcadores de que el usuario se está CORRIGIENDO (no ampliando). Cuando aparecen,
# reemplazamos la descripción en vez de pegarle el texto viejo. Es heurístico:
# cubre lo común, pero una corrección sin estas palabras puede arrastrar igual.
_CORRIGE = ("mejor", "en realidad", "en verdad", "me equivoq", "equivoque", "equivoqué",
            "olvidate", "olvidá", "olvida", "nono", "no no", "cambié", "cambie",
            "en vez", "mentira", "perdón no", "perdon no")


def _es_correccion(mensaje):
    m = mensaje.strip().lower()
    return any(marca in m for marca in _CORRIGE)


def _empieza_con(mensaje, opciones):
    """True si el mensaje (limpio, sin repeticiones tipo 'siii') arranca con
    alguna de las opciones. Así 'seeee', 'daleee', 'siii' cuentan como sí."""
    m = _colapsar(mensaje.strip().lower())
    return any(m == o or m.startswith(o) for o in opciones)


def _numerar(opciones):
    return "\n".join(f"{i}. {o}" for i, o in enumerate(opciones, 1))


def _elegir_de_lista(mensaje, opciones):
    """Devuelve la opción elegida por número o por nombre, o None si no matchea.
    Tolera 'la 3', '3', 'estoy en axion 3', pero NO confunde 'axion 32' con 'Axion 3'."""
    import re
    m = mensaje.strip().lower()

    # 1) Nombre EXACTO (tolerando mayúsculas/espacios): "axion 3", "home office".
    for o in opciones:
        if m == o.lower():
            return o

    # 2) Un número que apunte a la POSICIÓN, solo si el mensaje ES ese número
    #    ("3", "la 3", "opción 3"). Así "axion 32" o "4342" NO entran por acá.
    solo_num = re.fullmatch(r"(?:la\s+|opci[oó]n\s+|nro?\.?\s*|n[°º]\s*)?(\d+)", m)
    if solo_num:
        i = int(solo_num.group(1)) - 1
        if 0 <= i < len(opciones):
            return opciones[i]

    # 3) Nombre contenido, pero por PALABRAS completas (no substring): todas las
    #    palabras del nombre tienen que estar en el mensaje. "axion 3" ⊆ "estoy en
    #    axion 3"  ✔  ;  "axion 3" ⊄ "axion 32"  ✘ (porque el token es "32", no "3").
    tokens_m = set(re.findall(r"\w+", m))
    for o in opciones:
        tokens_o = set(re.findall(r"\w+", o.lower()))
        if tokens_o and tokens_o <= tokens_m:
            return o

    # 4) Fuzzy: tolera errores de tipeo y tildes ("home ofice", "neuquen"). SOLO si
    #    el mensaje no trae números: los números ya se resolvieron EXACTO en el paso 2,
    #    así evitamos que "axion 32" se parezca a "Axion 3" por un dígito.
    if not re.search(r"\d", m):
        mn = _norm(m)
        mejor, mejor_ratio = None, 0.0
        for o in opciones:
            # comparamos contra el nombre y contra su versión sin el prefijo "axion"
            formas = {_norm(o), _norm(o).replace("axion ", "")}
            for f in formas:
                ratio = SequenceMatcher(None, mn, f).ratio()
                if ratio > mejor_ratio:
                    mejor, mejor_ratio = o, ratio
        if mejor_ratio >= 0.82:      # umbral: bastante parecido, pero no cualquier cosa
            return mejor
    return None


class Orquestador:
    def __init__(self, glpi, guia):
        self.glpi = glpi
        self.guia = guia
        self.motivos = guia["motivos_posibles"]
        self.categorias = glpi.get_categories()
        self._cache_opciones = {}
        persistencia.init_db()

    def _opciones(self, rama, motivo):
        if motivo not in self._cache_opciones:
            self._cache_opciones[motivo] = opciones_de_glpi(self.glpi, rama)
        return self._cache_opciones[motivo]

    def _preview_ticket(self, conv):
        """Muestra los 4 campos del ticket que se va a crear, para corroborar."""
        cat = next((c for c in self.categorias if c["id"] == conv.get("categoria_glpi")), None)
        categoria = cat["nombre"] if cat else "(sin categoría)"
        tipo = "Incidente" if (cat and cat["incidente"]) else "Solicitud"
        lineas = [
            "—— Ticket a crear ——",
            f"1) Lugar: {conv.get('usuario_planta') or '(no especificado)'}",
            f"2) Categoría: {categoria}  [{tipo}]",
            f"3) Título: {conv.get('titulo') or '(sin título)'}",
            f"4) Descripción: {conv.get('descripcion') or ''}",
        ]
        datos = conv.get("datos") or {}
        if datos:
            lineas.append("Datos del relevamiento:")
            for k, v in datos.items():
                lineas.append(f"  • {k}: {v}")
        return "\n".join(lineas)

    def _pregunta_lugar(self):
        return ("Para terminar, ¿en qué planta estás?\n"
                f"{_numerar(PLANTAS)}\n(Respondé con el número o el nombre.)")

    def _cerrar_o_preguntar(self, telefono, r, historial, planta):
        """Con el resultado de relevar(), arma la respuesta: o la próxima pregunta
        (si falta algo), o el cierre (pedir lugar / confirmar). Devuelve el texto
        para el usuario. Lo usan tanto el flujo normal como el cambio de motivo."""
        if not r.get("listo"):
            pregunta = r.get("siguiente_pregunta") or (
                "Perdón, no te entendí. ¿Me lo contás de otra forma?")
            historial = historial + [{"rol": "bot", "texto": pregunta}]
            persistencia.guardar(telefono, estado="esperar_descripcion",
                                 historial=historial)
            return pregunta

        resumen = r.get("resumen") or "Resumen del pedido."
        historial = historial + [{"rol": "bot", "texto": resumen}]
        if not planta:
            persistencia.guardar(telefono, estado="esperar_lugar",
                                 datos=r.get("datos", {}), historial=historial)
            return f"{resumen}\n\n{self._pregunta_lugar()}"

        persistencia.guardar(telefono, estado="esperar_confirmacion",
                             datos=r.get("datos", {}), historial=historial)
        conv = persistencia.obtener(telefono)
        return (f"{resumen}\n\n{self._preview_ticket(conv)}\n\n"
                "¿Confirmo que cargue el ticket? (sí / no)")

    def procesar(self, telefono, mensaje):
        conv = persistencia.obtener(telefono)
        if conv is None:
            estado, motivo, historial, descripcion, planta = \
                "esperar_descripcion", None, [], "", None
            categoria_glpi = None
        else:
            estado = conv["estado"]
            motivo = conv["motivo"]
            historial = conv["historial"] or []
            descripcion = conv["descripcion"] or ""
            categoria_glpi = conv["categoria_glpi"]
            planta = conv["usuario_planta"]

        # ---------- Estado: esperando el lugar (planta) ----------
        if estado == "esperar_lugar":
            elegida = _elegir_de_lista(mensaje, PLANTAS)
            if elegida is None:
                return "No reconocí esa planta. Elegí una:\n" + _numerar(PLANTAS)
            persistencia.guardar(telefono, usuario_planta=elegida,
                                 estado="esperar_confirmacion")
            conv = persistencia.obtener(telefono)
            return (f"{self._preview_ticket(conv)}\n\n"
                    "¿Confirmo que cargue el ticket? (sí / no)")

        # ---------- Estado: esperando confirmación ----------
        if estado == "esperar_confirmacion":
            if _empieza_con(mensaje, _SI):
                preview = self._preview_ticket(conv)
                persistencia.borrar(telefono)
                return ("¡Listo! Se cargaría este ticket en GLPI "
                        "(la creación real la enchufamos en el próximo paso):\n\n"
                        f"{preview}")
            if _empieza_con(mensaje, _NO):
                historial.append({"rol": "usuario", "texto": mensaje})
                persistencia.guardar(telefono, estado="esperar_descripcion",
                                     historial=historial)
                return "Dale, contame qué hay que corregir."
            return "¿Confirmo el ticket? Respondé *sí* o *no*."

        # ---------- Estado: confirmando un cambio de motivo a mitad de charla ----------
        if estado == "esperar_cambio_motivo":
            cambio = (conv.get("datos") or {}).get("_cambio") or {}

            if _empieza_con(mensaje, _SI):
                # Confirmó: arrancamos el motivo NUEVO desde cero. Descartamos lo
                # relevado del viejo y dejamos el historial limpio con el pedido nuevo.
                nuevo_motivo = cambio.get("motivo")
                nueva_desc = cambio.get("descripcion") or ""
                hist_limpio = [{"rol": "usuario", "texto": nueva_desc}]
                persistencia.guardar(
                    telefono, estado="esperar_descripcion", motivo=nuevo_motivo,
                    categoria_glpi=cambio.get("categoria_glpi"),
                    titulo=cambio.get("titulo"), descripcion=nueva_desc,
                    datos={}, historial=hist_limpio,
                )
                rama = self.guia["motivos"].get(str(nuevo_motivo),
                                                {"nombre": "", "preguntas": []})
                r = relevar(rama, hist_limpio, self._opciones(rama, nuevo_motivo))
                return "Listo, cambiamos el pedido. " + self._cerrar_o_preguntar(
                    telefono, r, hist_limpio, planta)

            if _empieza_con(mensaje, _NO):
                # Sigue con el motivo viejo: repetimos la última pregunta pendiente.
                ultima = cambio.get("ultima_pregunta") or "¿Seguimos? Contame."
                nota = f"Dale, seguimos con lo de antes. {ultima}"
                historial = historial + [{"rol": "bot", "texto": nota}]
                persistencia.guardar(telefono, estado="esperar_descripcion",
                                     datos={}, historial=historial)
                return nota

            return "¿Querés cambiar el pedido? Respondé *sí* o *no*."

        # A partir de acá siempre sumamos el mensaje del usuario al historial.
        historial.append({"rol": "usuario", "texto": mensaje})

        try:
            # ---------- Todavía sin clasificar ----------
            if motivo is None:
                primer_mensaje = not descripcion

                # Portero: en un mensaje NUEVO filtramos saludos/preguntas/fuera.
                if primer_mensaje:
                    portero = clasificar_intencion(mensaje)
                    if portero.get("intencion") != "problema":
                        respuesta = portero.get("respuesta_sugerida") or (
                            "Soy un asistente para reportar problemas de IT. "
                            "¿Qué inconveniente querés cargar?"
                        )
                        persistencia.borrar(telefono)
                        return respuesta

                # El detalle es lo que el usuario fue contando (campo 4 del ticket).
                # Si amplía, concatenamos; si se corrige ("mejor un mouse"), reemplazamos
                # para no arrastrar el pedido viejo a la descripción del ticket.
                if descripcion and not _es_correccion(mensaje):
                    descripcion = f"{descripcion}. {mensaje}".strip(". ")
                else:
                    descripcion = mensaje
                c = clasificar(descripcion, self.categorias, self.motivos)

                # Desambiguación: si el mensaje es dudoso, preguntamos UNA vez
                # (solo en el primer mensaje; con la respuesta del usuario ya
                # clasificamos para no quedar en un loop).
                indeciso = c.get("necesita_aclaracion") or (c.get("confianza") or 1) < 0.5
                if indeciso and primer_mensaje and c.get("pregunta"):
                    historial.append({"rol": "bot", "texto": c["pregunta"]})
                    persistencia.guardar(telefono, estado="esperar_descripcion",
                                         descripcion=descripcion, historial=historial)
                    return c["pregunta"]

                motivo = c.get("motivo")
                if motivo is None:
                    # Último recurso: pedimos un poco más de detalle.
                    pregunta = c.get("pregunta") or "¿Podés darme un poco más de detalle?"
                    historial.append({"rol": "bot", "texto": pregunta})
                    persistencia.guardar(telefono, estado="esperar_descripcion",
                                         descripcion=descripcion, historial=historial)
                    return pregunta

                # Clasificado: guardamos categoría (2) + título (3) + descripción (4).
                categoria_glpi = c.get("categoria_id")
                persistencia.guardar(telefono, estado="esperar_descripcion", motivo=motivo,
                                     categoria_glpi=categoria_glpi,
                                     titulo=c.get("titulo"),
                                     descripcion=descripcion, historial=historial)

            # ---------- Relevamiento de los datos del motivo ----------
            rama = self.guia["motivos"].get(str(motivo), {"nombre": "", "preguntas": []})
            r = relevar(rama, historial, self._opciones(rama, motivo))

            # ¿El usuario se fue de tema (pide algo de OTRO motivo)? Preguntamos
            # antes de descartar lo que veníamos relevando (opción "preguntar").
            if r.get("fuera_de_tema"):
                c2 = clasificar(mensaje, self.categorias, self.motivos)
                nuevo_motivo = c2.get("motivo")
                # Solo lo tomamos como cambio si sale un motivo DISTINTO y claro.
                if nuevo_motivo and nuevo_motivo != motivo:
                    nom_viejo = self.motivos.get(str(motivo), "lo anterior")
                    nom_nuevo = self.motivos.get(str(nuevo_motivo), "otra cosa")
                    ultima_preg = next(
                        (h["texto"] for h in reversed(historial) if h["rol"] == "bot"),
                        "¿Seguimos?")
                    cambio = {
                        "motivo": nuevo_motivo,
                        "categoria_glpi": c2.get("categoria_id"),
                        "titulo": c2.get("titulo"),
                        "descripcion": mensaje,
                        "ultima_pregunta": ultima_preg,
                    }
                    persistencia.guardar(telefono, estado="esperar_cambio_motivo",
                                         datos={"_cambio": cambio}, historial=historial)
                    return (f"Pará, me parece que ahora me hablás de «{nom_nuevo}» "
                            f"y veníamos con «{nom_viejo}». "
                            "¿Querés cambiar el pedido? (sí / no)")
                # Si no hay un motivo nuevo claro, seguimos normal (no cortamos).

            return self._cerrar_o_preguntar(telefono, r, historial, planta)

        except RuntimeError:
            persistencia.guardar(telefono, estado="error", historial=historial)
            return ("El asistente está sobrecargado en este momento. "
                    "Probá de nuevo en un ratito 🙏")


if __name__ == "__main__":
    with open(ARCHIVO_GUIA, encoding="utf-8") as fh:
        guia = json.load(fh)

    glpi = GLPIClient()
    try:
        glpi.init_session()
        orq = Orquestador(glpi, guia)
        telefono = "consola-test"

        print('Escribí como si fueras el usuario de WhatsApp.\n'
              '("reiniciar" para empezar una charla nueva, "salir" para terminar.)\n')
        mensaje = input("Vos: ").strip()
        while mensaje.lower() not in ("salir", "exit", "quit", ""):
            if mensaje.lower() == "reiniciar":
                persistencia.borrar(telefono)
                print("\n(Conversación reiniciada.)\n")
            else:
                respuesta = orq.procesar(telefono, mensaje)
                print(f"\nBot: {respuesta}\n")
            mensaje = input("Vos: ").strip()
    finally:
        glpi.kill_session()