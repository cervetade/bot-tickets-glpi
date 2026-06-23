"""
clasificador.py — Módulo "Clasificador IA" del bot (comprensión + relevamiento).

clasificar(mensaje, categorias, motivos)  -> turno 1: motivo + categoría GLPI.
relevar(rama, historial, opciones_glpi)   -> turnos siguientes: completa slots.

REGLA CLAVE (integridad de los datos):
- Slots ABIERTOS (texto libre): Gemini interpreta lo que diga el usuario.
- Slots CERRADOS (vienen de una lista: 'opciones' del form, o 'fuente_glpi' de
  GLPI): el valor DEBE ser uno de la lista real. El bot trae la lista de GLPI,
  se la pasa a Gemini, y además VALIDA la respuesta. Si no matchea, repregunta
  mostrando las opciones. Si el usuario no sabe -> "SIN IDENTIFICAR". Si dice que
  afecta a todos -> "FALLA GENERAL". Nunca se guarda un valor inventado.

Gemini junta HECHOS; no decide urgencia ni prioridad (eso lo hace GLPI).

Banco de pruebas (conversación completa por consola):  python clasificador.py
Requiere formulario_guia.json en la misma carpeta y las vars de GLPI/GEMINI.
"""

import os
import json
import time
from functools import lru_cache

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai import errors

load_dotenv()

MODELO = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


INSTRUCCION_CLASIFICAR = """\
Sos un clasificador de tickets de soporte de IT (área "4.0").
Recibís el mensaje de un empleado, la lista de categorías de GLPI y la lista de
motivos del formulario. Elegí el "motivo" (número) y un "categoria_id" de la
lista (nunca inventes), proponé un "titulo" corto, y si no alcanza para decidir
poné "necesita_aclaracion": true con una "pregunta".

Respondé SOLO con un JSON:
{"motivo": <int|null>, "categoria_id": <int|null>, "titulo": <str>,
 "confianza": <float>, "necesita_aclaracion": <bool>, "pregunta": <str|null>}
"""

INSTRUCCION_RELEVAR = """\
Sos un asistente de soporte de IT que recolecta los datos de un problema
SIGUIENDO UNA GUÍA (fragmento de formulario). Te doy la guía del motivo y la
conversación hasta ahora.

Reglas:
- Extraé de la conversación TODO lo que el usuario ya dijo (aunque sea un texto
  largo). Nunca repreguntes algo ya respondido.
- Respetá las condiciones "se_muestra_si": una sub-pregunta solo aplica si su
  condición se cumple. Si no, ignorala.
- Hacé UNA pregunta por turno, breve y clara.

Slots CERRADOS (los que tienen "opciones"):
- El valor DEBE ser EXACTAMENTE una de las opciones (copiala tal cual).
- Al preguntar, ofrecé las opciones.
- Si el usuario no sabe cuál es, mostrale las opciones; si aun así no sabe, poné
  el valor "SIN IDENTIFICAR".
- Si el usuario dice que el problema afecta a TODOS o es general, no elijas un
  item: poné el valor "FALLA GENERAL".
- NUNCA inventes una opción que no esté en la lista.

Cuando tengas todos los datos OBLIGATORIOS que aplican, poné "listo": true,
devolvé "datos" (lo recolectado) y un "resumen" de una línea para confirmar.
NO decidas urgencia ni prioridad.

Respondé SOLO con un JSON:
{"listo": <bool>, "siguiente_pregunta": <str|null>,
 "datos": <object>, "resumen": <str|null>}
"""


@lru_cache(maxsize=1)
def _cliente():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Falta GEMINI_API_KEY en el .env. "
            "Sacá una gratis en https://aistudio.google.com (Get API key)."
        )
    return genai.Client(api_key=api_key)


def _generar(prompt, instruccion_sistema, intentos=4):
    """Llama a Gemini pidiendo JSON, con reintentos ante errores transitorios."""
    for i in range(intentos):
        try:
            resp = _cliente().models.generate_content(
                model=MODELO,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=instruccion_sistema,
                    temperature=0,  # determinista. (Para Gemini 3.x: borrá esta línea.)
                    response_mime_type="application/json",
                ),
            )
            return json.loads(resp.text)
        except errors.ServerError:
            if i == intentos - 1:
                raise
            time.sleep(2 * (i + 1))


