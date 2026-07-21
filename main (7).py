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

from meta_client import get_creative_performance, get_account_currency, MetaAPIError

app = FastAPI(title="Chill It · Dashboard Creativo (solo lectura)")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/creatives")
def creatives(
    market: Literal["es", "mx"] = Query(..., description="es o mx"),
    range: Literal["today", "yesterday", "7d", "30d"] = Query(...),
):
    """Devuelve la performance por creativo. Solo lectura, sin excepción."""
    try:
        rows = get_creative_performance(market, range)
        currency = get_account_currency()
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "market": market,
        "range": range,
        "currency": currency,
        "count": len(rows),
        "creatives": rows,
    }


# Sirve el frontend estático (index.html, css, js) desde la misma app,
# así el navegador solo habla con este backend y nunca ve el token de Meta.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
