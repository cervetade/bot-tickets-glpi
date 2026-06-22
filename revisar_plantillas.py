"""
revisar_plantillas.py — Revisa qué plantillas usan las categorías de 4.0 y
qué campos OBLIGATORIOS exige cada una. SOLO LECTURA (GET), no toca nada.

Sirve para saber si, al crear un ticket en una categoría de 4.0, vas a tener
que mandar campos extra además de name / content / categoría / type.

Uso:
    python revisar_plantillas.py

.env: GLPI_URL, GLPI_APP_TOKEN, GLPI_USER_TOKEN (+ GLPI_PROFILE_ID, GLPI_AREA_PREFIX)
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GLPI_URL = os.environ["GLPI_URL"].rstrip("/")
GLPI_APP_TOKEN = os.environ["GLPI_APP_TOKEN"]
GLPI_USER_TOKEN = os.environ["GLPI_USER_TOKEN"]
GLPI_PROFILE_ID = os.environ.get("GLPI_PROFILE_ID")
PREFIX = os.environ.get("GLPI_AREA_PREFIX", "4.0")


def main():
    http = httpx.Client(timeout=60)
    r = http.get(
        f"{GLPI_URL}/initSession",
        headers={"Authorization": f"user_token {GLPI_USER_TOKEN}", "App-Token": GLPI_APP_TOKEN},
    )
    r.raise_for_status()
    token = r.json()["session_token"]
    H = {"Content-Type": "application/json", "Session-Token": token, "App-Token": GLPI_APP_TOKEN}
    if GLPI_PROFILE_ID:
        http.post(f"{GLPI_URL}/changeActiveProfile", headers=H,
                  json={"profiles_id": int(GLPI_PROFILE_ID)}).raise_for_status()

    try:
        # Mapa num -> nombre de campo, para traducir los campos obligatorios
        nombres = {}
        try:
            so = http.get(f"{GLPI_URL}/listSearchOptions/Ticket", headers=H).json()
            nombres = {str(k): v.get("name", k) for k, v in so.items()
                       if isinstance(v, dict) and "name" in v}
        except Exception:
            pass

        # Categorías del área, SIN expandir (para tener los IDs de plantilla)
        cats = http.get(f"{GLPI_URL}/ITILCategory", headers=H,
                        params={"range": "0-999", "expand_dropdowns": "false"}).json()

        plantillas = {}  # id_plantilla -> cantidad de categorías que la usan
        for c in cats:
            full = c.get("completename", "")
            if not full.startswith(PREFIX):
                continue
            for campo in ("tickettemplates_id_incident", "tickettemplates_id_demand"):
                tid = c.get(campo, 0)
                if tid and int(tid) != 0:
                    plantillas[int(tid)] = plantillas.get(int(tid), 0) + 1

        if not plantillas:
            print(f"Ninguna categoria de '{PREFIX}' tiene plantilla propia (usan la Default).")
            print("=> Tu POST minimo (name / content / categoria / type) deberia alcanzar.")
            return

        print(f"Plantillas usadas por las categorias de '{PREFIX}':\n")
        for tid, cant in plantillas.items():
            try:
                tpl = http.get(f"{GLPI_URL}/TicketTemplate/{tid}", headers=H).json()
                tname = tpl.get("name", f"id {tid}")
            except Exception:
                tname = f"id {tid}"
            print(f"== Plantilla '{tname}' (id {tid}) — usada en {cant} categoria(s) ==")

            try:
                obl = http.get(f"{GLPI_URL}/TicketTemplate/{tid}/TicketTemplateMandatoryField",
                               headers=H).json()
                if obl:
                    print("  Campos OBLIGATORIOS:")
                    for f in obl:
                        num = str(f.get("num"))
                        print(f"    - {nombres.get(num, 'campo num ' + num)}")
                else:
                    print("  Campos OBLIGATORIOS: ninguno")
            except httpx.HTTPStatusError as e:
                print(f"  (no pude leer los obligatorios: HTTP {e.response.status_code})")
            print()

    finally:
        http.get(f"{GLPI_URL}/killSession", headers=H)
        http.close()


if __name__ == "__main__":
    main()
