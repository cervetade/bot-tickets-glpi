"""
glpi_client.py — Cliente de conexión a la API de GLPI (Accion Lift)

Trae las categorías de ticket de un área concreta, ya filtradas y formateadas.
Solo lectura. Diseñado para reutilizarse en otras áreas cambiando el prefijo.

Uso:
    python glpi_client.py

.env necesario:
    GLPI_URL, GLPI_APP_TOKEN, GLPI_USER_TOKEN
.env opcional:
    GLPI_PROFILE_ID   -> perfil con permisos (ej. 4 = Super-Admin)
    GLPI_AREA_PREFIX  -> área a filtrar por nombre (default "4.0"; vacío = todas)
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GLPI_URL = os.environ["GLPI_URL"].rstrip("/")
GLPI_APP_TOKEN = os.environ["GLPI_APP_TOKEN"]
GLPI_USER_TOKEN = os.environ["GLPI_USER_TOKEN"]
GLPI_PROFILE_ID = os.environ.get("GLPI_PROFILE_ID")               # opcional
GLPI_AREA_PREFIX = os.environ.get("GLPI_AREA_PREFIX", "4.0")      # área a filtrar


class GLPIClient:
    def __init__(self):
        self.base_url = GLPI_URL
        self.app_token = GLPI_APP_TOKEN
        self.user_token = GLPI_USER_TOKEN
        self.profile_id = GLPI_PROFILE_ID
        self.session_token = None
        self._http = httpx.Client(timeout=60)

    def init_session(self) -> str:
        """Abre sesión y, si hay perfil configurado, cambia a ese perfil."""
        r = self._http.get(
            f"{self.base_url}/initSession",
            headers={
                "Authorization": f"user_token {self.user_token}",
                "App-Token": self.app_token,
            },
        )
        r.raise_for_status()
        self.session_token = r.json()["session_token"]
        if self.profile_id:
            self.change_active_profile(self.profile_id)
        return self.session_token

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Session-Token": self.session_token,
            "App-Token": self.app_token,
        }

    def change_active_profile(self, profile_id):
        r = self._http.post(
            f"{self.base_url}/changeActiveProfile",
            headers=self._headers(),
            json={"profiles_id": int(profile_id)},
        )
        r.raise_for_status()

    def get_categories(self, area_prefix=GLPI_AREA_PREFIX, solo_para_tickets=True):
        """
        Trae las categorías de ticket (ITILCategory).
        - area_prefix: filtra por el comienzo del nombre completo (ej. "4.0").
          Vacío o None = todas.
        - solo_para_tickets: descarta las categorías 'contenedor' que no sirven
          para crear tickets (ni incidente ni solicitud).
        """
        r = self._http.get(
            f"{self.base_url}/ITILCategory",
            headers=self._headers(),
            params={"range": "0-999", "expand_dropdowns": "true"},
        )
        r.raise_for_status()
        cats = r.json()

        out = []
        for c in cats:
            full = c.get("completename") or c.get("name", "")
            if area_prefix and not full.startswith(area_prefix):
                continue
            es_incidente = bool(c.get("is_incident", 0))
            es_solicitud = bool(c.get("is_request", 0))
            if solo_para_tickets and not (es_incidente or es_solicitud):
                continue
            out.append({
                "id": c["id"],
                "nombre": full,
                "incidente": es_incidente,
                "solicitud": es_solicitud,
                "visible_helpdesk": bool(c.get("is_helpdeskvisible", 0)),
            })
        return out

    def kill_session(self):
        if self.session_token:
            self._http.get(f"{self.base_url}/killSession", headers=self._headers())
            self.session_token = None
        self._http.close()


if __name__ == "__main__":
    glpi = GLPIClient()
    try:
        glpi.init_session()
        print(f"OK -> sesion abierta (perfil: {glpi.profile_id or 'por defecto'})\n")

        cats = glpi.get_categories()
        print(f"Categorias del area '{GLPI_AREA_PREFIX}' usables para tickets: {len(cats)}\n")
        for c in cats:
            tipos = []
            if c["incidente"]:
                tipos.append("incidente")
            if c["solicitud"]:
                tipos.append("solicitud")
            visible = "visible" if c["visible_helpdesk"] else "oculta"
            print(f'  [{c["id"]:>4}] {c["nombre"]}  ->  {", ".join(tipos)}  ({visible})')

        if not cats:
            print("No apareci\u00f3 ninguna categoria con ese prefijo.")
            print(f"Quiza el area no se llama exactamente '{GLPI_AREA_PREFIX}'.")
            print("Proba dejando GLPI_AREA_PREFIX vacio en el .env para ver todas.")
    finally:
        glpi.kill_session()