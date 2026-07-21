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

# La moneda de la cuenta casi no cambia nunca, así que se cachea aparte
# con un TTL mucho más largo.
_CURRENCY_CACHE: dict[str, tuple[float, str]] = {}
CURRENCY_CACHE_TTL_SECONDS = 3600  # 1 hora


def get_account_currency() -> str:
    """Moneda real configurada en la cuenta de Meta (ej: 'USD'). Como
    España y México comparten cuenta, es una sola para los dos mercados —
    no se asume por mercado, se pregunta a la API."""
    cached = _CURRENCY_CACHE.get(AD_ACCOUNT_ID)
    if cached and (time.time() - cached[0]) < CURRENCY_CACHE_TTL_SECONDS:
        return cached[1]
    data = _get(f"act_{AD_ACCOUNT_ID}", {"fields": "currency"})
    currency = data.get("currency", "USD")
    _CURRENCY_CACHE[AD_ACCOUNT_ID] = (time.time(), currency)
    return currency

# La conversión personalizada "SQL" casi no cambia, así que se cachea con
# un TTL largo.
_SQL_CACHE: dict[str, tuple[float, str | None]] = {}
SQL_CACHE_TTL_SECONDS = 3600  # 1 hora


class MetaAPIError(Exception):
    pass


def _get_sql_action_type() -> str | None:
    """Busca en la cuenta una conversión personalizada que corresponda a
    Sales Qualified Lead (el nombre real en la cuenta es 'salesqualifiedlead',
    sin espacios — confirmado en Ads Manager) y devuelve el action_type que
    usa la API de insights para ella, o None si no se encuentra."""
    cached = _SQL_CACHE.get(AD_ACCOUNT_ID)
    if cached and (time.time() - cached[0]) < SQL_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        data = _get(f"act_{AD_ACCOUNT_ID}/customconversions", {"fields": "id,name"})
    except MetaAPIError:
        return None
    action_type = None
    for cc in data.get("data", []):
        normalized = cc.get("name", "").lower().replace(" ", "").replace("_", "")
        if "salesqualifiedlead" in normalized or "sql" in normalized:
            action_type = f"offsite_conversion.custom.{cc['id']}"
            break
    _SQL_CACHE[AD_ACCOUNT_ID] = (time.time(), action_type)
    return action_type


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


def _pick_result(actions: list[dict] | None, sql_action_type: str | None) -> tuple[str, float]:
    """Devuelve (tipo_de_resultado, cantidad) para un anuncio. El
    resultado que le importa a Chill It es siempre el Sales Qualified
    Lead (SQL) — nunca se reemplaza en silencio por clics, leads
    genéricos u otra métrica, ni siquiera cuando el anuncio todavía no
    generó ningún SQL en el período (en ese caso la cantidad es 0, un
    resultado legítimo, no "no hay dato"). Prioridad para encontrarlo:
    1) la conversión personalizada SQL si aparece por nombre en la cuenta,
    2) cualquier acción cuyo action_type contenga "salesqualifiedlead",
    3) "offsite_conversion.fb_pixel_custom" — confirmado con datos reales
       de esta cuenta: es el bucket genérico donde Meta agrupa el evento
       de píxel personalizado (HubSpot marca el lead como SQL acá). Si
       esta cuenta sumara un segundo evento de píxel personalizado en el
       futuro, quedarían mezclados bajo este mismo nombre — hoy no es
       el caso."""
    by_type = {a["action_type"]: float(a["value"]) for a in (actions or [])}

    if sql_action_type and sql_action_type in by_type:
        return "sql", by_type[sql_action_type]

    for action_type, value in by_type.items():
        if "salesqualifiedlead" in action_type.lower().replace("_", ""):
            return "sql", value

    if "offsite_conversion.fb_pixel_custom" in by_type:
        return "sql", by_type["offsite_conversion.fb_pixel_custom"]

    return "sql", 0.0


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
    sql_action_type = _get_sql_action_type()
    out = {}
    for row in data.get("data", []):
        result_type, result_count = _pick_result(row.get("actions"), sql_action_type)
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