def clasificar(mensaje, categorias, motivos):
    listado_cat = "\n".join(f'{c["id"]} | {c["nombre"]}' for c in categorias)
    listado_mot = "\n".join(f"{k} | {v}" for k, v in motivos.items())
    prompt = (
        f"Motivos del formulario (numero | nombre):\n{listado_mot}\n\n"
        f"Categorías de GLPI (id | nombre):\n{listado_cat}\n\n"
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


def _preparar_rama(rama, opciones_glpi):
    """
    Inyecta en cada slot 'fuente_glpi' las opciones reales traídas de GLPI y
    arma el set de valores permitidos por slot cerrado (para validar después).
    Devuelve (rama_para_ia, cerrados) con cerrados = {slot: {casefold: nombre}}.
    """
    cerrados = {}
    preguntas = []
    for q in rama.get("preguntas", []):
        q = dict(q)
        opciones = list(q["opciones"]) if q.get("opciones") else None
        if not opciones and q.get("fuente_glpi"):
            lista = (opciones_glpi or {}).get(q["texto"])
            if lista:
                opciones = list(lista)
                q["opciones"] = opciones
                q.pop("fuente_glpi", None)
            # si no hay lista (vacía o demasiado grande) queda como texto libre
        if opciones:
            cerrados[q["texto"]] = {o.strip().casefold(): o for o in opciones}
        preguntas.append(q)
    rama2 = dict(rama)
    rama2["preguntas"] = preguntas
    return rama2, cerrados


def relevar(rama, historial, opciones_glpi=None):
    """
    rama          : fragmento de formulario_guia.json del motivo detectado.
    historial     : [{"rol": "usuario"|"bot", "texto": "..."}].
    opciones_glpi : {slot_texto: [opciones reales]} pre-traídas de GLPI por el
                    orquestador para los slots 'fuente_glpi'.
    """
    rama_ia, cerrados = _preparar_rama(rama, opciones_glpi)
    conversacion = "\n".join(f'{t["rol"]}: {t["texto"]}' for t in historial)
    prompt = (
        f"GUÍA del motivo (JSON):\n{json.dumps(rama_ia, ensure_ascii=False)}\n\n"
        f"CONVERSACIÓN hasta ahora:\n{conversacion}"
    )
    r = _generar(prompt, INSTRUCCION_RELEVAR)

    # --- Validación determinista de los slots cerrados ---
    especiales = {"sin identificar", "falla general"}
    if r.get("listo") and cerrados:
        datos = r.get("datos") or {}
        for slot, permitido in cerrados.items():
            val = datos.get(slot)
            if val is None:
                continue
            clave = str(val).strip().casefold()
            if clave in especiales:
                continue
            canon = permitido.get(clave)
            if canon is None:
                # La IA devolvió algo que NO está en la lista real -> rechazamos.
                muestra = ", ".join(list(permitido.values())[:12])
                r["listo"] = False
                r["siguiente_pregunta"] = (
                    f'Para "{slot}" tenés que elegir una de la lista. '
                    f'Opciones: {muestra}. Si no sabés cuál es, escribí "no sé".'
                )
                datos.pop(slot, None)
                break
            datos[slot] = canon  # guardamos el nombre exacto de GLPI
    return r


def opciones_de_glpi(glpi, rama, cap=60):
    """
    Pre-trae de GLPI las opciones de los slots 'fuente_glpi' de la rama.
    Listas chicas (impresoras, plantas) se ofrecen completas; las muy grandes
    (ej. usuarios) se dejan para búsqueda dirigida (texto libre) más adelante.
    """
    op = {}
    for q in rama.get("preguntas", []):
        itemtype = q.get("fuente_glpi")
        if not itemtype:
            continue
        try:
            items = glpi.listar_items(itemtype, limite=cap + 1)
        except Exception:
            continue
        if 0 < len(items) <= cap:
            op[q["texto"]] = [i["nombre"] for i in items]
    return op


if __name__ == "__main__":
    from glpi_client import GLPIClient

    with open("formulario_guia.json", encoding="utf-8") as fh:
        guia = json.load(fh)
    motivos = guia["motivos_posibles"]

    glpi = GLPIClient()
    try:
        glpi.init_session()
        categorias = glpi.get_categories()
        print(f"{len(categorias)} categorías cargadas. Modelo: {MODELO}\n")

        mensaje = input('Contá tu problema (o "salir"): ').strip()
        while mensaje.lower() not in ("salir", "exit", "quit", ""):
            c = clasificar(mensaje, categorias, motivos)
            if c.get("necesita_aclaracion"):
                print(f'\n  Bot: {c["pregunta"]}')
                mensaje = f'{mensaje}. {input("  Vos: ").strip()}'
                continue

            rama = guia["motivos"].get(str(c["motivo"]), {"nombre": "", "preguntas": []})
            print(f'\n  [motivo {c["motivo"]}: {rama.get("nombre")} | '
                  f'categoría: {c["categoria_nombre"]} | tipo: {c["type"]}]')

            # El orquestador (acá, la consola) pre-trae las listas reales de GLPI.
            opciones_glpi = opciones_de_glpi(glpi, rama)

            historial = [{"rol": "usuario", "texto": mensaje}]
            while True:
                r = relevar(rama, historial, opciones_glpi)
                if r.get("listo"):
                    print(f'\n  Bot: {r.get("resumen")}')
                    print("  Datos:", json.dumps(r.get("datos", {}), ensure_ascii=False))
                    break
                print(f'\n  Bot: {r["siguiente_pregunta"]}')
                resp = input("  Vos: ").strip()
                historial.append({"rol": "bot", "texto": r["siguiente_pregunta"]})
                historial.append({"rol": "usuario", "texto": resp})

            print()
            mensaje = input('Otro problema (o "salir"): ').strip()
    finally:
        glpi.kill_session()