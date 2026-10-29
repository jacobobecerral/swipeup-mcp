"""
server.py — Servidor MCP principal de SwipeUp

Expone herramientas para consultar GA4 y Google Search Console
desde Claude Desktop u otro cliente compatible con MCP.

Los activos se resuelven así:
  1. site / property_id explícitos en la llamada (tienen prioridad).
  2. cliente="Nombre o alias" → activos de ese cliente en clients.json
     (archivo privado, ignorado en git). Si tiene varias propiedades GSC,
     la herramienta devuelve un bloque por cada una.
  3. Si no se indica ninguna de las tres cosas, la herramienta devuelve un
     error pidiéndolo: NO existe ningún valor por defecto, para no leer
     datos de un cliente (o de la agencia) por error.

Uso:
    python src/server.py

Requisitos previos:
    - Haber ejecutado src/auth.py al menos una vez (genera token.json)
    - Tener un clients.json (ver clients.example.json) o pasar site/property_id
"""

import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    Metric,
    OrderBy,
    RunReportRequest,
)
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import registry

# ── Configuración ──────────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")
TOKEN_FILE = ROOT_DIR / os.getenv("GOOGLE_TOKEN_FILE", "token.json")
CREDENTIALS_FILE = ROOT_DIR / os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# Search Console tarda ~2-3 días en consolidar datos ("final"). Terminar el
# periodo antes evita comparar contra días incompletos y falsear las caídas.
GSC_DATA_LAG_DAYS = 3
# GA4 procesa el día anterior con algo de retraso; el día en curso es parcial.
GA4_DATA_LAG_DAYS = 1

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


def get_ga4_admin_client() -> AnalyticsAdminServiceClient:
    return AnalyticsAdminServiceClient(credentials=get_credentials())


def get_gsc_service():
    return build("searchconsole", "v1", credentials=get_credentials())


# ── Resolución de clientes, dominios y propiedades ────────────────────────────

MISSING_TARGET = (
    "Falta indicar el cliente: pasa 'cliente' (nombre o alias; usa list_clients() "
    "para verlos) o '{explicit}'. No hay ningún valor por defecto."
)


def resolve_gsc_sites(cliente: str, site: str) -> list[str]:
    """Propiedades GSC a consultar: 'site' explícito > 'cliente'. Sin ninguno, error."""
    if site and site.strip():
        return [registry.normalize_gsc_site(site)]
    if cliente and cliente.strip():
        c = registry.find_client(cliente)
        if not c.gsc_sites:
            raise registry.NotAvailable(
                f"Este cliente no tiene propiedad de Search Console ({c.name})."
            )
        return list(c.gsc_sites)
    raise ValueError(MISSING_TARGET.format(explicit="site"))


def resolve_ga4_properties(cliente: str, property_id: str) -> list[str]:
    """Propiedades GA4 a consultar: 'property_id' explícito > 'cliente'. Sin ninguno, error."""
    if property_id and str(property_id).strip():
        return [registry.normalize_property_id(property_id)]
    if cliente and cliente.strip():
        c = registry.find_client(cliente)
        if not c.ga4_property_ids:
            raise registry.NotAvailable(
                f"Este cliente no tiene propiedad de GA4 ({c.name})."
            )
        return list(c.ga4_property_ids)
    raise ValueError(MISSING_TARGET.format(explicit="property_id"))


def fail(what: str, e: Exception) -> str:
    """Un cliente sin el activo pedido es un aviso (⚠️); cualquier otra cosa, un error."""
    if isinstance(e, registry.NotAvailable):
        return f"⚠️ {e}"
    return f"❌ {what}: {e}"


def site_label(site_url: str) -> str:
    """'Cliente Ejemplo (sc-domain:ejemplo.com)' si está registrada; si no, solo la URL."""
    c = registry.client_for_site(site_url)
    return f"{c.name} ({site_url})" if c else f"{site_url} (sin cliente registrado)"


def property_label(prop: str) -> str:
    """'Cliente Ejemplo (123456789)' si está registrada; si no, 'propiedad 123 (sin cliente…)'."""
    c = registry.client_for_property(prop)
    return f"{c.name} ({prop})" if c else f"propiedad {prop} (sin cliente registrado)"


def run_each(assets: list[str], fn) -> str:
    """Ejecuta fn(activo) para cada activo; un fallo en uno no tumba el resto."""
    blocks = []
    for asset in assets:
        try:
            blocks.append(fn(asset))
        except Exception as e:
            blocks.append(f"❌ Error en {asset}: {e}")
    return "\n\n".join(blocks)


# ── Helpers de fechas ──────────────────────────────────────────────────────────

def date_range_str(days: int, lag: int = GA4_DATA_LAG_DAYS) -> tuple[str, str]:
    """
    Devuelve (inicio, fin) YYYY-MM-DD de EXACTAMENTE `days` días, terminando
    `lag` días antes de hoy (los datos más recientes aún no están consolidados).
    """
    days = max(1, int(days))
    end = date.today() - timedelta(days=lag)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def previous_period(start: str, end: str) -> tuple[str, str]:
    """Periodo inmediatamente anterior, de la misma duración y sin solaparse."""
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    length = (e - s).days + 1
    prev_end = s - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start.isoformat(), prev_end.isoformat()


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def fmt_num(value: float) -> str:
    return f"{int(value):,}"


