"""
Cliente de la API de Meta — SOLO LECTURA.

Este módulo únicamente hace peticiones GET a la Graph API de Meta.
A propósito no existe ninguna función que escriba (POST/DELETE) sobre
campañas, conjuntos de anuncios, anuncios o presupuestos. El dashboard
es de solo visualización por diseño, no solo por permisos del token.
"""
import os
import time
import httpx

GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID", "")

# España y México comparten la misma cuenta de anuncios. Los mercados se
# distinguen por el prefijo del nombre de campaña (ES_ / MX_), no por
# cuentas separadas.
MARKET_PREFIXES = {
    "es": "ES_",
    "mx": "MX_",
}

# Mapeo de los rangos de fecha que ofrece el dashboard a los date_preset
# que entiende la Graph API. "30d" se resuelve como los últimos 30 días
# corridos (no el mes calendario) para que el filtro "Último mes" sirva
# como una ventana siempre-actualizada.
DATE_PRESETS = {
    "today": "today",
    "yesterday": "yesterday",
    "7d": "last_7d",
    "30d": "last_30d",
}

# Cache en memoria simple para no pegarle a la API en cada refresh del
# navegador y para que varias personas mirando el dashboard a la vez no
# multipliquen las llamadas. TTL corto porque el objetivo es "tiempo real".
# Se cachea por rango de fecha (no por mercado): como es la misma cuenta,
# se trae una sola vez y se filtra por mercado en memoria.
_CACHE: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutos

# Tipos de acción que consideramos "el resultado" de un anuncio, en orden
# de prioridad. Como Chill It capta leads (formularios de HubSpot) además
# de algunas conversiones de compra/mensajes, probamos en este orden y nos
# quedamos con el primero que el anuncio efectivamente tenga.
RESULT_ACTION_PRIORITY = [
    "lead",
    "offsite_conversion.fb_pixel_lead",
    "onsite_conversion.lead_grouped",
    "onsite_conversion.messaging_conversation_started_7d",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "link_click",
]


class MetaAPIError(Exception):
    pass


def _get(path: str, params: dict) -> dict:
    if not ACCESS_TOKEN:
        raise MetaAPIError(
            "Falta META_ACCESS_TOKEN. Configuralo como variable de entorno "
            "en Railway (no se guarda en el código)."
        )
    params = {**params, "access_token": ACCESS_TOKEN}
    try:
        with httpx.Client(timeout=25) as client:
            resp = client.get(f"{GRAPH_BASE}/{path}", params=params)
    except httpx.TimeoutException:
        raise MetaAPIError(
            "Meta tardó demasiado en responder (timeout). Probá de nuevo "
            "en un momento."
        )
    except httpx.RequestError as e:
        raise MetaAPIError(f"No se pudo conectar con Meta: {e}")
    if resp.status_code != 200:
        detail = resp.json().get("error", {}).get("message", resp.text)
        raise MetaAPIError(f"Meta API error ({resp.status_code}): {detail}")
    return resp.json()


def _pick_result(actions: list[dict] | None) -> tuple[str | None, float]:
    """Devuelve (tipo_de_resultado, cantidad) para un anuncio, eligiendo
    el tipo de acción más relevante según RESULT_ACTION_PRIORITY."""
    if not actions:
        return None, 0.0
    by_type = {a["action_type"]: float(a["value"]) for a in actions}
    for action_type in RESULT_ACTION_PRIORITY:
        if action_type in by_type:
            return action_type, by_type[action_type]
    return None, 0.0


