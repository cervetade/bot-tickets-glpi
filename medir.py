"""
medir.py — Banco de pruebas: ¿cuánto mejora la clasificación con los ejemplos?

Qué hace: agarra los 20 casos de banco_pruebas_20.json (tickets reales que NO están
entre los ejemplos), se los pasa al bot DOS veces —una CON los ejemplos few-shot
y otra SIN— y compara la categoría que adivinó contra la que el ticket tuvo en
GLPI. Al final te tira la precisión de cada modo y la diferencia.

Lo corrés VOS (necesita tu GEMINI_API_KEY y GLPI prendido para traer categorías):
    python medir.py

CUOTA (pensada para free tier): son 20 casos x 2 = 40 pedidos. Va lento a propósito
(6s entre pedidos) para no chocar el límite POR MINUTO. Si igual pega un límite,
espera un minuto y reintenta solo; si es el límite DIARIO, guarda el progreso en
resultados_medicion.json y al volver a correr el script retoma donde quedó. O sea,
nunca perdés lo hecho y podés correrlo en tandas (hoy una parte, mañana el resto).

Va en la misma carpeta que clasificador.py, formulario_guia.json, glpi_client.py
y banco_pruebas_20.json.
"""

import json
import time
import os

from clasificador import clasificar
from glpi_client import GLPIClient

PAUSA = 6                         # segundos entre pedidos (no saturar el límite por minuto)
ESPERA_REINTENTO = 65             # si pega el límite POR MINUTO, espera esto y reintenta
ARCHIVO_BANCO = "banco_pruebas_20.json"
ARCHIVO_RESULTADOS = "resultados_medicion.json"   # progreso (se puede reanudar)


def _norm(s):
    """Normaliza una categoría para comparar sin tropezar con mayúsculas/espacios."""
    return " ".join((s or "").split()).casefold()


