"""
server.py — Servidor MCP principal de SwipeUp

Expone herramientas para consultar GA4 y Google Search Console
desde Claude Desktop u otro cliente compatible con MCP.

Todas las herramientas admiten parámetros opcionales para consultar
cualquier propiedad a la que tenga acceso la cuenta autenticada:
  - site        → URL de propiedad en GSC (ej: sc-domain:ricopesca.com)
  - property_id → ID numérico de propiedad GA4 (ej: 123456789)

Si se omiten, se usan los valores del archivo .env (swipeup.biz por defecto).

Uso:
    python src/server.py

Requisitos previos:
    - Haber ejecutado src/auth.py al menos una vez (genera token.json)
    - Tener el .env configurado con GA4_PROPERTY_ID y GSC_SITE_URL
"""

import os
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    OrderBy,
    RunReportRequest,
)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ── Configuración ──────────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")
TOKEN_FILE = ROOT_DIR / os.getenv("GOOGLE_TOKEN_FILE", "token.json")
CREDENTIALS_FILE = ROOT_DIR / os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
DEFAULT_GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID")
DEFAULT_GSC_SITE_URL = os.getenv("GSC_SITE_URL")

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# Instancia del servidor MCP
mcp = FastMCP("SwipeUp MCP")


# ── Autenticación ──────────────────────────────────────────────────────────────

def get_credentials() -> Credentials:
    """
    Carga y renueva automáticamente las credenciales de Google.
    Si el token ha caducado, lo renueva sin intervención del usuario.
    """
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "No se encontró token.json. Ejecuta primero: python src/auth.py"
        )

    credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        TOKEN_FILE.write_text(credentials.to_json())

    return credentials


def get_ga4_client() -> BetaAnalyticsDataClient:
    return BetaAnalyticsDataClient(credentials=get_credentials())


def get_gsc_service():
    return build("searchconsole", "v1", credentials=get_credentials())


# ── Helpers de resolución de cliente ──────────────────────────────────────────

def resolve_gsc_site(site: str) -> str:
    """
    Devuelve la URL de propiedad GSC a usar.
    Si se pasa un valor explícito, lo usa; si no, usa el valor del .env.
    """
    s = site.strip() if site else ""
    if s:
        return s
    if DEFAULT_GSC_SITE_URL:
        return DEFAULT_GSC_SITE_URL
    raise ValueError(
        "No se ha especificado ninguna propiedad GSC. "
        "Pasa el parámetro 'site' o configura GSC_SITE_URL en el .env"
    )


def resolve_ga4_property(property_id: str) -> str:
    """
    Devuelve el ID de propiedad GA4 a usar.
    Si se pasa un valor explícito, lo usa; si no, usa el valor del .env.
    """
    p = property_id.strip() if property_id else ""
    if p:
        return p
    if DEFAULT_GA4_PROPERTY_ID:
        return DEFAULT_GA4_PROPERTY_ID
    raise ValueError(
        "No se ha especificado ningún ID de propiedad GA4. "
        "Pasa el parámetro 'property_id' o configura GA4_PROPERTY_ID en el .env"
    )


def get_domain_from_gsc_url(site_url: str) -> str:
    """
    Extrae el dominio de una URL de propiedad GSC para normalizar rutas.

    Ejemplos:
      sc-domain:swipeup.biz     → swipeup.biz
      https://ricopesca.com/    → ricopesca.com
      http://example.com        → example.com
    """
    url = site_url.strip()
    if url.startswith("sc-domain:"):
        return url[len("sc-domain:"):]
    # Quitamos protocolo y trailing slash
    url = url.replace("https://", "").replace("http://", "").rstrip("/")
    return url


# ── Helpers de fechas ──────────────────────────────────────────────────────────

def date_range_str(days: int) -> tuple[str, str]:
    """Devuelve (start_date, end_date) en formato YYYY-MM-DD para los últimos N días."""
    end = datetime.today()
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def previous_period(start: str, end: str) -> tuple[str, str]:
    """Dado un rango de fechas, devuelve el periodo anterior de igual duración."""
    fmt = "%Y-%m-%d"
    s = datetime.strptime(start, fmt)
    e = datetime.strptime(end, fmt)
    delta = e - s
    prev_end = s - timedelta(days=1)
    prev_start = prev_end - delta
    return prev_start.strftime(fmt), prev_end.strftime(fmt)


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def fmt_num(value: float) -> str:
    return f"{int(value):,}"


