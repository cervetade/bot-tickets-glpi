"""
explorar_glpi.py — Explorador de la API de GLPI (SOLO LECTURA / GET).

Sirve para ver QUÉ podés pedir y QUÉ campos tiene cada cosa, sin tocar nada.
Todo lo que hace este script es leer. No crea, no edita, no borra.

Comandos:
    python explorar_glpi.py                 -> muestra esta ayuda
    python explorar_glpi.py perfiles        -> perfiles que tu cuenta puede usar (con sus IDs)
    python explorar_glpi.py perfil          -> perfil y entidad ACTIVOS en la sesión
    python explorar_glpi.py ITILCategory    -> lista las categorías de ticket
    python explorar_glpi.py User            -> lista usuarios (para la identificación)
    python explorar_glpi.py Entity          -> lista entidades / áreas
    python explorar_glpi.py Ticket          -> lista tickets existentes
    python explorar_glpi.py Ticket 5        -> trae el ticket nº 5 COMPLETO

IMPORTANTE — perfil de la sesión:
    Por defecto GLPI te loguea con el perfil de menor privilegio (Autoservicio),
    que NO puede listar categorías ni usuarios. Para arreglarlo:
      1) Corré 'python explorar_glpi.py perfiles' y mirá el ID del perfil que querés
         (ej. Super-Admin o Technician).
      2) Agregá esa línea a tu .env:  GLPI_PROFILE_ID=4
      3) Volvé a correr lo que quieras: la sesión va a cambiar a ese perfil sola.

Requiere en el .env: GLPI_URL, GLPI_APP_TOKEN, GLPI_USER_TOKEN
Opcional en el .env: GLPI_PROFILE_ID
"""

import json
import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

GLPI_URL = os.environ["GLPI_URL"].rstrip("/")
GLPI_APP_TOKEN = os.environ["GLPI_APP_TOKEN"]
GLPI_USER_TOKEN = os.environ["GLPI_USER_TOKEN"]
GLPI_PROFILE_ID = os.environ.get("GLPI_PROFILE_ID")  # opcional


def abrir_sesion(http: httpx.Client) -> str:
    r = http.get(
        f"{GLPI_URL}/initSession",
        headers={
            "Authorization": f"user_token {GLPI_USER_TOKEN}",
            "App-Token": GLPI_APP_TOKEN,
        },
    )
    r.raise_for_status()
    return r.json()["session_token"]


def cambiar_perfil(http: httpx.Client, headers: dict, profile_id: str):
    """Cambia el perfil activo de la sesión (necesita permiso de ese perfil)."""
    r = http.post(
        f"{GLPI_URL}/changeActiveProfile",
        headers=headers,
        json={"profiles_id": int(profile_id)},
    )
    r.raise_for_status()


def cerrar_sesion(http: httpx.Client, token: str):
    http.get(
        f"{GLPI_URL}/killSession",
        headers={"Session-Token": token, "App-Token": GLPI_APP_TOKEN},
    )


def mostrar(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    recurso = sys.argv[1]
    item_id = sys.argv[2] if len(sys.argv) > 2 else None

    http = httpx.Client(timeout=20)
    token = abrir_sesion(http)
    headers = {"Session-Token": token, "App-Token": GLPI_APP_TOKEN}

    # Si definiste un perfil en el .env, cambiamos a ese perfil
    if GLPI_PROFILE_ID:
        try:
            cambiar_perfil(http, headers, GLPI_PROFILE_ID)
            print(f"(sesión cambiada al perfil ID {GLPI_PROFILE_ID})\n")
        except httpx.HTTPStatusError as e:
            print(f"No pude cambiar al perfil {GLPI_PROFILE_ID}: {e.response.text}\n")

    try:
        # Ver qué perfiles tiene disponible tu cuenta (con sus IDs)
        if recurso.lower() == "perfiles":
            r = http.get(f"{GLPI_URL}/getMyProfiles", headers=headers)
            r.raise_for_status()
            perfiles = r.json().get("myprofiles", [])
            print("Perfiles que podés usar:\n")
            for p in perfiles:
                print(f'  ID {p.get("id"):>3}  ->  {p.get("name")}')
            print("\nPoné el ID que quieras en el .env como GLPI_PROFILE_ID.")
            return

        # Contexto activo de la sesión
        if recurso.lower() == "perfil":
            prof = http.get(f"{GLPI_URL}/getActiveProfile", headers=headers).json()
            ent = http.get(f"{GLPI_URL}/getActiveEntities", headers=headers).json()
            print("== Perfil activo ==")
            mostrar(prof)
            print("\n== Entidades activas ==")
            mostrar(ent)
            return

        params = {"expand_dropdowns": "true"}

        if item_id:
            r = http.get(f"{GLPI_URL}/{recurso}/{item_id}", headers=headers, params=params)
            r.raise_for_status()
            print(f"== {recurso} #{item_id} (todos los campos) ==\n")
            mostrar(r.json())
        else:
            params["range"] = "0-9"
            r = http.get(f"{GLPI_URL}/{recurso}", headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            print(f"== {recurso}: primeras {len(data)} filas (rango 0-9) ==\n")
            if data:
                print("Campos que tiene este itemtype:")
                print("  " + ", ".join(data[0].keys()) + "\n")
                print("Filas:")
                mostrar(data)
            else:
                print("(no hay filas, o este perfil no tiene permiso para verlas)")

    except httpx.HTTPStatusError as e:
        print(f"Error HTTP {e.response.status_code}: {e.response.text}")
    finally:
        cerrar_sesion(http, token)


if __name__ == "__main__":
    main()
