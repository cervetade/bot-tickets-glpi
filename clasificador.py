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

# Modelos a probar en orden: si el primero está saturado (503) o sin cupo (429),
# se salta al siguiente. El cupo gratis es POR MODELO, así que esto suma pedidos.
# (Familia 2.5, donde temperature=0 es válida.)
MODELOS_FALLBACK = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",   # familia 3.x: cada modelo tiene su propio cupo gratis
    "gemini-3.5-flash",
]


INSTRUCCION_CLASIFICAR = """\
Sos un clasificador de tickets de soporte de IT (área "4.0"). Recibís el mensaje
de un empleado, la lista de categorías de GLPI y la lista de motivos del formulario.

Tu trabajo:
- Si el mensaje encaja CLARAMENTE en un motivo, elegí el "motivo" (número) y un
  "categoria_id" de la lista (nunca inventes), y proponé un "titulo" corto.
- Si el mensaje podría ser DOS o más motivos distintos, o si falta información para
  decidir, NO elijas a la suerte: poné "necesita_aclaracion": true, listá los
  números de los motivos candidatos en "motivos_candidatos", y en "pregunta" hacé
  UNA pregunta corta y concreta que permita distinguirlos, ofreciendo las
  alternativas en lenguaje natural.
- "confianza" es tu seguridad de 0 a 1.

Ambigüedades típicas que SIEMPRE hay que preguntar:
- FALLA de algo que ya funcionaba vs INSTALAR/configurar algo nuevo vs SOLICITAR un
  equipo nuevo vs MEJORAR un equipo que anda. Ej: "necesito la impresora de
  administración" -> ¿instalarla (motivo 1) o pedir una nueva (motivo 7)? Preguntá.
- "las cámaras andan mal" -> ¿es una falla o piden revisión/instalación? Preguntá.

Respondé SOLO con un JSON:
{"motivo": <int|null>, "categoria_id": <int|null>, "titulo": <str>,
 "confianza": <float>, "necesita_aclaracion": <bool>, "pregunta": <str|null>,
 "motivos_candidatos": <lista de int>}
"""

INSTRUCCION_RELEVAR = """\
Sos un asistente de soporte de IT que recolecta los datos de un problema
SIGUIENDO UNA GUÍA (fragmento de formulario). Te doy la guía del motivo y la
conversación hasta ahora.

Reglas:
- Extraé de la conversación TODO lo que el usuario ya dijo (aunque sea un texto
  largo). Nunca repreguntes algo ya respondido.
- Respetá las condiciones "se_muestra_si": una sub-pregunta solo aplica si su
  condición se cumple. Si no, ignorala. El campo "respuesta" puede ser un valor o
  una LISTA de valores: la pregunta aplica si la respuesta previa es ese valor o
  uno de la lista.
- Solo pedí datos OBLIGATORIOS que apliquen. Los opcionales, solo si vienen al caso.
- Si una pregunta trae "ayuda", usala para explicarle al usuario qué tiene que poner.
- Si el motivo trae "aviso_inicial", comunicáselo al usuario en tu primera pregunta.
- Si el motivo trae "avisos" y la opción elegida tiene un aviso, comunicáselo al
  usuario (es información útil, no una pregunta).
- Para preguntas de SELECCIÓN MÚLTIPLE (tipo "opciones_multiple"): listá vos las
  opciones, aclará que puede elegir VARIAS, y guardá la lista de lo elegido. Una
  condición "se_muestra_si" sobre una de estas se cumple si su valor está entre
  los seleccionados.
- Hacé UNA pregunta por turno, breve y clara.

Slots CERRADOS (los que tienen "opciones"):
- El valor DEBE ser EXACTAMENTE una de las opciones (copiala tal cual).
- Cuando preguntes por un slot cerrado, NO escribas vos la lista de opciones:
  poné en "slot_preguntado" el "texto" exacto de ese slot; el sistema le muestra
  la lista al usuario. La "siguiente_pregunta" puede ser breve (ej. "¿Cuál es?").
- El usuario puede responder con el NÚMERO o el NOMBRE de una opción; en "datos"
  devolvé SIEMPRE el nombre exacto.
- Si el usuario no sabe cuál es, poné el valor "SIN IDENTIFICAR".
- Si el problema afecta a TODOS o es general, poné el valor "FALLA GENERAL".
- NUNCA inventes una opción que no esté en la lista.

Cuando tengas todos los datos OBLIGATORIOS que aplican, poné "listo": true,
devolvé "datos" (lo recolectado) y un "resumen" de una línea para confirmar.
NO decidas urgencia ni prioridad.

Respondé SOLO con un JSON:
{"listo": <bool>, "siguiente_pregunta": <str|null>, "slot_preguntado": <str|null>,
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


def _generar(prompt, instruccion_sistema, reintentos=2):
    """
    Pide JSON a Gemini. Si un modelo está saturado (503) o sin cupo (429),
    reintenta unas veces y, si sigue fallando, prueba el siguiente modelo.
    """
    modelos, vistos = [], set()
    for m in [MODELO] + MODELOS_FALLBACK:
        if m and m not in vistos:
            vistos.add(m)
            modelos.append(m)

    ultimo = None
    for pos, modelo in enumerate(modelos):
        for i in range(reintentos):
            try:
                # Gemini 3.x recomienda NO tocar temperature; en 2.x la fijamos en 0.
                cfg = dict(
                    system_instruction=instruccion_sistema,
                    response_mime_type="application/json",
                )
                if modelo.startswith("gemini-2"):
                    cfg["temperature"] = 0
                resp = _cliente().models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=types.GenerateContentConfig(**cfg),
                )
                return json.loads(resp.text)
            except errors.ServerError as e:   # 503 saturado -> esperar y reintentar
                ultimo = e
                time.sleep(2 * (i + 1))
            except errors.ClientError as e:    # 429 sin cupo / modelo no disponible
                ultimo = e
                break  # no insistas con este modelo, pasá al siguiente
        if pos + 1 < len(modelos):
            print(f"  (Gemini: {modelo} no disponible, probando otro modelo...)")

    raise RuntimeError(
        "Gemini no respondió con ningún modelo (saturado o sin cupo). "
        "Esperá un momento y reintentá, o activá billing para subir los topes."
    ) from ultimo


INSTRUCCION_INTENCION = """\
Sos el FILTRO DE ENTRADA de un bot que SOLO crea tickets de soporte INFORMÁTICO
(área de IT / Sistemas "4.0"). Tu única tarea es clasificar el mensaje en una
intención. No respondés dudas, no mostrás datos, no buscás tickets.