# ── Herramienta de descubrimiento de propiedades ───────────────────────────────

@mcp.tool()
def gsc_list_properties() -> str:
    """
    Lista todas las propiedades de Google Search Console a las que tiene acceso
    la cuenta autenticada. Úsala para saber qué dominios puedes consultar y
    obtener las URLs exactas que necesitas pasar al parámetro 'site' en las
    demás herramientas.

    Ejemplo de uso posterior:
        gsc_seo_summary(site="sc-domain:ricopesca.com")
    """
    try:
        service = get_gsc_service()
        sites_response = service.sites().list().execute()
        sites = sites_response.get("siteEntry", [])

        if not sites:
            return "No se encontraron propiedades en la cuenta de Search Console."

        lines = [
            f"🌐 Propiedades disponibles en Search Console ({len(sites)} total)\n",
            f"   Usa estas URLs exactas en el parámetro 'site' de cualquier herramienta:\n",
        ]
        for site in sites:
            url = site["siteUrl"]
            permission = site.get("permissionLevel", "desconocido")
            lines.append(f"  • {url}   [{permission}]")

        lines.append(
            f"\n  Ejemplo: gsc_seo_summary(site=\"{sites[0]['siteUrl']}\")"
        )
        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al listar propiedades de Search Console: {e}"


# ── Herramientas GA4 ───────────────────────────────────────────────────────────

@mcp.tool()
def ga4_traffic_summary(days: int = 30, property_id: str = "") -> str:
    """
    Devuelve un resumen general del tráfico desde Google Analytics 4.
    Úsala para preguntas como: ¿cuántas sesiones hemos tenido este mes?, ¿cuántos
    usuarios únicos?, ¿cuál es la tasa de rebote?, ¿ha subido o bajado el tráfico?

    Args:
        days: Número de días a analizar hacia atrás desde hoy (por defecto 30).
        property_id: ID numérico de propiedad GA4 (ej: "257135421"). Si se omite,
                     usa la propiedad configurada en el .env (swipeup.biz).
    """
    try:
        prop = resolve_ga4_property(property_id)
        client = get_ga4_client()
        start, end = date_range_str(days)
        prev_start, prev_end = previous_period(start, end)

        def fetch(s, e):
            req = RunReportRequest(
                property=f"properties/{prop}",
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="activeUsers"),
                    Metric(name="screenPageViews"),
                    Metric(name="bounceRate"),
                    Metric(name="averageSessionDuration"),
                    Metric(name="newUsers"),
                ],
                date_ranges=[DateRange(start_date=s, end_date=e)],
            )
            r = client.run_report(req)
            row = r.rows[0].metric_values if r.rows else None
            return row

        curr = fetch(start, end)
        prev = fetch(prev_start, prev_end)

        if not curr:
            return "No hay datos disponibles para el periodo seleccionado."

        sessions = int(curr[0].value)
        users = int(curr[1].value)
        pageviews = int(curr[2].value)
        bounce = float(curr[3].value) * 100
        duration = float(curr[4].value)
        new_users = int(curr[5].value)

        prev_sessions = int(prev[0].value) if prev else 0
        delta = ((sessions - prev_sessions) / prev_sessions * 100) if prev_sessions else 0
        trend = f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%"

        minutes = int(duration // 60)
        seconds = int(duration % 60)

        label = f"propiedad {prop}" if prop != DEFAULT_GA4_PROPERTY_ID else "swipeup.biz"

        return (
            f"📊 Resumen GA4 — {label} — últimos {days} días ({start} → {end})\n\n"
            f"  Sesiones:           {fmt_num(sessions)}  ({trend} vs periodo anterior)\n"
            f"  Usuarios activos:   {fmt_num(users)}\n"
            f"  Usuarios nuevos:    {fmt_num(new_users)}\n"
            f"  Páginas vistas:     {fmt_num(pageviews)}\n"
            f"  Tasa de rebote:     {fmt_pct(bounce)}\n"
            f"  Duración media:     {minutes}m {seconds}s\n"
        )

    except Exception as e:
        return f"❌ Error al consultar GA4: {e}"


@mcp.tool()
def ga4_traffic_by_channel(days: int = 30, property_id: str = "") -> str:
    """
    Devuelve el tráfico desglosado por canal en GA4.
    Úsala para preguntas como: ¿qué canal trae más tráfico?, ¿cuánto tráfico
    orgánico tenemos?, ¿qué porcentaje viene de redes sociales o email?

    Args:
        days: Número de días a analizar (por defecto 30).
        property_id: ID numérico de propiedad GA4. Si se omite, usa swipeup.biz.
    """
    try:
        prop = resolve_ga4_property(property_id)
        client = get_ga4_client()
        start, end = date_range_str(days)

        req = RunReportRequest(
            property=f"properties/{prop}",
            dimensions=[Dimension(name="sessionDefaultChannelGroup")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="activeUsers"),
                Metric(name="bounceRate"),
            ],
            date_ranges=[DateRange(start_date=start, end_date=end)],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        )
        resp = client.run_report(req)

        if not resp.rows:
            return "No hay datos de canales para el periodo seleccionado."

        total_sessions = sum(int(r.metric_values[0].value) for r in resp.rows)
        label = f"propiedad {prop}" if prop != DEFAULT_GA4_PROPERTY_ID else "swipeup.biz"

        lines = [f"📡 Tráfico por canal — {label} — últimos {days} días ({start} → {end})\n"]
        for row in resp.rows:
            channel = row.dimension_values[0].value
            sessions = int(row.metric_values[0].value)
            users = int(row.metric_values[1].value)
            bounce = float(row.metric_values[2].value) * 100
            pct = sessions / total_sessions * 100 if total_sessions else 0
            lines.append(
                f"  {channel:<30} {fmt_num(sessions):>8} sesiones  "
                f"({pct:.1f}%)  |  {fmt_num(users)} usuarios  |  rebote: {fmt_pct(bounce)}"
            )

        lines.append(f"\n  Total: {fmt_num(total_sessions)} sesiones")
        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al consultar GA4: {e}"


