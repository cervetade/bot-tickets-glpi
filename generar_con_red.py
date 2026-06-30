# =============================================================================
#  ACTUALIZACIÓN de _generar()  —  ahora también aguanta cortes de red
# =============================================================================
#  El problema: tu _generar atrapaba 503 (saturado) y 429 (sin cupo), pero NO
#  los cortes de conexión (el WinError 10054). Cuando la red se cortaba un
#  segundo, la excepción no era ninguna de las dos esperadas, el bot se moría,
#  y el script lo mostraba como "límite" (cuando no lo era).
#
#  El cambio: una sola línea de except nueva, marcada con  # <-- NUEVO.
#  Un corte de red es pasajero y no tiene que ver con el modelo, así que lo
#  tratamos como al 503: esperamos un toque y reintentamos el mismo modelo.
#
#  IMPORTANTE: arriba de clasificador.py, sumá este import junto a los otros:
#      import httpx
#  (El SDK de Gemini usa httpx por debajo, así que ya lo tenés instalado.)
# =============================================================================

import httpx   # <-- NUEVO (va arriba, con el resto de los imports)


def _generar(prompt, instruccion_sistema, reintentos=2):
    """
    Pide JSON a Gemini. Si un modelo está saturado (503), sin cupo (429) o se
    corta la red, reintenta unas veces y, si sigue fallando, prueba el siguiente
    modelo.
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
            except errors.ServerError as e:        # 503 saturado -> esperar y reintentar
                ultimo = e
                time.sleep(2 * (i + 1))
            except httpx.TransportError as e:       # <-- NUEVO: corte de red (WinError
                ultimo = e                          #     10054, timeouts) -> reintentar
                time.sleep(2 * (i + 1))
            except errors.ClientError as e:         # 429 sin cupo / modelo no disponible
                ultimo = e
                break  # no insistas con este modelo, pasá al siguiente
        if pos + 1 < len(modelos):
            print(f"  (Gemini: {modelo} no disponible, probando otro modelo...)")

    raise RuntimeError(
        "Gemini no respondió con ningún modelo (saturado, sin cupo o red caída). "
        "Esperá un momento y reintentá, o activá billing para subir los topes."
    ) from ultimo
