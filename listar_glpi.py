"""
listar_glpi.py — Diagnóstico: muestra qué trae GLPI para una lista (dropdown).

Sirve para ver si una lista está cargada en GLPI antes de usarla en el bot.

Uso:
    python listar_glpi.py              # por defecto, modelos de impresora
    python listar_glpi.py Printer      # impresoras físicas (activos)
    python listar_glpi.py Location     # plantas / ubicaciones
    python listar_glpi.py User         # usuarios
"""

import sys
from glpi_client import GLPIClient

itemtype = sys.argv[1] if len(sys.argv) > 1 else "PrinterModel"

g = GLPIClient()
try:
    g.init_session()
    items = g.listar_items(itemtype)
    print(f"{itemtype}: {len(items)} registros cargados en GLPI\n")
    for it in items:
        print(f'  [{it["id"]:>4}]  {it["nombre"]}')
finally:
    g.kill_session()