def _fetch_video_pictures(video_ids: list[str]) -> dict:
    """video_id -> URL pública de portada del video.

    A propósito NO se usa object_story_spec.video_data.image_url: esa es
    una URL tipo facebook.com/ads/image/?d=... que solo carga si quien la
    ve está logueado en Facebook con permiso sobre la cuenta — por eso no
    se veía para nadie del equipo. El campo "picture" del objeto Video sí
    es una URL pública de CDN, igual que las fotos."""
    out = {}
    for vid in video_ids:
        try:
            data = _get(vid, {"fields": "picture"})
        except MetaAPIError:
            continue  # si un video puntual falla, seguimos con el resto
        if data.get("picture"):
            out[vid] = data["picture"]
    return out


def _fetch_creatives(account_id: str, ad_ids: list[str]) -> dict:
    """ad_id -> {thumbnail_url, image_url, name} para mostrar la pieza."""
    if not ad_ids:
        return {}
    out = {}
    video_ids_seen: set[str] = set()
    prelim: dict[str, dict] = {}

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
                "filtering": str(
                    [{"field": "id", "operator": "IN", "value": chunk}]
                ).replace("'", '"'),
                "limit": chunk_size,
            },
        )
        for row in data.get("data", []):
            creative = row.get("creative", {}) or {}
            story_video = (creative.get("object_story_spec") or {}).get("video_data") or {}
            video_id = story_video.get("video_id")
            if video_id:
                video_ids_seen.add(video_id)
            prelim[row["id"]] = {
                "image_url": creative.get("image_url"),
                "thumbnail_url": creative.get("thumbnail_url"),
                "video_id": video_id,
                "status": row.get("effective_status"),
            }

    video_pictures = _fetch_video_pictures(list(video_ids_seen))

    for ad_id, info in prelim.items():
        # Prioridad: imagen completa (fotos) > portada pública del video
        # (fetch aparte) > miniatura chica como último recurso.
        thumb = (
            info["image_url"]
            or video_pictures.get(info["video_id"])
            or info["thumbnail_url"]
        )
        out[ad_id] = {
            "thumbnail_url": thumb,
            "status": info["status"],
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


def get_debug_sample(market: str, range_key: str, limit: int = 4) -> list[dict]:
    """SOLO DIAGNÓSTICO (temporal): devuelve, sin transformar, las acciones
    y la estructura del creativo tal cual las manda Meta para los primeros
    `limit` anuncios del mercado/rango pedido. Sigue siendo de solo
    lectura — no modifica nada — pero no está pensado para el dashboard
    final, solo para ver el formato real de los datos y ajustar el código
    con precisión en vez de adivinar."""
    if market not in MARKET_PREFIXES:
        raise MetaAPIError(f"Mercado desconocido: {market}")
    if range_key not in DATE_PRESETS:
        raise MetaAPIError(f"Rango de fecha desconocido: {range_key}")
    if not AD_ACCOUNT_ID:
        raise MetaAPIError("Falta META_AD_ACCOUNT_ID.")

    prefix = MARKET_PREFIXES[market]
    data = _get(
        f"act_{AD_ACCOUNT_ID}/insights",
        {
            "level": "ad",
            "date_preset": DATE_PRESETS[range_key],
            "fields": "ad_id,ad_name,campaign_name,actions",
            "limit": 500,
        },
    )
    rows = [
        r for r in data.get("data", [])
        if r.get("campaign_name", "").upper().startswith(prefix)
    ][:limit]
    ad_ids = [r["ad_id"] for r in rows]

    creative_by_id = {}
    if ad_ids:
        cdata = _get(
            f"act_{AD_ACCOUNT_ID}/ads",
            {
                "fields": (
                    "id,creative{thumbnail_url,image_url,object_type,"
                    "object_story_spec,asset_feed_spec}"
                ),
                "filtering": str(
                    [{"field": "id", "operator": "IN", "value": ad_ids}]
                ).replace("'", '"'),
                "limit": limit,
            },
        )
        for row in cdata.get("data", []):
            creative_by_id[row["id"]] = row.get("creative", {})

    return [
        {
            "ad_name": r.get("ad_name"),
            "actions": r.get("actions", []),
            "creative": creative_by_id.get(r["ad_id"], {}),
        }
        for r in rows
    ]