def cargar_progreso():
    if os.path.exists(ARCHIVO_RESULTADOS):
        with open(ARCHIVO_RESULTADOS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_progreso(res):
    with open(ARCHIVO_RESULTADOS, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)


def clasificar_seguro(texto, categorias, motivos, con_ejemplos):
    """
    Devuelve una tupla (estado, categoria):
      ("ok", "<categoría>")    el bot eligió una categoría.
      ("sin_cat", "")          el bot respondió bien pero NO eligió categoría
                               (texto ambiguo -> pidió aclaración). Es un resultado
                               VÁLIDO de la medición, no un fallo de API.
      ("error_api", None)      error real de API o red (cupo, conexión). SOLO esto
                               frena la corrida.
    """
    try:
        r = clasificar(texto, categorias, motivos, con_ejemplos=con_ejemplos)
        cat = r.get("categoria_nombre")
        return ("ok", cat) if cat else ("sin_cat", "")
    except Exception as e:
        print(f"    (error de API/red: {type(e).__name__})")
        return ("error_api", None)


def main():
    with open(ARCHIVO_BANCO, encoding="utf-8") as f:
        banco = json.load(f)

    with open("formulario_guia.json", encoding="utf-8") as f:
        motivos = json.load(f)["motivos_posibles"]

    glpi = GLPIClient()
    glpi.init_session()
    try:
        categorias = glpi.get_categories()
    finally:
        glpi.kill_session()
    print(f"{len(categorias)} categorías cargadas de GLPI.")

    resultados = cargar_progreso()
    print(f"Casos ya medidos de antes: {len(resultados)} / {len(banco)}\n")

    for i, caso in enumerate(banco, 1):
        clave = str(i)
        if clave in resultados:
            continue   # ya estaba medido (reanudación)

        texto = caso["texto"]
        esperada = caso["categoria_esperada"]
        print(f"[{i}/{len(banco)}] {texto[:60]}...")

        est_con, pred_con = clasificar_seguro(texto, categorias, motivos, True)
        time.sleep(PAUSA)
        est_sin, pred_sin = clasificar_seguro(texto, categorias, motivos, False)
        time.sleep(PAUSA)

        # SOLO un error real de API/red frena la corrida. Que el bot no clasifique
        # ("sin_cat") es un resultado válido, NO un corte. Ante error de API puede
        # ser el límite por minuto (se recupera) o el diario (no): esperamos y
        # reintentamos una vez; si insiste, ahí sí cortamos y guardamos.
        if est_con == "error_api" or est_sin == "error_api":
            print(f"    (error de API; espero {ESPERA_REINTENTO}s y reintento este caso...)")
            time.sleep(ESPERA_REINTENTO)
            est_con, pred_con = clasificar_seguro(texto, categorias, motivos, True)
            time.sleep(PAUSA)
            est_sin, pred_sin = clasificar_seguro(texto, categorias, motivos, False)
            time.sleep(PAUSA)

        if est_con == "error_api" or est_sin == "error_api":
            print("\n  Error de API que no se recupera (límite diario o red). Progreso "
                  "guardado. Volvé a correr 'python medir.py' más tarde para retomar acá.")
            guardar_progreso(resultados)
            return

        resultados[clave] = {
            "texto": texto,
            "esperada": esperada,
            "pred_con": pred_con,                 # "" si el bot no clasificó
            "pred_sin": pred_sin,
            "ok_con": _norm(pred_con) == _norm(esperada),
            "ok_sin": _norm(pred_sin) == _norm(esperada),
            "sin_cat_con": est_con == "sin_cat",  # el bot pidió aclaración (con ejemplos)
            "sin_cat_sin": est_sin == "sin_cat",
        }
        guardar_progreso(resultados)

    reportar(resultados)


def reportar(resultados):
    total = len(resultados)
    if total == 0:
        print("No hay resultados todavía.")
        return

    ok_con = sum(r["ok_con"] for r in resultados.values())
    ok_sin = sum(r["ok_sin"] for r in resultados.values())
    pc = ok_con / total * 100
    ps = ok_sin / total * 100

    print("\n" + "=" * 60)
    print(f"  RESULTADO  ({total} casos)")
    print("=" * 60)
    print(f"  CON ejemplos (few-shot): {ok_con}/{total}  =  {pc:.1f}%")
    print(f"  SIN ejemplos:            {ok_sin}/{total}  =  {ps:.1f}%")
    print(f"  Diferencia:              {pc - ps:+.1f} puntos")
    print("=" * 60)

    ayudo = [r for r in resultados.values() if r["ok_con"] and not r["ok_sin"]]
    empeoro = [r for r in resultados.values() if r["ok_sin"] and not r["ok_con"]]
    fallan_ambos = [r for r in resultados.values() if not r["ok_con"] and not r["ok_sin"]]

    if ayudo:
        print(f"\n  Donde los ejemplos DESEMPATARON a favor ({len(ayudo)}):")
        for r in ayudo:
            print(f"   • \"{r['texto'][:55]}...\"")
            print(f"       esperada: {r['esperada']}")
            print(f"       sin ej.:  {r['pred_sin']}  (mal)")

    if empeoro:
        print(f"\n  Donde los ejemplos EMPEORARON ({len(empeoro)}) — para revisar:")
        for r in empeoro:
            print(f"   • \"{r['texto'][:55]}...\"")
            print(f"       esperada:  {r['esperada']}")
            print(f"       con ej.:   {r['pred_con']}  (mal)")

    if fallan_ambos:
        print(f"\n  Fallan con y sin ejemplos ({len(fallan_ambos)}) — casos difíciles:")
        for r in fallan_ambos:
            print(f"   • \"{r['texto'][:55]}...\"")
            print(f"       esperada: {r['esperada']}  |  dio: {r['pred_con'] or '(no clasificó)'}")

    # Casos donde el bot, con ejemplos, NO eligió categoría y pidió aclaración.
    # No son errores de medición: en producción seguiría la charla y repreguntaría.
    pidio_aclaracion = [r for r in resultados.values() if r.get("sin_cat_con")]
    if pidio_aclaracion:
        print(f"\n  El bot pidió aclaración en vez de clasificar ({len(pidio_aclaracion)}):")
        print("  (texto ambiguo; en producción repreguntaría. Acá cuenta como no-acierto.)")
        for r in pidio_aclaracion:
            print(f"   • \"{r['texto'][:55]}...\"  (esperada: {r['esperada']})")


if __name__ == "__main__":
    main()