El bot SOLO atiende temas de informática/sistemas. Incluye: computadoras,
notebooks, impresoras, periféricos (mouse, teclado, monitor), teléfonos IP,
software y aplicaciones, internet/red, cuentas, contraseñas, permisos, accesos,
y altas/bajas de usuarios.

NO atiende (es "fuera"): mantenimiento edilicio, máquinas de producción,
herramientas, escaleras, vehículos, muebles, electricidad, RR.HH. no informático,
ni nada que no sea de informática/sistemas.

Intenciones:
- "saludo"   : saludos, agradecimientos o charla sin un problema concreto.
- "problema" : un inconveniente o pedido CLARAMENTE informático, cargable como
               ticket (ej. "no anda la impresora", "necesito Office", "no tengo
               internet", "reseteo de contraseña", "permisos a una carpeta").
- "pregunta" : una duda sobre el sistema o sobre cómo usar el bot.
- "fuera"    : un pedido que NO es de informática (ej. "cambiar las ruedas de una
               escalera", "se rompió una máquina de producción", "quiero sillas").

REGLA CLAVE: marcá "problema" SOLO si es claramente de informática/sistemas. Si
es de otra área, o no estás seguro de que sea de IT, usá "fuera".

Respondé SOLO con un JSON:
{"intencion": "saludo|problema|pregunta|fuera", "respuesta_sugerida": <str>}
"respuesta_sugerida" es una frase corta y amable, SOLO si NO es "problema". Para
"fuera", aclará con buena onda que el bot es solo para soporte informático y que
ese pedido debe ir por otro canal.
"""


def clasificar_intencion(mensaje):
    """
    El "portero": decide si el mensaje es un saludo, un problema reportable, una
    pregunta sobre el sistema, o algo fuera de alcance. Devuelve un dict:
        {"intencion": "saludo|problema|pregunta|fuera", "respuesta_sugerida": str}
    """
    return _generar(f'Mensaje del usuario:\n"{mensaje.strip()}"', INSTRUCCION_INTENCION)


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
        if opciones and q.get("tipo") != "opciones_multiple":
            cerrados[q["texto"]] = {o.strip().casefold(): o for o in opciones}
        preguntas.append(q)
    rama2 = dict(rama)
    rama2["preguntas"] = preguntas
    return rama2, cerrados


def _opciones_numeradas(opciones):
    """Lista numerada de opciones para mostrarle al usuario."""
    return "\n".join(f"{i}. {o}" for i, o in enumerate(opciones, 1))


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
    datos = r.get("datos") or {}
    especiales = {"sin identificar", "falla general"}

    # --- Normalizar y validar los slots cerrados que haya en 'datos' ---
    rechazo = None
    for slot, permitido in cerrados.items():
        val = datos.get(slot)
        if val is None:
            continue
        clave = str(val).strip().casefold()
        if clave in especiales:
            continue
        ordenadas = list(permitido.values())
        # si respondió con un número, lo mapeamos a la opción correspondiente
        if str(val).strip().isdigit():
            idx = int(str(val).strip()) - 1
            if 0 <= idx < len(ordenadas):
                datos[slot] = ordenadas[idx]
                continue
        canon = permitido.get(clave)
        if canon is None:
            datos.pop(slot, None)          # valor inventado -> lo descartamos
            rechazo = (slot, val)
        else:
            datos[slot] = canon            # guardamos el nombre exacto de GLPI

    if rechazo:
        slot, val = rechazo
        r["listo"] = False
        r["slot_preguntado"] = slot
        r["siguiente_pregunta"] = f'No encontré "{val}" en la lista. Elegí una de estas:'

    # --- Si estamos preguntando por un slot cerrado, mostramos las opciones reales ---
    if not r.get("listo"):
        slot = r.get("slot_preguntado")
        if slot not in cerrados:
            # fallback: detectar un slot cerrado nombrado en la pregunta
            sp = (r.get("siguiente_pregunta") or "").casefold()
            slot = next((s for s in cerrados if s.casefold() in sp), None)
        if slot in cerrados:
            base = r.get("siguiente_pregunta") or f"{slot}:"
            r["siguiente_pregunta"] = (
                f"{base}\n{_opciones_numeradas(list(cerrados[slot].values()))}\n"
                '(Respondé con el número o el nombre. Si no sabés, escribí "no sé".)'
            )
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