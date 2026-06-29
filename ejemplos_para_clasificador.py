# =============================================================================
#  PARCHE PARA clasificador.py  —  Few-shot: ejemplos reales en el prompt
# =============================================================================
#  Qué hace: antes de pedirle a Gemini que clasifique, le mostramos un puñado de
#  tickets reales ya cargados (mensaje del usuario -> categoría que le tocó).
#  Es como entrenar a un empleado nuevo con casos viejos: aprende el lenguaje
#  real de la gente y el patrón, sin tocar el modelo ni reentrenar nada.
#
#  Son 3 piezas. Copialas a clasificador.py donde se indica.
# =============================================================================


# Estos 3 imports YA están en tu clasificador.py. Acá van solo para poder correr
# este archivo suelto (PIEZA 3). Cuando copies a clasificador.py, no los dupliques.
import os
import json
from functools import lru_cache


# -----------------------------------------------------------------------------
# PIEZA 1 — Loader. Va arriba, cerca de donde definís MODELO / constantes.
#           Necesita: import os, json, y from functools import lru_cache
#           (los tres ya los tenés importados).
# -----------------------------------------------------------------------------

# El JSON va en la MISMA carpeta que clasificador.py (igual que formulario_guia.json).
RUTA_EJEMPLOS = os.path.join(os.path.dirname(__file__), "ejemplos_clasificacion.json")

# Cuántos ejemplos como mucho meter en el prompt. Subir/bajar según cuánto te
# pese en tokens (en free tier cada token cuenta). Con None entran todos.
MAX_EJEMPLOS = None


@lru_cache(maxsize=1)
def cargar_ejemplos():
    """
    Lee ejemplos_clasificacion.json una sola vez (queda cacheado).
    Cada item: {"texto": <lo que escribió el usuario>, "categoria": <nombre GLPI>}.
    Si el archivo no está, devolvemos lista vacía y el bot sigue andando igual
    (sin ejemplos, como antes). Así nunca se rompe por un archivo faltante.
    """
    try:
        with open(RUTA_EJEMPLOS, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _bloque_ejemplos():
    """
    Arma el texto de ejemplos que se inyecta en el prompt de clasificar().
    Guardamos la categoría por NOMBRE (no por id): el id lo trae GLPI vivo y
    puede cambiar; el nombre no. El modelo, que en el mismo prompt tiene la
    lista 'id | nombre', mapea solo el nombre del ejemplo al id correcto.
    """
    ejemplos = cargar_ejemplos()
    if MAX_EJEMPLOS:
        ejemplos = ejemplos[:MAX_EJEMPLOS]
    if not ejemplos:
        return ""  # sin ejemplos -> el prompt queda como estaba

    lineas = [
        "Ejemplos reales de tickets ya cargados por empleados "
        "(mensaje del usuario -> categoría que le correspondió). "
        "Usalos como referencia del lenguaje real y del patrón, pero elegí "
        "SIEMPRE el categoria_id de la lista de categorías de arriba:",
        "",
    ]
    for e in ejemplos:
        texto = " ".join(e["texto"].split())  # por las dudas, sin saltos raros
        lineas.append(f'- "{texto}" -> {e["categoria"]}')
    return "\n".join(lineas)


# -----------------------------------------------------------------------------
# PIEZA 2 — clasificar() con el bloque de ejemplos inyectado.
#           Reemplazá tu clasificar() actual por esta. El ÚNICO cambio real son
#           las 2 líneas marcadas con  # <-- NUEVO  (el resto es idéntico).
# -----------------------------------------------------------------------------

def clasificar(mensaje, categorias, motivos):
    listado_cat = "\n".join(f'{c["id"]} | {c["nombre"]}' for c in categorias)
    listado_mot = "\n".join(f"{k} | {v}" for k, v in motivos.items())

    ejemplos = _bloque_ejemplos()                                  # <-- NUEVO
    bloque_ej = f"{ejemplos}\n\n" if ejemplos else ""              # <-- NUEVO

    prompt = (
        f"Motivos del formulario (numero | nombre):\n{listado_mot}\n\n"
        f"Categorías de GLPI (id | nombre):\n{listado_cat}\n\n"
        f"{bloque_ej}"                                             # <-- NUEVO
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


# -----------------------------------------------------------------------------
# PIEZA 3 — (opcional) Prueba rápida sin tocar GLPI ni Gemini, para ver que el
#           bloque se arma bien. Corré:  python ejemplos_para_clasificador.py
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Ejemplos cargados: {len(cargar_ejemplos())}\n")
    print("Así se ve el bloque que se mete en el prompt (primeras 12 líneas):\n")
    print("\n".join(_bloque_ejemplos().splitlines()[:12]))
