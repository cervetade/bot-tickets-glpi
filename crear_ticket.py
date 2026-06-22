"""
crear_ticket.py — POST de prueba a la API de GLPI (CREA un ticket).

⚠️ A diferencia de glpi_client.py y explorar_glpi.py (que solo LEEN), este
script SÍ crea datos en GLPI. Crea un ticket de prueba en la categoría
'Prueba > PruebaBot-Impresoras' (id 372), que no se asigna a nadie.

Después de probar, ENTRÁ A GLPI Y BORRÁ/CERRÁ el ticket de prueba.

Uso:
    python crear_ticket.py

.env: GLPI_URL, GLPI_APP_TOKEN, GLPI_USER_TOKEN (+ GLPI_PROFILE_ID opcional)
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GLPI_URL = os.environ["GLPI_URL"].rstrip("/")
GLPI_APP_TOKEN = os.environ["GLPI_APP_TOKEN"]
GLPI_USER_TOKEN = os.environ["GLPI_USER_TOKEN"]
GLPI_PROFILE_ID = os.environ.get("GLPI_PROFILE_ID")

# --- Datos del ticket de prueba -------------------------------------------
TICKET = {
    "name": "[PRUEBA BOT - IGNORAR] ticket de prueba via API",
    "content": "Ticket de prueba creado por la API REST para validar el POST. Se puede ignorar / borrar.",
    "itilcategories_id": 372,   # Prueba > PruebaBot-Impresoras (categoria de prueba, no asigna a nadie)
    "type": 1,                  # 1 = incidente, 2 = solicitud
}
# --------------------------------------------------------------------------


def main():
    http = httpx.Client(timeout=60)

    # Abrir sesión
    r = http.get(
        f"{GLPI_URL}/initSession",
        headers={"Authorization": f"user_token {GLPI_USER_TOKEN}", "App-Token": GLPI_APP_TOKEN},
    )
    r.raise_for_status()
    token = r.json()["session_token"]
    headers = {"Content-Type": "application/json", "Session-Token": token, "App-Token": GLPI_APP_TOKEN}

    # Cambiar al perfil con permisos, si está configurado
    if GLPI_PROFILE_ID:
        http.post(
            f"{GLPI_URL}/changeActiveProfile",
            headers=headers,
            json={"profiles_id": int(GLPI_PROFILE_ID)},
        ).raise_for_status()

    try:
        print("Creando ticket de prueba en 'Prueba > PruebaBot-Impresoras' (id 372)...\n")
        r = http.post(f"{GLPI_URL}/Ticket", headers=headers, json={"input": TICKET})
        r.raise_for_status()
        data = r.json()
        ticket_id = data.get("id")
        print("OK -> ticket creado. Respuesta de GLPI:")
        print(data)
        web = GLPI_URL.replace("/apirest.php", "")
        print(f"\nVelo en GLPI: {web}/front/ticket.form.php?id={ticket_id}")
        print("Acordate de borrarlo/cerrarlo cuando termines de mirarlo.")
    except httpx.HTTPStatusError as e:
        print(f"Error HTTP {e.response.status_code}: {e.response.text}")
    finally:
        http.get(f"{GLPI_URL}/killSession", headers=headers)
        http.close()


if __name__ == "__main__":
    main()