def fmt_duration(seconds: float) -> str:
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"


def gsc_totals(service, site_url: str, start: str, end: str) -> dict:
    """Totales agregados de GSC (sin dimensiones → una sola fila)."""
    resp = service.searchanalytics().query(
        siteUrl=site_url, body={"startDate": start, "endDate": end}
    ).execute()
    rows = resp.get("rows") or [{}]
    row = rows[0]
    return {
        "clicks": int(row.get("clicks", 0)),
        "impressions": int(row.get("impressions", 0)),
        "ctr": row.get("ctr", 0) * 100,
        "position": row.get("position", 0),
    }


def gsc_rows(service, site_url: str, start: str, end: str, dimension: str,
             limit: int, order: str = "clicks") -> list[dict]:
    # La API ordena por clics descendentes por defecto; solo ordenamos
    # localmente cuando se pide otra métrica.
    resp = service.searchanalytics().query(
        siteUrl=site_url,
        body={
            "startDate": start,
            "endDate": end,
            "dimensions": [dimension],
            "rowLimit": min(limit, 25000),
        },
    ).execute()
    rows = resp.get("rows", [])
    if order != "clicks":
        rows.sort(key=lambda r: r.get(order, 0), reverse=True)
    return rows


# ── Herramientas de gestión de clientes ──────────────────────────────────────────

@mcp.tool()
def list_clients(verify: bool = False) -> str:
    """
    Lista los clientes registrados en clients.json con sus propiedades de Search
    Console y de GA4. Úsala primero para saber qué valor pasar en 'cliente'.

    Args:
        verify: Si es True, comprueba contra Google que la cuenta autenticada
                tiene acceso a cada propiedad registrada (detecta IDs mal
                copiados o accesos que faltan).
    """
    try:
        clients = registry.load_clients()
        if not clients:
            return (
                "No hay clientes registrados todavía. Busca activos con "
                "gsc_list_properties() y ga4_list_properties() y regístralos "
                "con save_client()."
            )

        gsc_ok, ga4_ok, verify_note = {}, set(), ""
        if verify:
            try:
                gsc_ok = {
                    s["siteUrl"]: s.get("permissionLevel", "")
                    for s in get_gsc_service().sites().list().execute().get("siteEntry", [])
                }
            except Exception as e:
                verify_note += f"\n⚠️ No se pudo verificar Search Console: {e}"
            try:
                ga4_ok = {
                    ps.property.split("/")[-1]
                    for summary in get_ga4_admin_client().list_account_summaries()
                    for ps in summary.property_summaries
                }
            except Exception as e:
                verify_note += f"\n⚠️ No se pudo verificar GA4 (Admin API): {e}"

        def mark(ok_set, value, enabled):
            if not verify or not enabled:
                return ""
            if value not in ok_set:
                return "  ❌ SIN ACCESO / no encontrada"
            if isinstance(ok_set, dict) and ok_set[value] == "siteUnverifiedUser":
                return "  ⚠️ sin verificar: no devolverá datos"
            return "  ✅ acceso"

        lines = [f"🏷️ Clientes registrados ({len(clients)})\n"]
        for c in clients.values():
            alias = f"  (alias: {', '.join(c.aliases)})" if c.aliases else ""
            lines.append(f"■ {c.name}{alias}")
            lines.append(f"    Search Console ({len(c.gsc_sites)}):")
            lines += [
                f"      • {s}{mark(gsc_ok, s, bool(gsc_ok))}" for s in c.gsc_sites
            ] or ["      (ninguna)"]
            lines.append(f"    GA4 ({len(c.ga4_property_ids)}):")
            lines += [
                f"      • {p}{mark(ga4_ok, p, bool(ga4_ok))}" for p in c.ga4_property_ids
            ] or ["      (ninguna)"]
            lines.append("")
        return "\n".join(lines) + verify_note

    except Exception as e:
        return f"❌ Error al leer los clientes: {e}"


@mcp.tool()
def save_client(
    name: str,
    gsc_sites: list[str] | None = None,
    ga4_property_ids: list[str] | None = None,
    aliases: list[str] | None = None,
) -> str:
    """
    Crea un cliente en clients.json o AÑADE activos a una existente (no borra
    los que ya había). El archivo es local y está ignorado en git.

    Args:
        name: Nombre del cliente (ej: "Marca Ejemplo"). Identifica al cliente.
        gsc_sites: Propiedades de Search Console, tal como las devuelve
                   gsc_list_properties() (ej: ["sc-domain:ejemplo.com"]).
        ga4_property_ids: IDs numéricos de propiedad GA4, tal como los devuelve
                   ga4_list_properties() (NO el ID de medición "G-XXXX").
        aliases: Otros nombres con los que se puede referir al cliente.
    """
    try:
        c = registry.save_client(name, gsc_sites, ga4_property_ids, aliases)
        return (
            f"✅ Cliente '{c.name}' guardado en {registry.clients_path().name}\n"
            f"   Search Console: {', '.join(c.gsc_sites) or '—'}\n"
            f"   GA4:            {', '.join(c.ga4_property_ids) or '—'}\n"
            f"   Compruébala con list_clients(verify=True)."
        )
    except Exception as e:
        return f"❌ No se pudo guardar el cliente: {e}"


