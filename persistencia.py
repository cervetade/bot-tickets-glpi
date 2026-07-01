"""
persistencia.py — Módulo "Persistencia" del bot.

Guarda el estado de cada conversación en SQLite (un solo archivo, sin servidor).
Es la "libretita" del bot: como en WhatsApp cada mensaje llega por separado, acá
recordamos en qué punto de la charla está cada número de teléfono.

Modelo de datos según la DocumentacionBot (tabla 'conversaciones'):
  telefono        TEXT  PRIMARY KEY   -- identifica la conversación
  estado          TEXT                -- en qué paso está (ver máquina de estados)
  identificado    INTEGER             -- 0/1: si el usuario ya está identificado
  usuario_nombre  TEXT
  usuario_area    TEXT
  usuario_planta  TEXT
  motivo          INTEGER             -- motivo del formulario (1..12)
  categoria_glpi  INTEGER             -- id de categoría de GLPI
  descripcion     TEXT                -- lo que el usuario fue contando
  datos           TEXT                -- JSON con los slots relevados
  historial       TEXT                -- JSON con los turnos de la charla
  ticket_id       INTEGER             -- id del ticket creado en GLPI
  creado_en       TEXT                -- timestamp ISO
  actualizado_en  TEXT                -- timestamp ISO

Este módulo NO sabe de WhatsApp, ni de Gemini, ni de GLPI: solo lee y escribe.
El orquestador es el que decide qué guardar.

Probarlo solo:  python persistencia.py
"""

import os
import json
import sqlite3
from datetime import datetime, timezone

# Ruta del archivo de base. En el contenedor irá en un volumen persistente
# (para que sobreviva a reinicios, RNF 10); configurable por .env.
RUTA_DB = os.environ.get("BOT_DB", "bot.db")

# Estados válidos (máquina de estados de la DocumentacionBot). El estado se valida
# en código contra esta lista; por eso la guardamos como texto.
ESTADOS = (
    "inicio",
    "identificacion",
    "pedir_datos",
    "esperar_descripcion",
    "esperar_lugar",
    "esperar_confirmacion",
    "procesando",
    "error",
    "finalizado",
    "esperar_cambio_motivo",
)

# Columnas que son JSON: se serializan al guardar y se parsean al leer.
_CAMPOS_JSON = ("datos", "historial")


def _conexion():
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row  # para leer filas como diccionarios
    return con


def init_db():
    """Crea la tabla si no existe. Se puede llamar siempre, es idempotente."""
    with _conexion() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS conversaciones (
                telefono        TEXT PRIMARY KEY,
                estado          TEXT NOT NULL DEFAULT 'inicio',
                identificado    INTEGER NOT NULL DEFAULT 0,
                usuario_nombre  TEXT,
                usuario_area    TEXT,
                usuario_planta  TEXT,
                motivo          INTEGER,
                categoria_glpi  INTEGER,
                titulo          TEXT,
                descripcion     TEXT,
                datos           TEXT,
                historial       TEXT,
                ticket_id       INTEGER,
                creado_en       TEXT NOT NULL,
                actualizado_en  TEXT NOT NULL
            )
        """)
        # Migración: sumar columnas nuevas si la tabla ya existía sin ellas.
        cols = {row[1] for row in con.execute("PRAGMA table_info(conversaciones)")}
        if "titulo" not in cols:
            con.execute("ALTER TABLE conversaciones ADD COLUMN titulo TEXT")


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def obtener(telefono):
    """
    Devuelve el estado de la conversación de ese teléfono como dict, o None si
    todavía no existe. Los campos JSON ('datos', 'historial') ya vienen parseados.
    """
    with _conexion() as con:
        fila = con.execute(
            "SELECT * FROM conversaciones WHERE telefono = ?", (telefono,)
        ).fetchone()
    if fila is None:
        return None
    conv = dict(fila)
    for campo in _CAMPOS_JSON:
        conv[campo] = json.loads(conv[campo]) if conv[campo] else None
    return conv


def guardar(telefono, **campos):
    """
    Crea o actualiza la conversación de un teléfono. Pasás solo los campos que
    querés tocar, ej:  guardar("549351...", estado="procesando", motivo=11)

    - Si el teléfono no existía, lo crea (con creado_en).
    - Siempre actualiza actualizado_en.
    - Los campos JSON ('datos', 'historial') se serializan solos.
    """
    if "estado" in campos and campos["estado"] not in ESTADOS:
        raise ValueError(f"Estado inválido: {campos['estado']!r}. Válidos: {ESTADOS}")

    # Serializamos los campos JSON.
    for campo in _CAMPOS_JSON:
        if campo in campos and campos[campo] is not None:
            campos[campo] = json.dumps(campos[campo], ensure_ascii=False)

    existe = obtener(telefono) is not None
    campos["actualizado_en"] = _ahora()

    with _conexion() as con:
        if existe:
            sets = ", ".join(f"{c} = ?" for c in campos)
            con.execute(
                f"UPDATE conversaciones SET {sets} WHERE telefono = ?",
                (*campos.values(), telefono),
            )
        else:
            campos["telefono"] = telefono
            campos.setdefault("creado_en", _ahora())
            cols = ", ".join(campos)
            placeholders = ", ".join("?" for _ in campos)
            con.execute(
                f"INSERT INTO conversaciones ({cols}) VALUES ({placeholders})",
                tuple(campos.values()),
            )


def borrar(telefono):
    """Elimina la conversación (ej. cuando el ticket ya se creó y se cierra)."""
    with _conexion() as con:
        con.execute("DELETE FROM conversaciones WHERE telefono = ?", (telefono,))


if __name__ == "__main__":
    # Prueba rápida: crea la tabla, guarda una conversación de ejemplo, la lee,
    # la actualiza y la vuelve a leer. No usa Gemini ni GLPI.
    init_db()
    tel = "5493510000000"

    print("1) Guardamos una conversación nueva...")
    guardar(
        tel,
        estado="esperar_descripcion",
        identificado=1,
        usuario_nombre="Lautaro",
        usuario_planta="Axion 3",
        motivo=11,
        historial=[{"rol": "usuario", "texto": "se rompió la impresora"}],
    )
    print("   ", obtener(tel), "\n")

    print("2) Actualizamos solo el estado y los datos relevados...")
    guardar(
        tel,
        estado="esperar_confirmacion",
        datos={"¿En qué dispositivo tiene la falla?": "Impresora",
               "Seleccione impresora": "HP LaserJet 1020"},
    )
    conv = obtener(tel)
    print("    estado:", conv["estado"])
    print("    datos :", conv["datos"])
    print("    creado:", conv["creado_en"], "| actualizado:", conv["actualizado_en"])

    print("\n3) Borramos la conversación de prueba.")
    borrar(tel)
    print("    obtener ahora:", obtener(tel))