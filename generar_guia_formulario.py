"""
generar_guia_formulario.py

Convierte el export CRUDO del formulario de GLPI (formularioSistemas4.0.json)
en una guía LIMPIA y COMPLETA que el bot le pasa a Gemini como fuente de verdad
de la conversación.

NO recorta preguntas: incluye las 12 ramas (motivos), todas sus preguntas, las
condiciones (qué pregunta aparece según una respuesta previa) y marca dónde las
opciones salen de una lista de GLPI (que el bot trae en vivo).

Lo que saca es el RUIDO del export: UUIDs, rangos, estrategias internas, etc.

Si el área cambia el formulario en GLPI, volvés a exportar y corrés este script:
la guía se regenera, no se edita a mano.

Uso:
    python generar_guia_formulario.py formularioSistemas4.0.json
"""

import json
import sys

# Cómo nombramos cada tipo de pregunta de GLPI en la guía (más legible para la IA).
TIPOS = {
    "QuestionTypeRadio": "opciones",
    "QuestionTypeDropdown": "opciones",
    "QuestionTypeCheckbox": "opciones_multiple",
    "QuestionTypeShortText": "texto",
    "QuestionTypeLongText": "texto_largo",
    "QuestionTypeNumber": "numero",
    "QuestionTypeDateTime": "fecha",
    "QuestionTypeFile": "archivo",
    "QuestionTypeItemDropdown": "lista_glpi",
    "QuestionTypeItem": "lista_glpi",
}


def _tipo_corto(t):
    return t.split("\\")[-1]


def _opciones(extra):
    """Devuelve la lista de etiquetas de opción (Radio/Dropdown/Checkbox)."""
    opts = (extra or {}).get("options") or {}
    if isinstance(opts, dict):
        return list(opts.values())
    if isinstance(opts, list):
        return [o.get("label", o) if isinstance(o, dict) else o for o in opts]
    return []


def construir_guia(form):
    qs = form["questions"]
    secciones = {s["id"]: s for s in form["sections"]}
    por_uuid = {q["uuid"]: q for q in qs}

    raiz = next(q for q in qs if q.get("name", "").startswith("¿Motivo"))
    motivos_labels = (raiz.get("extra_data") or {}).get("options", {})  # {"1": "...", ...}

    def condicion_seccion(sec):
        """Devuelve el número de motivo que dispara la sección (o None)."""
        for c in sec.get("conditions") or []:
            if por_uuid.get(c.get("item_uuid")) is raiz:
                return str(c.get("value"))
        return None

    def _label(opts, value):
        """Resuelve el valor de una condición a su etiqueta. Maneja listas
        (checkbox) y el caso de índices corridos (clave exacta, luego +1)."""
        def uno(v):
            v = str(v)
            if v in opts:
                return opts[v]
            try:
                return opts.get(str(int(v) + 1), v)  # algunas condiciones van 0-based
            except (ValueError, TypeError):
                return v
        if isinstance(value, list):
            return " / ".join(uno(x) for x in value)
        return uno(value)

    def se_muestra_si(q):
        """Condición legible: '<pregunta previa> = <respuesta>' (sin contar el motivo)."""
        for c in q.get("conditions") or []:
            disparador = por_uuid.get(c.get("item_uuid"))
            if disparador is None or disparador is raiz:
                continue  # el motivo ya lo da la sección
            opts = (disparador.get("extra_data") or {}).get("options") or {}
            respuesta = _label(opts, c.get("value")) if opts else "(una vez respondida)"
            return {
                "pregunta": disparador.get("name"),
                "respuesta": respuesta,
            }
        return None

    def limpiar_pregunta(q):
        tc = _tipo_corto(q["type"])
        extra = q.get("extra_data") or {}
        out = {
            "texto": q.get("name"),
            "obligatoria": bool(q.get("is_mandatory")),
            "tipo": TIPOS.get(tc, "texto"),
        }
        if out["tipo"] in ("opciones", "opciones_multiple"):
            out["opciones"] = _opciones(extra)
        if out["tipo"] == "lista_glpi":
            out["fuente_glpi"] = extra.get("itemtype")  # ej. Printer, Location, User
        cond = se_muestra_si(q)
        if cond:
            out["se_muestra_si"] = cond
        return out

    # Agrupamos preguntas por motivo (vía la sección a la que pertenecen).
    motivos = {}
    siempre = []  # preguntas que no dependen de ningún motivo (ej. ubicación)
    for q in sorted(qs, key=lambda x: (x.get("section_id") or 0, x.get("vertical_rank") or 0)):
        if q is raiz:
            continue
        sec = secciones.get(q.get("section_id"))
        motivo = condicion_seccion(sec) if sec else None
        if motivo:
            motivos.setdefault(motivo, {
                "nombre": motivos_labels.get(motivo, sec["name"]),
                "preguntas": [],
            })["preguntas"].append(limpiar_pregunta(q))
        else:
            siempre.append(limpiar_pregunta(q))

    # Aseguramos que estén las 12 ramas, aunque alguna no tenga preguntas extra.
    for num, nombre in motivos_labels.items():
        if num not in motivos:
            motivos[num] = {
                "nombre": nombre,
                "preguntas": [],
                "sin_preguntas_extra": True,
            }

    return {
        "pregunta_inicial": raiz.get("name"),
        "motivos_posibles": motivos_labels,
        "preguntas_siempre": siempre,
        "motivos": {k: motivos[k] for k in sorted(motivos, key=int)},
    }


def render_legible(guia):
    """Versión en texto para revisar a ojo."""
    lineas = [f'PREGUNTA INICIAL: {guia["pregunta_inicial"]}', ""]
    if guia["preguntas_siempre"]:
        lineas.append("SIEMPRE SE PREGUNTA:")
        for q in guia["preguntas_siempre"]:
            lineas.append(_render_q(q, "  "))
        lineas.append("")
    for num, m in guia["motivos"].items():
        lineas.append(f'■ MOTIVO {num}: {m["nombre"]}')
        for q in m["preguntas"]:
            lineas.append(_render_q(q, "   "))
        lineas.append("")
    return "\n".join(lineas)


def _render_q(q, pad):
    extra = ""
    if q.get("opciones"):
        extra = f'  [{" / ".join(map(str, q["opciones"]))}]'
    elif q.get("fuente_glpi"):
        extra = f'  (se elige de una lista de GLPI: {q["fuente_glpi"]})'
    cond = ""
    if q.get("se_muestra_si"):
        c = q["se_muestra_si"]
        cond = f'   ↳ solo si "{c["pregunta"]}" = "{c["respuesta"]}"'
    obl = "*" if q["obligatoria"] else " "
    return f'{pad}{obl} {q["texto"]}{extra}{cond}'


if __name__ == "__main__":
    ruta = sys.argv[1] if len(sys.argv) > 1 else "formularioSistemas4.0.json"
    with open(ruta, encoding="utf-8") as fh:
        data = json.load(fh)
    form = data["forms"][0]

    guia = construir_guia(form)

    with open("formulario_guia.json", "w", encoding="utf-8") as fh:
        json.dump(guia, fh, ensure_ascii=False, indent=2)

    print(render_legible(guia))
    print(f"\nGuía guardada en formulario_guia.json "
          f"({len(guia['motivos'])} motivos).")