# ── Herramientas de descubrimiento ─────────────────────────────────────────────

@mcp.tool()
def gsc_list_properties(search: str = "") -> str:
    """
    Lista las propiedades de Google Search Console a las que tiene acceso la
    cuenta autenticada, con la URL exacta que hay que registrar o pasar en
    'site'. Marca cuáles ya están asignadas a un cliente de clients.json.

    Args:
        search: Texto para filtrar (ej: "ejemplo"). Vacío = todas.
    """
    try:
        sites = get_gsc_service().sites().list().execute().get("siteEntry", [])
        needle = search.strip().lower()
        if needle:
            sites = [s for s in sites if needle in s["siteUrl"].lower()]

        if not sites:
            return (
                f"No hay propiedades de Search Console que contengan '{search}'."
                if needle else
                "No se encontraron propiedades en la cuenta de Search Console."
            )

        lines = [f"🌐 Propiedades de Search Console ({len(sites)})\n"]
        for s in sorted(sites, key=lambda s: s["siteUrl"]):
            url = s["siteUrl"]
            perm = s.get("permissionLevel", "desconocido")
            owner = registry.client_for_site(registry.normalize_gsc_site(url))
            tag = f"  → {owner.name}" if owner else "  (sin asignar a ningún cliente)"
            warn = "  ⚠️ sin verificar: no devolverá datos" if perm == "siteUnverifiedUser" else ""
            lines.append(f"  • {url}   [{perm}]{tag}{warn}")
        lines.append("\n  Para registrarlas: save_client(name=..., gsc_sites=[...])")
        return "\n".join(lines)

    except Exception as e:
        return f"❌ Error al listar propiedades de Search Console: {e}"


@mcp.tool()
def ga4_list_properties(search: str = "", include_streams: bool = True) -> str:
    """
    Lista todas las propiedades de Google Analytics 4 a las que tiene acceso la
    cuenta autenticada (agrupadas por cuenta de GA), con su ID numérico de
    propiedad. Así no hace falta que el cliente busque su ID.

    Para cada propiedad muestra los flujos de datos web (URL del sitio e ID de
    medición G-XXXX), útiles para saber a qué dominio corresponde cada una.
    Marca cuáles ya están asignadas a un cliente de clients.json.

    Args:
        search: Texto para filtrar por nombre de cuenta, nombre de propiedad,
                ID o dominio del flujo (ej: "ejemplo"). Vacío = todas.
        include_streams: Si es True (por defecto) consulta los flujos de datos
                de cada propiedad. Ponlo a False para una lista más rápida.
    """
    try:
        admin = get_ga4_admin_client()
        needle = search.strip().lower()
        # Para filtrar por dominio hacen falta los flujos
        want_streams = include_streams or bool(needle)

        found = []
        for summary in admin.list_account_summaries():
            for ps in summary.property_summaries:
                pid = ps.property.split("/")[-1]
                streams = []
                if want_streams:
                    try:
                        for st in admin.list_data_streams(parent=ps.property):
                            web = st.web_stream_data
                            if web.default_uri:
                                streams.append(
                                    f"{web.default_uri}  (ID de medición {web.measurement_id})"
                                )
                            else:
                                streams.append(f"{st.display_name}  (app/otro)")
                    except Exception as e:
                        streams.append(f"(no se pudieron leer los flujos: {e})")

                haystack = " ".join(
                    [summary.display_name, ps.display_name, pid, *streams]
                ).lower()
                if needle and needle not in haystack:
                    continue
                found.append((summary.display_name, ps.display_name, pid, streams))

        if not found:
            return (
                f"No hay propiedades de GA4 que contengan '{search}'."
                if needle else
                "No se encontraron propiedades de GA4 en la cuenta autenticada."
            )

        lines = [f"📊 Propiedades de GA4 ({len(found)})\n"]
        current_account = None
        for account, pname, pid, streams in found:
            if account != current_account:
                lines.append(f"Cuenta GA: {account}")
                current_account = account
            owner = registry.client_for_property(pid)
            tag = f"→ {owner.name}" if owner else "(sin asignar a ningún cliente)"
            lines.append(f"  • {pname}")
            lines.append(f"      ID de propiedad: {pid}   {tag}")
            lines += [f"      Flujo: {s}" for s in streams if include_streams]
        lines.append("\n  Para registrarlas: save_client(name=..., ga4_property_ids=[...])")
        return "\n".join(lines)

    except Exception as e:
        if "SERVICE_DISABLED" in str(e):
            url = re.search(r'activationUrl"\s*value:\s*"([^"]+)"', str(e))
            return (
                "❌ La Google Analytics Admin API está desactivada en el proyecto de "
                "Google Cloud. Actívala aquí y reintenta en un par de minutos:\n"
                f"   {url.group(1) if url else 'https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com'}"
            )
        return (
            f"❌ Error al listar propiedades de GA4: {e}\n\n"
            "Si el error habla de permisos o de que la API está desactivada, habilita "
            "'Google Analytics Admin API' en el proyecto de Google Cloud y vuelve a "
            "ejecutar python src/auth.py si hiciera falta renovar el token."
        )


