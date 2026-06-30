"""
listar_modelos.py — Te dice los nombres EXACTOS de modelos que tu cuenta puede usar.

Sirve para no adivinar el string del modelo: copiá los nombres de acá tal cual
a tu MODELO / MODELOS_FALLBACK en clasificador.py.

Corré:  python listar_modelos.py
(Usa tu GEMINI_API_KEY del .env, igual que el bot.)
"""

import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

print("Modelos disponibles para tu cuenta (que sirven para generar texto):\n")
for m in client.models.list():
    # nos quedamos solo con los que soportan generateContent (clasificar/relevar)
    acciones = getattr(m, "supported_actions", None) or []
    if not acciones or "generateContent" in acciones:
        # m.name viene como "models/gemini-3.1-flash-lite-preview"; sacamos el prefijo
        print("  ", m.name.replace("models/", ""))
