# =============================================================================
#  ACTUALIZACIÓN de clasificar()  —  agrega el interruptor para el A/B testing
# =============================================================================
#  Respecto a la versión anterior cambia SOLO esto:
#    - nuevo parámetro  con_ejemplos=True
#    - el bloque de ejemplos ahora se arma solo si con_ejemplos es True
#  El orquestador la sigue llamando igual: clasificar(msg, categorias, motivos).
#  El script de medición la llama con con_ejemplos=False para el modo "sin".
#  Reemplazá tu clasificar() actual por esta.
# =============================================================================

def clasificar(mensaje, categorias, motivos, con_ejemplos=True):
    listado_cat = "\n".join(f'{c["id"]} | {c["nombre"]}' for c in categorias)
    listado_mot = "\n".join(f"{k} | {v}" for k, v in motivos.items())

    bloque_ej = ""
    if con_ejemplos:                                  # <-- interruptor del A/B
        ej = _bloque_ejemplos()
        bloque_ej = f"{ej}\n\n" if ej else ""

    prompt = (
        f"Motivos del formulario (numero | nombre):\n{listado_mot}\n\n"
        f"Categorías de GLPI (id | nombre):\n{listado_cat}\n\n"
        f"{bloque_ej}"
        f'Mensaje del usuario:\n"{mensaje.strip()}"'
    )
    data = _generar(prompt, INSTRUCCION_CLASIFICAR)
    elegida = next((c for c in categorias if c["id"] == data.get("categoria_id")), None)
    if elegida:
        data["categoria_nombre"] = elegida["nombre"]
        data["type"] = 1 if elegida["incidente"] else 2
    else:
        data["categoria_nombre"] = None
        data["type"] = None
        data["necesita_aclaracion"] = True
        data.setdefault("pregunta", "¿Podés contarme un poco más sobre el problema?")
    return data