# ── Herramientas GA4 ───────────────────────────────────────────────────────────

@mcp.tool()
def ga4_traffic_summary(days: int = 30, cliente: str = "", property_id: str = "") -> str:
    """
    Devuelve un resumen general del tráfico desde Google Analytics 4.
    Úsala para preguntas como: ¿cuántas sesiones hemos tenido este mes?, ¿cuántos
    usuarios únicos?, ¿cuál es la tasa de rebote?, ¿ha subido o bajado el tráfico?

    Args:
        days: Número de días a analizar (por defecto 30). Termina ayer.
        cliente: Nombre o alias de un cliente de clients.json (ej: "Marca Ejemplo"). Si tiene
                varias propiedades GA4, devuelve un bloque por cada una.
        property_id: ID numérico de una propiedad GA4 concreta (tiene prioridad
                sobre 'cliente'). Usa ga4_list_properties() para encontrarlo.
    """
    try:
        props = resolve_ga4_properties(cliente, property_id)
        ga4 = get_ga4_client()
        start, end = date_range_str(days)
        prev_start, prev_end = previous_period(start, end)

        def one(prop: str) -> str:
            def fetch(s, e):
                resp = ga4.run_report(RunReportRequest(
                    property=f"properties/{prop}",
                    metrics=[Metric(name=n) for n in (
                        "sessions", "activeUsers", "screenPageViews",
                        "bounceRate", "averageSessionDuration", "newUsers",
                    )],
                    date_ranges=[DateRange(start_date=s, end_date=e)],
                ))
                return resp.rows[0].metric_values if resp.rows else None

            curr, prev = fetch(start, end), fetch(prev_start, prev_end)
            if not curr:
                return f"{property_label(prop)}: no hay datos para el periodo seleccionado."

            sessions = int(curr[0].value)
            prev_sessions = int(prev[0].value) if prev else 0
            if prev_sessions:
                delta = (sessions - prev_sessions) / prev_sessions * 100
                trend = f"{delta:+.1f}% vs periodo anterior"
            else:
                trend = "sin datos del periodo anterior"

            return (
                f"📊 Resumen GA4 — {property_label(prop)} — {days} días ({start} → {end})\n\n"
                f"  Sesiones:           {fmt_num(sessions)}  ({trend})\n"
                f"  Usuarios activos:   {fmt_num(float(curr[1].value))}\n"
                f"  Usuarios nuevos:    {fmt_num(float(curr[5].value))}\n"
                f"  Páginas vistas:     {fmt_num(float(curr[2].value))}\n"
                f"  Tasa de rebote:     {fmt_pct(float(curr[3].value) * 100)}\n"
                f"  Duración media:     {fmt_duration(float(curr[4].value))}"
            )

        return run_each(props, one)

    except Exception as e:
        return fail("Error al consultar GA4", e)


@mcp.tool()
def ga4_traffic_by_channel(days: int = 30, cliente: str = "", property_id: str = "") -> str:
    """
    Devuelve el tráfico desglosado por canal en GA4.
    Úsala para preguntas como: ¿qué canal trae más tráfico?, ¿cuánto tráfico
    orgánico tenemos?, ¿qué porcentaje viene de redes sociales o email?

    Args:
        days: Número de días a analizar (por defecto 30).
        cliente: Nombre o alias de un cliente de clients.json (consulta todas sus propiedades GA4).
        property_id: ID numérico de una propiedad GA4 concreta (tiene prioridad sobre 'cliente').
    """
    try:
        props = resolve_ga4_properties(cliente, property_id)
        ga4 = get_ga4_client()
        start, end = date_range_str(days)

        def one(prop: str) -> str:
            resp = ga4.run_report(RunReportRequest(
                property=f"properties/{prop}",
                dimensions=[Dimension(name="sessionDefaultChannelGroup")],
                metrics=[Metric(name="sessions"), Metric(name="activeUsers"), Metric(name="bounceRate")],
                date_ranges=[DateRange(start_date=start, end_date=end)],
                order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            ))
            if not resp.rows:
                return f"{property_label(prop)}: no hay datos de canales para el periodo."

            total = sum(int(r.metric_values[0].value) for r in resp.rows)
            lines = [f"📡 Tráfico por canal — {property_label(prop)} — {days} días ({start} → {end})\n"]
            for row in resp.rows:
                sessions = int(row.metric_values[0].value)
                pct = sessions / total * 100 if total else 0
                lines.append(
                    f"  {row.dimension_values[0].value:<30} {fmt_num(sessions):>8} sesiones  "
                    f"({pct:.1f}%)  |  {fmt_num(float(row.metric_values[1].value))} usuarios  "
                    f"|  rebote: {fmt_pct(float(row.metric_values[2].value) * 100)}"
                )
            lines.append(f"\n  Total: {fmt_num(total)} sesiones")
            return "\n".join(lines)

        return run_each(props, one)

    except Exception as e:
        return fail("Error al consultar GA4", e)