@mcp.tool()
def ga4_top_landing_pages(days: int = 30, limit: int = 10, property_id: str = "") -> str:
    """
    Devuelve las páginas de entrada principales en GA4.
    Úsala para preguntas como: ¿por qué páginas entra la gente?, ¿qué landing
    pages tienen más tráfico?, ¿qué páginas retienen mejor a los usuarios?

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número máximo de páginas a mostrar (por defecto 10).
        property_id: ID numérico de propiedad GA4. Si se omite, usa swipeup.biz.
    """
    try:
        prop = resolve_ga4_property(property_id)
        client = get_ga4_client()
        start, end = date_range_str(days)

        req = RunReportRequest(
            property=f"properties/{prop}",
            dimensions=[Dimension(name="landingPage")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="activeUsers"),
                Metric(name="bounceRate"),
                Metric(name="averageSessionDuration"),
            ],
            date_ranges=[DateRange(start_date=start, end_date=end)],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=limit,
        )
        resp = client.run_report(req)

        if not resp.rows:
            return "No hay datos de landing pages para el periodo seleccionado."

        label = f"propiedad {prop}" if prop != DEFAULT_GA4_PROPERTY_ID else "swipeup.biz"
        lines = [f"🏠 Top {limit} landing pages — {label} — últimos {days} días ({start} → {end})\n"]
        for i, row in enumerate(resp.rows, 1):
            page = row.dimension_values[0].value
            sessions = int(row.metric_values[0].value)
            users = int(row.metric_values[1].value)
            bounce = float(row.metric_values[2].value) * 100
            duration = float(row.metric_values[3].value)
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            lines.append(
                f"  {i:>2}. {page}\n"
                f"      Sesiones: {fmt_num(sessions)}  |  Usuarios: {fmt_num(users)}  "
                f"|  Rebote: {fmt_pct(bounce)}  |  Duración: {minutes}m {seconds}s\n"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al consultar GA4: {e}"


@mcp.tool()
def ga4_period_comparison(current_days: int = 30, property_id: str = "") -> str:
    """
    Compara el tráfico en GA4 entre el periodo actual y el anterior.
    Úsala para preguntas como: ¿ha subido o bajado el tráfico este mes?,
    ¿cómo estamos respecto al mes pasado?, ¿ha habido alguna caída relevante?

    Args:
        current_days: Duración en días del periodo actual (por defecto 30).
        property_id: ID numérico de propiedad GA4. Si se omite, usa swipeup.biz.
    """
    try:
        prop = resolve_ga4_property(property_id)
        client = get_ga4_client()
        start, end = date_range_str(current_days)
        prev_start, prev_end = previous_period(start, end)

        metrics = [
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"),
            Metric(name="newUsers"),
        ]
        metric_names = ["Sesiones", "Usuarios activos", "Páginas vistas", "Usuarios nuevos"]

        req = RunReportRequest(
            property=f"properties/{prop}",
            metrics=metrics,
            date_ranges=[
                DateRange(start_date=start, end_date=end),
                DateRange(start_date=prev_start, end_date=prev_end),
            ],
        )
        resp = client.run_report(req)

        if not resp.rows:
            return "No hay datos suficientes para comparar periodos."

        row = resp.rows[0]
        n = len(metrics)
        curr_vals = [int(row.metric_values[i].value) for i in range(n)]
        prev_vals = [int(row.metric_values[i + n].value) for i in range(n)]

        label = f"propiedad {prop}" if prop != DEFAULT_GA4_PROPERTY_ID else "swipeup.biz"
        lines = [
            f"📈 Comparación de periodos — {label}\n"
            f"  Actual:   {start} → {end} ({current_days} días)\n"
            f"  Anterior: {prev_start} → {prev_end}\n"
        ]

        for name, curr, prev in zip(metric_names, curr_vals, prev_vals):
            if prev > 0:
                delta = (curr - prev) / prev * 100
                trend = f"{'↑' if delta >= 0 else '↓'} {abs(delta):.1f}%"
            else:
                trend = "—"
            lines.append(
                f"  {name:<20} {fmt_num(curr):>10}  vs  {fmt_num(prev):>10}   {trend}"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al consultar GA4: {e}"


# ── Herramientas Search Console ────────────────────────────────────────────────

@mcp.tool()
def gsc_seo_summary(days: int = 30, site: str = "") -> str:
    """
    Devuelve un resumen SEO general desde Google Search Console.
    Úsala para preguntas como: ¿cuántos clics orgánicos hemos tenido?, ¿cuántas
    impresiones?, ¿cuál es nuestro CTR medio?, ¿cuál es nuestra posición media?

    Args:
        days: Número de días a analizar (por defecto 30).
        site: URL de propiedad en GSC (ej: "sc-domain:ricopesca.com"). Si se
              omite, usa la propiedad configurada en el .env (swipeup.biz).
              Usa gsc_list_properties() para ver todas las opciones disponibles.
    """
    try:
        site_url = resolve_gsc_site(site)
        service = get_gsc_service()
        start, end = date_range_str(days)
        prev_start, prev_end = previous_period(start, end)

        def fetch(s, e):
            return service.searchanalytics().query(
                siteUrl=site_url,
                body={"startDate": s, "endDate": e, "rowLimit": 1},
            ).execute()

        curr = fetch(start, end)
        prev = fetch(prev_start, prev_end)

        def extract(data):
            row = data.get("rows", [{}])[0]
            return {
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": row.get("ctr", 0) * 100,
                "position": row.get("position", 0),
            }

        c = extract(curr)
        p = extract(prev)

        clicks_delta = ((c["clicks"] - p["clicks"]) / p["clicks"] * 100) if p["clicks"] else 0
        trend = f"+{clicks_delta:.1f}%" if clicks_delta >= 0 else f"{clicks_delta:.1f}%"

        domain = get_domain_from_gsc_url(site_url)
        return (
            f"🔍 Resumen SEO — {domain} — últimos {days} días ({start} → {end})\n\n"
            f"  Clics:             {fmt_num(c['clicks'])}  ({trend} vs periodo anterior)\n"
            f"  Impresiones:       {fmt_num(c['impressions'])}\n"
            f"  CTR medio:         {fmt_pct(c['ctr'])}\n"
            f"  Posición media:    {c['position']:.1f}\n"
        )

    except Exception as e:
        return f"❌ Error al consultar Search Console: {e}"


@mcp.tool()
def gsc_top_queries(days: int = 30, limit: int = 15, site: str = "") -> str:
    """
    Devuelve las principales queries (palabras clave) por las que un sitio
    recibe tráfico desde Google Search.
    Úsala para preguntas como: ¿por qué palabras nos encuentran?, ¿cuáles son
    nuestras keywords principales?, ¿qué términos generan más clics?

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número de queries a mostrar (por defecto 15).
        site: URL de propiedad en GSC. Si se omite, usa swipeup.biz.
    """
    try:
        site_url = resolve_gsc_site(site)
        service = get_gsc_service()
        start, end = date_range_str(days)

        resp = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start,
                "endDate": end,
                "dimensions": ["query"],
                "rowLimit": limit,
                "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
            },
        ).execute()

        rows = resp.get("rows", [])
        if not rows:
            return "No hay datos de queries para el periodo seleccionado."

        domain = get_domain_from_gsc_url(site_url)
        lines = [f"🔎 Top {limit} queries — {domain} — últimos {days} días ({start} → {end})\n"]
        for i, row in enumerate(rows, 1):
            query = row["keys"][0]
            clicks = int(row.get("clicks", 0))
            impressions = int(row.get("impressions", 0))
            ctr = row.get("ctr", 0) * 100
            position = row.get("position", 0)
            lines.append(
                f"  {i:>2}. {query}\n"
                f"      Clics: {fmt_num(clicks)}  |  Impresiones: {fmt_num(impressions)}  "
                f"|  CTR: {fmt_pct(ctr)}  |  Posición: {position:.1f}\n"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al consultar Search Console: {e}"


@mcp.tool()
def gsc_top_pages(days: int = 30, limit: int = 10, site: str = "") -> str:
    """
    Devuelve las páginas con más clics desde Google Search.
    Úsala para preguntas como: ¿qué páginas reciben más tráfico orgánico?,
    ¿qué URLs funcionan mejor en Google?, ¿qué contenido posiciona más?

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número de páginas a mostrar (por defecto 10).
        site: URL de propiedad en GSC. Si se omite, usa swipeup.biz.
    """
    try:
        site_url = resolve_gsc_site(site)
        service = get_gsc_service()
        start, end = date_range_str(days)

        resp = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start,
                "endDate": end,
                "dimensions": ["page"],
                "rowLimit": limit,
                "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
            },
        ).execute()

        rows = resp.get("rows", [])
        if not rows:
            return "No hay datos de páginas para el periodo seleccionado."

        domain = get_domain_from_gsc_url(site_url)
        lines = [f"📄 Top {limit} páginas — {domain} — últimos {days} días ({start} → {end})\n"]
        for i, row in enumerate(rows, 1):
            page = row["keys"][0]
            clicks = int(row.get("clicks", 0))
            impressions = int(row.get("impressions", 0))
            ctr = row.get("ctr", 0) * 100
            position = row.get("position", 0)
            lines.append(
                f"  {i:>2}. {page}\n"
                f"      Clics: {fmt_num(clicks)}  |  Impresiones: {fmt_num(impressions)}  "
                f"|  CTR: {fmt_pct(ctr)}  |  Posición: {position:.1f}\n"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al consultar Search Console: {e}"


@mcp.tool()
def gsc_low_ctr_opportunities(days: int = 30, min_impressions: int = 100, limit: int = 15, site: str = "") -> str:
    """
    Detecta queries con muchas impresiones pero bajo CTR.
    Estas son oportunidades SEO: páginas que aparecen en Google pero que la
    gente no clica, normalmente porque el título o la meta descripción no son
    atractivos, o porque la posición media es baja.
    Úsala para preguntas como: ¿qué oportunidades SEO tenemos?, ¿qué queries
    tienen muchas impresiones pero bajo CTR?, ¿dónde podemos mejorar el CTR?

    Args:
        days: Número de días a analizar (por defecto 30).
        min_impressions: Mínimo de impresiones para considerar una query (por defecto 100).
        limit: Número de resultados a mostrar (por defecto 15).
        site: URL de propiedad en GSC. Si se omite, usa swipeup.biz.
    """
    try:
        site_url = resolve_gsc_site(site)
        service = get_gsc_service()
        start, end = date_range_str(days)

        resp = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start,
                "endDate": end,
                "dimensions": ["query"],
                "rowLimit": 500,
                "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}],
            },
        ).execute()

        rows = resp.get("rows", [])
        if not rows:
            return "No hay datos suficientes para el análisis."

        opportunities = [
            r for r in rows
            if r.get("impressions", 0) >= min_impressions and r.get("ctr", 1) < 0.05
        ]
        opportunities.sort(key=lambda r: r.get("impressions", 0), reverse=True)
        opportunities = opportunities[:limit]

        if not opportunities:
            return (
                f"No se encontraron queries con más de {min_impressions} impresiones "
                f"y CTR inferior al 5% en los últimos {days} días."
            )

        domain = get_domain_from_gsc_url(site_url)
        lines = [
            f"🎯 Oportunidades SEO — {domain}\n"
            f"   Últimos {days} días | mín. {min_impressions} impresiones | CTR < 5%\n"
        ]
        for i, row in enumerate(opportunities, 1):
            query = row["keys"][0]
            clicks = int(row.get("clicks", 0))
            impressions = int(row.get("impressions", 0))
            ctr = row.get("ctr", 0) * 100
            position = row.get("position", 0)
            lines.append(
                f"  {i:>2}. {query}\n"
                f"      Impresiones: {fmt_num(impressions)}  |  Clics: {fmt_num(clicks)}  "
                f"|  CTR: {fmt_pct(ctr)}  |  Posición: {position:.1f}\n"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al consultar Search Console: {e}"


@mcp.tool()
def gsc_period_comparison(current_days: int = 30, site: str = "") -> str:
    """
    Compara el rendimiento SEO entre el periodo actual y el anterior.
    Úsala para preguntas como: ¿ha mejorado el SEO este mes?, ¿han subido o bajado
    los clics orgánicos respecto al mes pasado?, ¿ha cambiado la posición media?

    Args:
        current_days: Duración en días del periodo actual (por defecto 30).
        site: URL de propiedad en GSC. Si se omite, usa swipeup.biz.
    """
    try:
        site_url = resolve_gsc_site(site)
        service = get_gsc_service()
        start, end = date_range_str(current_days)
        prev_start, prev_end = previous_period(start, end)

        def fetch(s, e):
            r = service.searchanalytics().query(
                siteUrl=site_url,
                body={"startDate": s, "endDate": e, "rowLimit": 1},
            ).execute()
            row = r.get("rows", [{}])[0]
            return {
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": row.get("ctr", 0) * 100,
                "position": row.get("position", 0),
            }

        c = fetch(start, end)
        p = fetch(prev_start, prev_end)

        def delta_str(curr, prev, lower_is_better=False):
            if prev == 0:
                return "—"
            d = (curr - prev) / prev * 100
            better = (d < 0) if lower_is_better else (d >= 0)
            arrow = "↑" if d >= 0 else "↓"
            sign = "+" if d >= 0 else ""
            tag = "✅" if better else "⚠️"
            return f"{arrow} {sign}{d:.1f}% {tag}"

        domain = get_domain_from_gsc_url(site_url)
        return (
            f"📊 Comparación SEO — {domain}\n"
            f"  Actual:   {start} → {end}\n"
            f"  Anterior: {prev_start} → {prev_end}\n\n"
            f"  {'Métrica':<20} {'Actual':>12}  {'Anterior':>12}  Cambio\n"
            f"  {'-'*60}\n"
            f"  {'Clics':<20} {fmt_num(c['clicks']):>12}  {fmt_num(p['clicks']):>12}  "
            f"{delta_str(c['clicks'], p['clicks'])}\n"
            f"  {'Impresiones':<20} {fmt_num(c['impressions']):>12}  {fmt_num(p['impressions']):>12}  "
            f"{delta_str(c['impressions'], p['impressions'])}\n"
            f"  {'CTR medio':<20} {fmt_pct(c['ctr']):>12}  {fmt_pct(p['ctr']):>12}  "
            f"{delta_str(c['ctr'], p['ctr'])}\n"
            f"  {'Posición media':<20} {c['position']:>12.1f}  {p['position']:>12.1f}  "
            f"{delta_str(c['position'], p['position'], lower_is_better=True)}\n"
        )

    except Exception as e:
        return f"❌ Error al consultar Search Console: {e}"


# ── Análisis cruzado GA4 + Search Console ──────────────────────────────────────

@mcp.tool()
def cross_seo_performance(days: int = 30, limit: int = 10, site: str = "", property_id: str = "") -> str:
    """
    Cruza datos de GA4 y Search Console para identificar páginas que reciben
    clics orgánicos desde Google (GSC) pero tienen bajo rendimiento en GA4
    (alta tasa de rebote o poca duración de sesión).
    Úsala para preguntas como: ¿qué páginas atraen tráfico SEO pero no retienen?,
    ¿hay páginas que posicionan bien pero decepcionan al usuario?, ¿dónde hay
    desconexión entre el tráfico orgánico y el comportamiento en la web?

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número de páginas a mostrar (por defecto 10).
        site: URL de propiedad en GSC. Si se omite, usa swipeup.biz.
        property_id: ID numérico de propiedad GA4. Si se omite, usa swipeup.biz.
    """
    try:
        site_url = resolve_gsc_site(site)
        prop = resolve_ga4_property(property_id)
        service = get_gsc_service()
        client = get_ga4_client()
        start, end = date_range_str(days)
        domain = get_domain_from_gsc_url(site_url)

        # Datos de Search Console por página
        gsc_resp = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start,
                "endDate": end,
                "dimensions": ["page"],
                "rowLimit": 50,
                "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
            },
        ).execute()

        gsc_rows = gsc_resp.get("rows", [])
        if not gsc_rows:
            return "No hay datos de Search Console para el periodo seleccionado."

        # Mapa de path → datos GSC (normalizamos quitando el dominio)
        gsc_data = {}
        for row in gsc_rows:
            page = row["keys"][0]
            path = page.replace(f"https://{domain}", "").replace(f"http://{domain}", "")
            if not path:
                path = "/"
            gsc_data[path] = {
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": row.get("ctr", 0) * 100,
                "position": row.get("position", 0),
            }

        # Datos de GA4 por página (landing page)
        ga4_req = RunReportRequest(
            property=f"properties/{prop}",
            dimensions=[Dimension(name="landingPage")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="bounceRate"),
                Metric(name="averageSessionDuration"),
            ],
            date_ranges=[DateRange(start_date=start, end_date=end)],
            limit=200,
        )
        ga4_resp = client.run_report(ga4_req)

        ga4_data = {}
        for row in ga4_resp.rows:
            path = row.dimension_values[0].value
            ga4_data[path] = {
                "sessions": int(row.metric_values[0].value),
                "bounce": float(row.metric_values[1].value) * 100,
                "duration": float(row.metric_values[2].value),
            }

        # Cruce: páginas con clics SEO + datos de GA4
        combined = []
        for path, gsc in gsc_data.items():
            if gsc["clicks"] < 5:
                continue
            ga4 = ga4_data.get(path, {})
            combined.append({
                "path": path,
                "clicks": gsc["clicks"],
                "position": gsc["position"],
                "ctr": gsc["ctr"],
                "sessions": ga4.get("sessions", 0),
                "bounce": ga4.get("bounce", None),
                "duration": ga4.get("duration", None),
            })

        combined.sort(key=lambda x: x["clicks"], reverse=True)
        combined = combined[:limit]

        if not combined:
            return "No se encontraron páginas con datos suficientes para el cruce."

        lines = [
            f"🔗 Cruce SEO — {domain} — últimos {days} días ({start} → {end})\n"
            f"   Páginas con tráfico orgánico (GSC) y su comportamiento en GA4\n"
        ]

        for i, p in enumerate(combined, 1):
            bounce_str = fmt_pct(p["bounce"]) if p["bounce"] is not None else "sin datos"
            if p["duration"] is not None:
                dur_min = int(p["duration"] // 60)
                dur_sec = int(p["duration"] % 60)
                dur_str = f"{dur_min}m {dur_sec}s"
            else:
                dur_str = "sin datos"

            alert = " ⚠️ alto rebote" if p["bounce"] and p["bounce"] > 70 else ""

            lines.append(
                f"  {i:>2}. {p['path']}{alert}\n"
                f"      GSC → Clics: {fmt_num(p['clicks'])}  |  "
                f"Posición: {p['position']:.1f}  |  CTR: {fmt_pct(p['ctr'])}\n"
                f"      GA4 → Sesiones: {fmt_num(p['sessions'])}  |  "
                f"Rebote: {bounce_str}  |  Duración: {dur_str}\n"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error en análisis cruzado: {e}"


# ── Arranque del servidor ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DEFAULT_GA4_PROPERTY_ID or not DEFAULT_GSC_SITE_URL:
        print("❌ Faltan variables en el .env (GA4_PROPERTY_ID o GSC_SITE_URL)")
        raise SystemExit(1)

    if not TOKEN_FILE.exists():
        print("❌ No existe token.json. Ejecuta primero: python src/auth.py")
        raise SystemExit(1)

    mcp.run()