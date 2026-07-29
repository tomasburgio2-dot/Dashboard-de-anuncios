"""
Backend del dashboard de performance creativa — Chill It.

IMPORTANTE: esta app expone únicamente endpoints de LECTURA (GET).
No existe, a propósito, ningún endpoint que cree, edite, pause o borre
campañas, conjuntos de anuncios, anuncios o presupuestos. El objetivo
del dashboard es visualizar performance, nunca operar la cuenta.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Literal
import os

from meta_client import (
    get_creative_performance,
    get_account_currency,
    get_market_summary,
    get_debug_sample,
    get_debug_sql_by_ad,
    get_debug_promoted_objects,
    get_debug_custom_conversions,
    get_debug_sql_resolution,
    get_debug_campaign_raw,
    MetaAPIError,
)

app = FastAPI(title="Chill It · Dashboard Creativo (solo lectura)")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/debug/campaign-raw")
def debug_campaign_raw(range: Literal["today", "yesterday", "7d", "30d"] = Query(...)):
    """DIAGNÓSTICO TEMPORAL — solo lectura. Respuesta cruda de Meta a
    nivel campaña, para ver si hay algo raro (ej. filas duplicadas)
    detrás del resumen doblado."""
    try:
        return get_debug_campaign_raw(range)
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/debug/sql-resolution")
def debug_sql_resolution(
    market: Literal["es", "mx"] = Query(...),
    range: Literal["today", "yesterday", "7d", "30d"] = Query(...),
):
    """DIAGNÓSTICO TEMPORAL — solo lectura. Expone el razonamiento
    interno completo para encontrar exactamente dónde se corta la
    cadena entre la conversión personalizada y el resultado final."""
    try:
        return get_debug_sql_resolution(market, range)
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/debug/custom-conversions")
def debug_custom_conversions():
    """DIAGNÓSTICO TEMPORAL — solo lectura. Llamada directa y cruda a
    customconversions, sin tapar errores, para ver si el problema es de
    permisos o de datos."""
    try:
        return get_debug_custom_conversions()
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/debug/promoted-object")
def debug_promoted_object(market: Literal["es", "mx"] = Query(...)):
    """DIAGNÓSTICO TEMPORAL — solo lectura. A qué evento optimiza cada
    campaña, según Meta mismo (no una suposición del código)."""
    try:
        return {"campaigns": get_debug_promoted_objects(market)}
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/debug/sql-by-ad")
def debug_sql_by_ad(
    market: Literal["es", "mx"] = Query(...),
    range: Literal["today", "yesterday", "7d", "30d"] = Query(...),
):
    """DIAGNÓSTICO TEMPORAL — solo lectura. SQL calculado por anuncio,
    para comparar uno por uno contra Ads Manager, o para comparar hoy
    contra ayer y ver si un rango ya estable también da 0."""
    try:
        return {"ads": get_debug_sql_by_ad(market, range)}
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/debug/sample")
def debug_sample(
    market: Literal["es", "mx"] = Query(...),
    range: Literal["today", "yesterday", "7d", "30d"] = Query(...),
):
    """DIAGNÓSTICO TEMPORAL — solo lectura, no modifica nada. Devuelve los
    datos crudos de Meta sin transformar, para ajustar el mapeo de
    resultados e imágenes con precisión. Se puede borrar este endpoint
    una vez resueltos los dos bugs pendientes."""
    try:
        return {"sample": get_debug_sample(market, range)}
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/creatives")
def creatives(
    market: Literal["es", "mx"] = Query(..., description="es o mx"),
    range: Literal["today", "yesterday", "7d", "30d"] = Query(...),
):
    """Devuelve la performance por creativo. Solo lectura, sin excepción."""
    try:
        rows = get_creative_performance(market, range)
        currency = get_account_currency()
        summary = get_market_summary(market, range)
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "market": market,
        "range": range,
        "currency": currency,
        "count": len(rows),
        "creatives": rows,
        "summary": summary,
    }


# Sirve el frontend estático (index.html, css, js) desde la misma app,
# así el navegador solo habla con este backend y nunca ve el token de Meta.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