@mcp.tool()
def ga4_top_landing_pages(days: int = 30, limit: int = 10, cliente: str = "", property_id: str = "") -> str:
    """
    Devuelve las páginas de entrada principales en GA4.
    Úsala para preguntas como: ¿por qué páginas entra la gente?, ¿qué landing
    pages tienen más tráfico?, ¿qué páginas retienen mejor a los usuarios?

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número máximo de páginas a mostrar (por defecto 10).
        cliente: Nombre o alias de un cliente de clients.json (consulta todas sus propiedades GA4).
        property_id: ID numérico de una propiedad GA4 concreta (tiene prioridad sobre 'cliente').
    """
    try:
        props = resolve_ga4_properties(cliente, property_id)
        ga4 = get_ga4_client()
        start, end = date_range_str(days)

        def one(prop: str) -> str:
            resp = ga4.run_report(RunReportRequest(
                property=f"properties/{prop}",
                dimensions=[Dimension(name="landingPage")],
                metrics=[Metric(name=n) for n in (
                    "sessions", "activeUsers", "bounceRate", "averageSessionDuration")],
                date_ranges=[DateRange(start_date=start, end_date=end)],
                order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
                limit=limit,
            ))
            if not resp.rows:
                return f"{property_label(prop)}: no hay datos de landing pages para el periodo."

            lines = [f"🏠 Top {limit} landing pages — {property_label(prop)} — {days} días ({start} → {end})\n"]
            for i, row in enumerate(resp.rows, 1):
                m = row.metric_values
                lines.append(
                    f"  {i:>2}. {row.dimension_values[0].value}\n"
                    f"      Sesiones: {fmt_num(float(m[0].value))}  |  Usuarios: {fmt_num(float(m[1].value))}  "
                    f"|  Rebote: {fmt_pct(float(m[2].value) * 100)}  |  Duración: {fmt_duration(float(m[3].value))}\n"
                )
            return "\n".join(lines)

        return run_each(props, one)

    except Exception as e:
        return fail("Error al consultar GA4", e)


@mcp.tool()
def ga4_period_comparison(current_days: int = 30, cliente: str = "", property_id: str = "") -> str:
    """
    Compara el tráfico en GA4 entre el periodo actual y el anterior.
    Úsala para preguntas como: ¿ha subido o bajado el tráfico este mes?,
    ¿cómo estamos respecto al mes pasado?, ¿ha habido alguna caída relevante?

    Args:
        current_days: Duración en días del periodo actual (por defecto 30).
        cliente: Nombre o alias de un cliente de clients.json (consulta todas sus propiedades GA4).
        property_id: ID numérico de una propiedad GA4 concreta (tiene prioridad sobre 'cliente').
    """
    try:
        props = resolve_ga4_properties(cliente, property_id)
        ga4 = get_ga4_client()
        start, end = date_range_str(current_days)
        prev_start, prev_end = previous_period(start, end)

        names = ["Sesiones", "Usuarios activos", "Páginas vistas", "Usuarios nuevos"]
        metrics = ["sessions", "activeUsers", "screenPageViews", "newUsers"]

        def one(prop: str) -> str:
            # Con dos rangos GA4 devuelve UNA FILA POR RANGO (dimensión implícita
            # "dateRange"), no una fila con las métricas duplicadas.
            resp = ga4.run_report(RunReportRequest(
                property=f"properties/{prop}",
                metrics=[Metric(name=m) for m in metrics],
                date_ranges=[
                    DateRange(start_date=start, end_date=end, name="current"),
                    DateRange(start_date=prev_start, end_date=prev_end, name="previous"),
                ],
            ))
            by_range = {
                row.dimension_values[0].value: [float(v.value) for v in row.metric_values]
                for row in resp.rows
            }
            curr = by_range.get("current", [0.0] * len(metrics))
            prev = by_range.get("previous", [0.0] * len(metrics))
            if not any(curr) and not any(prev):
                return f"{property_label(prop)}: no hay datos suficientes para comparar periodos."

            lines = [
                f"📈 Comparación de periodos — {property_label(prop)}\n"
                f"  Actual:   {start} → {end} ({current_days} días)\n"
                f"  Anterior: {prev_start} → {prev_end}\n"
            ]
            for name, c, p in zip(names, curr, prev):
                if p > 0:
                    d = (c - p) / p * 100
                    trend = f"{'↑' if d >= 0 else '↓'} {abs(d):.1f}%"
                else:
                    trend = "—"
                lines.append(f"  {name:<20} {fmt_num(c):>10}  vs  {fmt_num(p):>10}   {trend}")
            return "\n".join(lines)

        return run_each(props, one)

    except Exception as e:
        return fail("Error al consultar GA4", e)


# ── Herramientas Search Console ────────────────────────────────────────────────

