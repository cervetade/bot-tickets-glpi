"""
orquestador.py — Módulo "Orquestador" del bot (el director de orquesta).

Recibe (telefono, mensaje) y devuelve la respuesta del bot. Por cada mensaje:
  1. Lee de persistencia en qué estado venía ese teléfono (o arranca de cero).
  2. Según el estado, decide qué hacer.
  3. Llama a la pieza que corresponda (clasificador / relevamiento).
  4. Guarda el nuevo estado en persistencia.
  5. Devuelve el texto para el usuario.

NO sabe de WhatsApp: por eso se prueba por consola igual que andará en producción
(cada input simula un mensaje entrante del mismo número). El día de mañana, el
webhook de WhatsApp solo tiene que llamar a procesar() y mandar lo que devuelve.

Esta versión cubre el NÚCLEO conversacional: clasificar -> relevar -> confirmar.
(Sin identificación de usuario ni creación del ticket todavía: próximos pasos.)

Probarlo por consola:  python orquestador.py
"""

import json

import persistencia
from clasificador import clasificar, clasificar_intencion, relevar, opciones_de_glpi
from glpi_client import GLPIClient

ARCHIVO_GUIA = "formulario_guia.json"

# Palabras que cuentan como "sí" / "no" en la confirmación.
_SI = ("si", "sí", "dale", "ok", "okay", "confirm", "correcto", "perfecto", "sip", "obvio")
_NO = ("no", "nop", "cambi", "corregi", "corrige", "mal")


def _empieza_con(mensaje, opciones):
    """True si el mensaje (limpio) arranca con alguna de las opciones.
    Tolera 'sii', 'siii', 'dale total', etc."""
    m = mensaje.strip().lower()
    return any(m == o or m.startswith(o) for o in opciones)


class Orquestador:
    def __init__(self, glpi, guia):
        self.glpi = glpi
        self.guia = guia
        self.motivos = guia["motivos_posibles"]
        self.categorias = glpi.get_categories()   # se cachean una vez
        self._cache_opciones = {}                  # opciones de GLPI por motivo
        persistencia.init_db()

    def _opciones(self, rama, motivo):
        """Trae (y cachea) las listas de GLPI para los slots cerrados del motivo."""
        if motivo not in self._cache_opciones:
            self._cache_opciones[motivo] = opciones_de_glpi(self.glpi, rama)
        return self._cache_opciones[motivo]

    def _preview_ticket(self, categoria_id, datos):
        """Resumen legible del ticket que se va a crear (para corroborar)."""
        cat = next((c for c in self.categorias if c["id"] == categoria_id), None)
        nombre = cat["nombre"] if cat else "(sin categoría)"
        tipo = "Incidente" if (cat and cat["incidente"]) else "Solicitud"
        lineas = [
            "—— Ticket a crear ——",
            f"Categoría: {nombre}",
            f"Tipo: {tipo}",
        ]
        if datos:
            lineas.append("Datos:")
            for k, v in datos.items():
                lineas.append(f"  • {k}: {v}")
        return "\n".join(lineas)

    def procesar(self, telefono, mensaje):
        conv = persistencia.obtener(telefono)
        if conv is None:
            estado, motivo, historial, descripcion = "esperar_descripcion", None, [], ""
            categoria_glpi = None
        else:
            estado = conv["estado"]
            motivo = conv["motivo"]
            historial = conv["historial"] or []
            descripcion = conv["descripcion"] or ""
            categoria_glpi = conv["categoria_glpi"]

        # ---------- Estado: esperando confirmación del resumen ----------
        if estado == "esperar_confirmacion":
            if _empieza_con(mensaje, _SI):
                preview = self._preview_ticket(conv["categoria_glpi"], conv["datos"])
                persistencia.borrar(telefono)  # núcleo: cerramos la charla acá
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
            # ---------- Todavía sin clasificar (o pidiendo aclaración) ----------
            if motivo is None:
                # Portero: en un mensaje NUEVO (sin descripción previa), filtramos
                # saludos, preguntas y cosas fuera de alcance antes de armar ticket.
                if not descripcion:
                    portero = clasificar_intencion(mensaje)
                    if portero.get("intencion") != "problema":
                        respuesta = portero.get("respuesta_sugerida") or (
                            "Soy un asistente para reportar problemas de IT. "
                            "¿Tenés algún inconveniente que quieras cargar?"
                        )
                        persistencia.borrar(telefono)  # no abrimos conversación
                        return respuesta

                descripcion = f"{descripcion}. {mensaje}".strip(". ") if descripcion else mensaje
                c = clasificar(descripcion, self.categorias, self.motivos)
                if c.get("necesita_aclaracion"):
                    historial.append({"rol": "bot", "texto": c["pregunta"]})
                    persistencia.guardar(telefono, estado="esperar_descripcion",
                                         descripcion=descripcion, historial=historial)
                    return c["pregunta"]
                # Quedó clasificado: guardamos motivo + categoría y seguimos a relevar.
                motivo = c["motivo"]
                categoria_glpi = c.get("categoria_id")
                persistencia.guardar(telefono, estado="esperar_descripcion", motivo=motivo,
                                     categoria_glpi=categoria_glpi,
                                     descripcion=descripcion, historial=historial)

            # ---------- Relevamiento: completar los slots del motivo ----------
            rama = self.guia["motivos"].get(str(motivo), {"nombre": "", "preguntas": []})
            r = relevar(rama, historial, self._opciones(rama, motivo))

            if r.get("listo"):
                resumen = r.get("resumen") or "Resumen del pedido."
                preview = self._preview_ticket(categoria_glpi, r.get("datos", {}))
                historial.append({"rol": "bot", "texto": resumen})
                persistencia.guardar(telefono, estado="esperar_confirmacion",
                                     datos=r.get("datos", {}), historial=historial)
                return f"{resumen}\n\n{preview}\n\n¿Confirmo que cargue el ticket? (sí / no)"

            pregunta = r["siguiente_pregunta"]
            historial.append({"rol": "bot", "texto": pregunta})
            persistencia.guardar(telefono, estado="esperar_descripcion", historial=historial)
            return pregunta

        except RuntimeError:
            # La IA no respondió (sin cupo / saturada). Estado "error" (RNF Confiabilidad).
            persistencia.guardar(telefono, estado="error", historial=historial)
            return ("El asistente está sobrecargado en este momento. "
                    "Probá de nuevo en un ratito 🙏")


if __name__ == "__main__":
    # Simula WhatsApp por consola: cada línea que escribís es un "mensaje entrante"
    # del mismo número. El estado se guarda y se lee de SQLite en cada vuelta, igual
    # que pasará en producción.
    with open(ARCHIVO_GUIA, encoding="utf-8") as fh:
        guia = json.load(fh)

    glpi = GLPIClient()
    try:
        glpi.init_session()
        orq = Orquestador(glpi, guia)
        telefono = "consola-test"  # un único "número" para toda la sesión de prueba

        print("Escribí como si fueras el usuario de WhatsApp (o 'salir').\n")
        mensaje = input("Vos: ").strip()
        while mensaje.lower() not in ("salir", "exit", "quit", ""):
            respuesta = orq.procesar(telefono, mensaje)
            print(f"\nBot: {respuesta}\n")
            mensaje = input("Vos: ").strip()
    finally:
        glpi.kill_session()