def _fetch_insights(account_id: str, date_preset: str) -> dict:
    """ad_id -> métricas agregadas del período."""
    fields = (
        "ad_id,ad_name,adset_name,campaign_name,spend,impressions,reach,"
        "clicks,ctr,cpc,cpm,frequency,actions"
    )
    data = _get(
        f"act_{account_id}/insights",
        {
            "level": "ad",
            "date_preset": date_preset,
            "fields": fields,
            "limit": 500,
        },
    )
    out = {}
    for row in data.get("data", []):
        result_type, result_count = _pick_result(row.get("actions"))
        spend = float(row.get("spend", 0))
        cost_per_result = round(spend / result_count, 2) if result_count > 0 else None
        out[row["ad_id"]] = {
            "ad_id": row["ad_id"],
            "ad_name": row.get("ad_name", ""),
            "adset_name": row.get("adset_name", ""),
            "campaign_name": row.get("campaign_name", ""),
            "spend": float(row.get("spend", 0)),
            "impressions": int(row.get("impressions", 0)),
            "reach": int(row.get("reach", 0)),
            "clicks": int(row.get("clicks", 0)),
            "ctr": float(row.get("ctr", 0)),
            "cpc": float(row.get("cpc", 0)) if row.get("cpc") else 0.0,
            "cpm": float(row.get("cpm", 0)) if row.get("cpm") else 0.0,
            "frequency": float(row.get("frequency", 0)) if row.get("frequency") else 0.0,
            "result_type": result_type,
            "result_count": result_count,
            "cost_per_result": cost_per_result,
        }
    return out


def _fetch_creatives(account_id: str, ad_ids: list[str]) -> dict:
    """ad_id -> {thumbnail_url, image_url, name} para mostrar la pieza."""
    if not ad_ids:
        return {}
    out = {}
    # La Graph API acepta hasta ~50 ids por llamada de forma cómoda.
    chunk_size = 50
    for i in range(0, len(ad_ids), chunk_size):
        chunk = ad_ids[i : i + chunk_size]
        data = _get(
            f"act_{account_id}/ads",
            {
                "fields": (
                    "id,name,effective_status,"
                    "creative{thumbnail_url,image_url,object_type,"
                    "object_story_spec,asset_feed_spec}"
                ),
                # Sin esto, thumbnail_url viene en 64x64 por default (pensado
                # para ícono chico en Ads Manager) y se ve borroso al
                # estirarlo en una card grande.
                "thumbnail_width": 600,
                "thumbnail_height": 600,
                "filtering": str(
                    [{"field": "id", "operator": "IN", "value": chunk}]
                ).replace("'", '"'),
                "limit": chunk_size,
            },
        )
        for row in data.get("data", []):
            creative = row.get("creative", {}) or {}
            # image_url es la imagen completa del creativo (mejor calidad);
            # solo se usa thumbnail_url como respaldo, típicamente en
            # creativos de video que no tienen image_url.
            thumb = creative.get("image_url") or creative.get("thumbnail_url")
            out[row["id"]] = {
                "thumbnail_url": thumb,
                "status": row.get("effective_status"),
            }
    return out


def _fetch_all_creatives(range_key: str) -> list[dict]:
    """Todas las filas de la cuenta para un rango, cacheadas (no filtradas
    por mercado todavía)."""
    if not AD_ACCOUNT_ID:
        raise MetaAPIError(
            "Falta el ID de cuenta de anuncios. Configurá META_AD_ACCOUNT_ID en Railway."
        )
    if range_key not in DATE_PRESETS:
        raise MetaAPIError(f"Rango de fecha desconocido: {range_key}")

    cached = _CACHE.get(range_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    insights = _fetch_insights(AD_ACCOUNT_ID, DATE_PRESETS[range_key])
    creatives = _fetch_creatives(AD_ACCOUNT_ID, list(insights.keys()))

    rows = []
    for ad_id, metrics in insights.items():
        creative_info = creatives.get(ad_id, {})
        rows.append({**metrics, **creative_info})

    rows.sort(key=lambda r: r["spend"], reverse=True)
    _CACHE[range_key] = (time.time(), rows)
    return rows


def get_creative_performance(market: str, range_key: str) -> list[dict]:
    if market not in MARKET_PREFIXES:
        raise MetaAPIError(f"Mercado desconocido: {market}")

    all_rows = _fetch_all_creatives(range_key)
    prefix = MARKET_PREFIXES[market]
    return [
        r for r in all_rows
        if r["campaign_name"].upper().startswith(prefix)
    ]