@mcp.tool()
def gsc_seo_summary(days: int = 30, cliente: str = "", site: str = "") -> str:
    """
    Devuelve un resumen SEO general desde Google Search Console.
    Úsala para preguntas como: ¿cuántos clics orgánicos hemos tenido?, ¿cuántas
    impresiones?, ¿cuál es nuestro CTR medio?, ¿cuál es nuestra posición media?

    Args:
        days: Número de días a analizar (por defecto 30). Termina 3 días antes
              de hoy, cuando Search Console ya tiene datos consolidados.
        cliente: Nombre o alias de un cliente de clients.json (ej: "Marca Ejemplo"). Si tiene
                varias propiedades GSC, devuelve un bloque por cada una.
        site: Propiedad GSC concreta (ej: "sc-domain:dominio.com"); tiene
              prioridad sobre 'cliente'. Usa gsc_list_properties() para verlas.
    """
    try:
        sites = resolve_gsc_sites(cliente, site)
        service = get_gsc_service()
        start, end = date_range_str(days, GSC_DATA_LAG_DAYS)
        prev_start, prev_end = previous_period(start, end)

        def one(site_url: str) -> str:
            c = gsc_totals(service, site_url, start, end)
            p = gsc_totals(service, site_url, prev_start, prev_end)
            if p["clicks"]:
                trend = f"{(c['clicks'] - p['clicks']) / p['clicks'] * 100:+.1f}% vs periodo anterior"
            else:
                trend = "sin datos del periodo anterior"
            return (
                f"🔍 Resumen SEO — {site_label(site_url)} — {days} días ({start} → {end})\n\n"
                f"  Clics:             {fmt_num(c['clicks'])}  ({trend})\n"
                f"  Impresiones:       {fmt_num(c['impressions'])}\n"
                f"  CTR medio:         {fmt_pct(c['ctr'])}\n"
                f"  Posición media:    {c['position']:.1f}"
            )

        return run_each(sites, one)

    except Exception as e:
        return fail("Error al consultar Search Console", e)


def _gsc_ranking(title_icon: str, title: str, dimension: str, days: int, limit: int,
                 cliente: str, site: str) -> str:
    sites = resolve_gsc_sites(cliente, site)
    service = get_gsc_service()
    start, end = date_range_str(days, GSC_DATA_LAG_DAYS)

    def one(site_url: str) -> str:
        rows = gsc_rows(service, site_url, start, end, dimension, limit)
        if not rows:
            return f"{site_label(site_url)}: no hay datos de {title.lower()} para el periodo."
        lines = [f"{title_icon} Top {limit} {title} — {site_label(site_url)} — {days} días ({start} → {end})\n"]
        for i, row in enumerate(rows, 1):
            lines.append(
                f"  {i:>2}. {row['keys'][0]}\n"
                f"      Clics: {fmt_num(row.get('clicks', 0))}  |  Impresiones: {fmt_num(row.get('impressions', 0))}  "
                f"|  CTR: {fmt_pct(row.get('ctr', 0) * 100)}  |  Posición: {row.get('position', 0):.1f}\n"
            )
        return "\n".join(lines)

    return run_each(sites, one)


@mcp.tool()
def gsc_top_queries(days: int = 30, limit: int = 15, cliente: str = "", site: str = "") -> str:
    """
    Devuelve las principales queries (palabras clave) por las que un sitio
    recibe tráfico desde Google Search.
    Úsala para preguntas como: ¿por qué palabras nos encuentran?, ¿cuáles son
    nuestras keywords principales?, ¿qué términos generan más clics?

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número de queries a mostrar (por defecto 15).
        cliente: Nombre o alias de un cliente de clients.json (consulta todas sus propiedades GSC).
        site: Propiedad GSC concreta; tiene prioridad sobre 'cliente'.
    """
    try:
        return _gsc_ranking("🔎", "queries", "query", days, limit, cliente, site)
    except Exception as e:
        return fail("Error al consultar Search Console", e)


@mcp.tool()
def gsc_top_pages(days: int = 30, limit: int = 10, cliente: str = "", site: str = "") -> str:
    """
    Devuelve las páginas con más clics desde Google Search.
    Úsala para preguntas como: ¿qué páginas reciben más tráfico orgánico?,
    ¿qué URLs funcionan mejor en Google?, ¿qué contenido posiciona más?

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número de páginas a mostrar (por defecto 10).
        cliente: Nombre o alias de un cliente de clients.json (consulta todas sus propiedades GSC).
        site: Propiedad GSC concreta; tiene prioridad sobre 'cliente'.
    """
    try:
        return _gsc_ranking("📄", "páginas", "page", days, limit, cliente, site)
    except Exception as e:
        return fail("Error al consultar Search Console", e)


