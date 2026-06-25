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

_SI = ("si", "sí", "dale", "ok", "okay", "confirm", "correcto", "perfecto", "sip", "obvio")
_NO = ("no", "nop", "cambi", "corregi", "corrige", "mal")


def _empieza_con(mensaje, opciones):
    """True si el mensaje (limpio) arranca con alguna de las opciones."""
    m = mensaje.strip().lower()
    return any(m == o or m.startswith(o) for o in opciones)


def _numerar(opciones):
    return "\n".join(f"{i}. {o}" for i, o in enumerate(opciones, 1))


def _elegir_de_lista(mensaje, opciones):
    """Devuelve la opción elegida por número o por nombre, o None si no matchea.
    Tolera mensajes con texto alrededor del número (ej. 'la 1', 'axiaon 1')."""
    import re
    m = mensaje.strip().lower()
    # 1) ¿hay un número que apunte a una opción? (ej. "1", "la 3", "axion 1")
    nums = re.findall(r"\d+", m)
    if len(nums) == 1:
        i = int(nums[0]) - 1
        if 0 <= i < len(opciones):
            return opciones[i]
    # 2) ¿coincide por nombre?
    for o in opciones:
        if o.lower() in m or m in o.lower():
            return o
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

        # A partir de acá siempre sumamos el mensaje del usuario al historial.
        historial.append({"rol": "usuario", "texto": mensaje})

        try:
            # ---------- Todavía sin clasificar ----------
            if motivo is None:
                # Portero: en un mensaje NUEVO filtramos saludos/preguntas/fuera.
                if not descripcion:
                    portero = clasificar_intencion(mensaje)
                    if portero.get("intencion") != "problema":
                        respuesta = portero.get("respuesta_sugerida") or (
                            "Soy un asistente para reportar problemas de IT. "
                            "¿Qué inconveniente querés cargar?"
                        )
                        persistencia.borrar(telefono)
                        return respuesta

                # El detalle es lo que el usuario fue contando (campo 4 del ticket).
                descripcion = f"{descripcion}. {mensaje}".strip(". ") if descripcion else mensaje
                c = clasificar(descripcion, self.categorias, self.motivos)
                if c.get("necesita_aclaracion"):
                    historial.append({"rol": "bot", "texto": c["pregunta"]})
                    persistencia.guardar(telefono, estado="esperar_descripcion",
                                         descripcion=descripcion, historial=historial)
                    return c["pregunta"]
                # Clasificado: guardamos categoría (2) + título (3) + descripción (4).
                motivo = c["motivo"]
                categoria_glpi = c.get("categoria_id")
                persistencia.guardar(telefono, estado="esperar_descripcion", motivo=motivo,
                                     categoria_glpi=categoria_glpi,
                                     titulo=c.get("titulo"),
                                     descripcion=descripcion, historial=historial)

            # ---------- Relevamiento de los datos del motivo ----------
            rama = self.guia["motivos"].get(str(motivo), {"nombre": "", "preguntas": []})
            r = relevar(rama, historial, self._opciones(rama, motivo))

            if not r.get("listo"):
                pregunta = r.get("siguiente_pregunta") or (
                    "Perdón, no te entendí. ¿Me lo contás de otra forma?")
                historial.append({"rol": "bot", "texto": pregunta})
                persistencia.guardar(telefono, estado="esperar_descripcion",
                                     historial=historial)
                return pregunta

            # Relevamiento completo: guardamos datos y pedimos el LUGAR (campo 1),
            # salvo que ya lo tengamos (ej. veníamos de una corrección).
            resumen = r.get("resumen") or "Resumen del pedido."
            historial.append({"rol": "bot", "texto": resumen})
            if not planta:
                persistencia.guardar(telefono, estado="esperar_lugar",
                                     datos=r.get("datos", {}), historial=historial)
                return f"{resumen}\n\n{self._pregunta_lugar()}"

            persistencia.guardar(telefono, estado="esperar_confirmacion",
                                 datos=r.get("datos", {}), historial=historial)
            conv = persistencia.obtener(telefono)
            return (f"{resumen}\n\n{self._preview_ticket(conv)}\n\n"
                    "¿Confirmo que cargue el ticket? (sí / no)")

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