@mcp.tool()
def gsc_low_ctr_opportunities(days: int = 30, min_impressions: int = 100, limit: int = 15,
                              max_ctr: float = 5.0, cliente: str = "", site: str = "") -> str:
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
        max_ctr: CTR máximo, en porcentaje, para considerar una oportunidad (por defecto 5).
        cliente: Nombre o alias de un cliente de clients.json (consulta todas sus propiedades GSC).
        site: Propiedad GSC concreta; tiene prioridad sobre 'cliente'.
    """
    try:
        sites = resolve_gsc_sites(cliente, site)
        service = get_gsc_service()
        start, end = date_range_str(days, GSC_DATA_LAG_DAYS)

        def one(site_url: str) -> str:
            rows = gsc_rows(service, site_url, start, end, "query", 5000, order="impressions")
            opps = [
                r for r in rows
                if r.get("impressions", 0) >= min_impressions
                and r.get("ctr", 1) * 100 < max_ctr
            ][:limit]
            if not opps:
                return (
                    f"{site_label(site_url)}: no hay queries con ≥{min_impressions} impresiones "
                    f"y CTR < {max_ctr:g}% en los últimos {days} días."
                )
            lines = [
                f"🎯 Oportunidades SEO — {site_label(site_url)}\n"
                f"   {days} días ({start} → {end}) | mín. {min_impressions} impresiones | CTR < {max_ctr:g}%\n"
            ]
            for i, row in enumerate(opps, 1):
                lines.append(
                    f"  {i:>2}. {row['keys'][0]}\n"
                    f"      Impresiones: {fmt_num(row.get('impressions', 0))}  |  Clics: {fmt_num(row.get('clicks', 0))}  "
                    f"|  CTR: {fmt_pct(row.get('ctr', 0) * 100)}  |  Posición: {row.get('position', 0):.1f}\n"
                )
            return "\n".join(lines)

        return run_each(sites, one)

    except Exception as e:
        return fail("Error al consultar Search Console", e)


@mcp.tool()
def gsc_period_comparison(current_days: int = 30, cliente: str = "", site: str = "") -> str:
    """
    Compara el rendimiento SEO entre el periodo actual y el anterior.
    Úsala para preguntas como: ¿ha mejorado el SEO este mes?, ¿han subido o bajado
    los clics orgánicos respecto al mes pasado?, ¿ha cambiado la posición media?

    Args:
        current_days: Duración en días del periodo actual (por defecto 30).
        cliente: Nombre o alias de un cliente de clients.json (consulta todas sus propiedades GSC).
        site: Propiedad GSC concreta; tiene prioridad sobre 'cliente'.
    """
    try:
        sites = resolve_gsc_sites(cliente, site)
        service = get_gsc_service()
        start, end = date_range_str(current_days, GSC_DATA_LAG_DAYS)
        prev_start, prev_end = previous_period(start, end)

        def delta_str(curr, prev, lower_is_better=False):
            if prev == 0:
                return "—"
            d = (curr - prev) / prev * 100
            better = (d < 0) if lower_is_better else (d >= 0)
            return f"{'↑' if d >= 0 else '↓'} {d:+.1f}% {'✅' if better else '⚠️'}"

        def one(site_url: str) -> str:
            c = gsc_totals(service, site_url, start, end)
            p = gsc_totals(service, site_url, prev_start, prev_end)
            return (
                f"📊 Comparación SEO — {site_label(site_url)}\n"
                f"  Actual:   {start} → {end}\n"
                f"  Anterior: {prev_start} → {prev_end}\n\n"
                f"  {'Métrica':<20} {'Actual':>12}  {'Anterior':>12}  Cambio\n"
                f"  {'-' * 60}\n"
                f"  {'Clics':<20} {fmt_num(c['clicks']):>12}  {fmt_num(p['clicks']):>12}  "
                f"{delta_str(c['clicks'], p['clicks'])}\n"
                f"  {'Impresiones':<20} {fmt_num(c['impressions']):>12}  {fmt_num(p['impressions']):>12}  "
                f"{delta_str(c['impressions'], p['impressions'])}\n"
                f"  {'CTR medio':<20} {fmt_pct(c['ctr']):>12}  {fmt_pct(p['ctr']):>12}  "
                f"{delta_str(c['ctr'], p['ctr'])}\n"
                f"  {'Posición media':<20} {c['position']:>12.1f}  {p['position']:>12.1f}  "
                f"{delta_str(c['position'], p['position'], lower_is_better=True)}"
            )

        return run_each(sites, one)

    except Exception as e:
        return fail("Error al consultar Search Console", e)


# ── Análisis cruzado GA4 + Search Console ──────────────────────────────────────

def normalize_path(url_or_path: str) -> str | None:
    """
    Reduce una URL completa (GSC) o una ruta con query (GA4) a una ruta
    comparable: sin dominio, sin query/fragmento, sin barra final, decodificada.
    """
    raw = url_or_path.strip()
    if not raw or raw == "(not set)":
        return None
    path = unquote(urlsplit(raw).path) or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


@mcp.tool()
def cross_seo_performance(days: int = 30, limit: int = 10, min_clicks: int = 5,
                          cliente: str = "", site: str = "", property_id: str = "") -> str:
    """
    Cruza Search Console y GA4 para ver cómo se comportan en la web las páginas
    que reciben clics desde Google: solo se miran las sesiones de GA4 del canal
    Organic Search, para comparar manzanas con manzanas.
    Úsala para preguntas como: ¿qué páginas atraen tráfico SEO pero no retienen?,
    ¿hay páginas que posicionan bien pero decepcionan al usuario?

    Necesita UNA propiedad GSC y UNA propiedad GA4. Si el cliente tiene varias de
    cada, indica cuál con 'site' y 'property_id' (usa list_clients()).

    Args:
        days: Número de días a analizar (por defecto 30).
        limit: Número de páginas a mostrar (por defecto 10).
        min_clicks: Clics mínimos en GSC para incluir una página (por defecto 5).
        cliente: Nombre o alias de un cliente de clients.json.
        site: Propiedad GSC concreta (tiene prioridad sobre 'cliente').
        property_id: ID de propiedad GA4 concreta (tiene prioridad sobre 'cliente').
    """
    try:
        sites = resolve_gsc_sites(cliente, site)
        props = resolve_ga4_properties(cliente, property_id)
        if len(sites) != 1 or len(props) != 1:
            return (
                "⚠️ El cruce necesita una única propiedad GSC y una única propiedad GA4, "
                f"pero hay {len(sites)} de Search Console y {len(props)} de GA4.\n"
                f"  Search Console: {', '.join(sites)}\n"
                f"  GA4:            {', '.join(props)}\n"
                "Vuelve a llamar indicando 'site' y 'property_id' del par que quieras cruzar."
            )
        site_url, prop = sites[0], props[0]

        service = get_gsc_service()
        ga4 = get_ga4_client()
        # Mismo rango para ambas fuentes: el límite lo marca el retraso de GSC
        start, end = date_range_str(days, GSC_DATA_LAG_DAYS)

        # ── Search Console: por página, agregando variantes de URL ──
        gsc_data: dict[str, dict] = {}
        for row in gsc_rows(service, site_url, start, end, "page", 5000):
            path = normalize_path(row["keys"][0])
            if path is None:
                continue
            d = gsc_data.setdefault(path, {"clicks": 0, "impressions": 0, "pos_x_imp": 0.0})
            imp = row.get("impressions", 0)
            d["clicks"] += row.get("clicks", 0)
            d["impressions"] += imp
            d["pos_x_imp"] += row.get("position", 0) * imp
        if not gsc_data:
            return f"{site_label(site_url)}: no hay datos de Search Console para el periodo."

        # ── GA4: landing pages, solo tráfico orgánico de Google ──
        ga4_resp = ga4.run_report(RunReportRequest(
            property=f"properties/{prop}",
            dimensions=[Dimension(name="landingPage")],
            metrics=[Metric(name="sessions"), Metric(name="bounceRate"),
                     Metric(name="averageSessionDuration")],
            date_ranges=[DateRange(start_date=start, end_date=end)],
            dimension_filter=FilterExpression(filter=Filter(
                field_name="sessionDefaultChannelGroup",
                string_filter=Filter.StringFilter(
                    value="Organic Search",
                    match_type=Filter.StringFilter.MatchType.EXACT,
                ),
            )),
            limit=10000,
        ))

        ga4_data: dict[str, dict] = {}
        for row in ga4_resp.rows:
            path = normalize_path(row.dimension_values[0].value)
            if path is None:
                continue
            sessions = int(row.metric_values[0].value)
            d = ga4_data.setdefault(path, {"sessions": 0, "bounce_x_s": 0.0, "dur_x_s": 0.0})
            d["sessions"] += sessions
            d["bounce_x_s"] += float(row.metric_values[1].value) * 100 * sessions
            d["dur_x_s"] += float(row.metric_values[2].value) * sessions

        # ── Cruce ──
        combined = []
        for path, g in gsc_data.items():
            if g["clicks"] < min_clicks:
                continue
            a = ga4_data.get(path)
            s = a["sessions"] if a else 0
            combined.append({
                "path": path,
                "clicks": g["clicks"],
                "position": g["pos_x_imp"] / g["impressions"] if g["impressions"] else 0,
                "ctr": g["clicks"] / g["impressions"] * 100 if g["impressions"] else 0,
                "sessions": s,
                "bounce": a["bounce_x_s"] / s if a and s else None,
                "duration": a["dur_x_s"] / s if a and s else None,
            })

        combined.sort(key=lambda x: x["clicks"], reverse=True)
        combined = combined[:limit]
        if not combined:
            return f"No hay páginas con ≥{min_clicks} clics para cruzar en este periodo."

        lines = [
            f"🔗 Cruce SEO — {site_label(site_url)} × {property_label(prop)}\n"
            f"   {days} días ({start} → {end}) | GA4 filtrado a Organic Search\n"
        ]
        for i, p in enumerate(combined, 1):
            if p["bounce"] is None:
                ga_line = "GA4 → sin sesiones orgánicas para esta página en GA4 (¿otra propiedad/dominio?)"
                alert = ""
            else:
                ga_line = (
                    f"GA4 → Sesiones: {fmt_num(p['sessions'])}  |  "
                    f"Rebote: {fmt_pct(p['bounce'])}  |  Duración: {fmt_duration(p['duration'])}"
                )
                alert = " ⚠️ alto rebote" if p["bounce"] > 70 else ""
            lines.append(
                f"  {i:>2}. {p['path']}{alert}\n"
                f"      GSC → Clics: {fmt_num(p['clicks'])}  |  "
                f"Posición: {p['position']:.1f}  |  CTR: {fmt_pct(p['ctr'])}\n"
                f"      {ga_line}\n"
            )
        return "\n".join(lines)

    except Exception as e:
        return fail("Error en análisis cruzado", e)


# ── Arranque del servidor ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # En stdio, stdout es el canal MCP: los avisos van a stderr.
    if not TOKEN_FILE.exists():
        print("❌ No existe token.json. Ejecuta primero: python src/auth.py", file=sys.stderr)
        raise SystemExit(1)

    if not registry.clients_path().exists():
        print(
            "⚠️ No hay clients.json. El servidor arranca, pero hay que pasar "
            "site/property_id o registrar clientes con save_client().",
            file=sys.stderr,
        )

    mcp.run()
