"""
auth.py — Autenticación OAuth2 con Google

Ejecutar UNA SOLA VEZ para generar el token.json:
    python src/auth.py

Lo que hace este script:
1. Lee las credenciales de Google Cloud (credentials.json)
2. Abre el navegador para que autorices el acceso con tu cuenta de Google
3. Guarda el token en token.json (el servidor lo usará a partir de ahora)
4. Verifica que la conexión con GA4 y Search Console funciona

El token.json se renueva automáticamente cuando caduca.
NUNCA subas credentials.json ni token.json a GitHub.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient

# ── Configuración ──────────────────────────────────────────────────────────────

# Cargamos las variables del archivo .env
load_dotenv()

# Permisos que pedimos a Google (solo lectura)
SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# Rutas a los archivos de credenciales
# Buscamos siempre desde la raíz del proyecto, independientemente
# de desde dónde se ejecute el script
ROOT_DIR = Path(__file__).parent.parent
CREDENTIALS_FILE = ROOT_DIR / os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = ROOT_DIR / os.getenv("GOOGLE_TOKEN_FILE", "token.json")


# ── Función principal de autenticación ────────────────────────────────────────

def get_credentials() -> Credentials:
    """
    Obtiene credenciales de Google válidas.

    - Si ya existe token.json y es válido, lo reutiliza.
    - Si el token está caducado pero tiene refresh_token, lo renueva automáticamente.
    - Si no hay token, abre el navegador para autenticarse (solo la primera vez).

    Returns:
        Credentials: objeto de credenciales listo para usar con las APIs de Google.
    """
    credentials = None

    # Intentamos cargar el token existente
    if TOKEN_FILE.exists():
        print(f"📂 Token encontrado en: {TOKEN_FILE}")
        credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # Si no hay credenciales válidas, las obtenemos
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            # El token caducó pero podemos renovarlo sin abrir el navegador
            print("🔄 Token caducado, renovando automáticamente...")
            credentials.refresh(Request())
        else:
            # Primera vez: abrimos el navegador para autenticarnos
            if not CREDENTIALS_FILE.exists():
                print(f"\n❌ ERROR: No se encontró el archivo de credenciales.")
                print(f"   Buscado en: {CREDENTIALS_FILE}")
                print(f"\n   Solución:")
                print(f"   1. Ve a Google Cloud Console → APIs y servicios → Credenciales")
                print(f"   2. Descarga el JSON de tu cliente OAuth")
                print(f"   3. Guárdalo como 'credentials.json' en la raíz del proyecto")
                sys.exit(1)

            print("\n🌐 Abriendo el navegador para autenticarte con Google...")
            print("   Selecciona la cuenta que tiene acceso a GA4 y Search Console de swipeup.biz\n")

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE),
                SCOPES
            )
            credentials = flow.run_local_server(port=0)

        # Guardamos el token para no tener que autenticarnos de nuevo
        TOKEN_FILE.write_text(credentials.to_json())
        print(f"\n✅ Token guardado en: {TOKEN_FILE}")

    return credentials


# ── Verificación de conexión ───────────────────────────────────────────────────

def verify_ga4(credentials: Credentials) -> bool:
    """
    Verifica que podemos conectarnos a la Google Analytics Data API.
    Hace una consulta mínima para confirmar que el acceso funciona.
    """
    property_id = os.getenv("GA4_PROPERTY_ID")

    if not property_id:
        print("\n⚠️  GA4_PROPERTY_ID no está definido en el archivo .env")
        print("   La autenticación fue correcta pero no podemos verificar GA4.")
        return False

    try:
        print(f"\n🔍 Verificando conexión con GA4 (propiedad: {property_id})...")
        client = BetaAnalyticsDataClient(credentials=credentials)

        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
        )

        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name="date")],
            metrics=[Metric(name="sessions")],
            date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
        )

        response = client.run_report(request)
        total_sessions = sum(
            int(row.metric_values[0].value) for row in response.rows
        )

        print(f"   ✅ GA4 conectado correctamente.")
        print(f"   📊 Sesiones últimos 7 días: {total_sessions:,}")
        return True

    except Exception as e:
        print(f"   ❌ Error al conectar con GA4: {e}")
        print(f"\n   Posibles causas:")
        print(f"   - El GA4_PROPERTY_ID del .env es incorrecto")
        print(f"   - Tu cuenta no tiene acceso a esa propiedad de GA4")
        print(f"   - La Google Analytics Data API no está habilitada en Google Cloud")
        return False


def verify_gsc(credentials: Credentials) -> bool:
    """
    Verifica que podemos conectarnos a la Google Search Console API.
    Lista las propiedades disponibles para confirmar que el acceso funciona.
    """
    site_url = os.getenv("GSC_SITE_URL")

    if not site_url:
        print("\n⚠️  GSC_SITE_URL no está definido en el archivo .env")
        print("   La autenticación fue correcta pero no podemos verificar Search Console.")
        return False

    try:
        print(f"\n🔍 Verificando conexión con Search Console ({site_url})...")
        service = build("searchconsole", "v1", credentials=credentials)

        # Listamos las propiedades a las que tenemos acceso
        sites_response = service.sites().list().execute()
        sites = [s["siteUrl"] for s in sites_response.get("siteEntry", [])]

        print(f"   ✅ Search Console conectado correctamente.")
        print(f"   🌐 Propiedades disponibles: {len(sites)}")
        for site in sites:
            print(f"      - {site}")

        if site_url not in sites:
            print(f"\n   ⚠️  Aviso: '{site_url}' no aparece en tus propiedades de GSC.")
            print(f"   Comprueba que el GSC_SITE_URL del .env coincide exactamente")
            print(f"   con la URL que aparece en Search Console.")

        return True

    except Exception as e:
        print(f"   ❌ Error al conectar con Search Console: {e}")
        print(f"\n   Posibles causas:")
        print(f"   - Tu cuenta no tiene acceso a esa propiedad de Search Console")
        print(f"   - La Google Search Console API no está habilitada en Google Cloud")
        return False


# ── Punto de entrada ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  SwipeUp MCP — Autenticación con Google")
    print("=" * 60)

    # Paso 1: autenticación
    credentials = get_credentials()
    print("\n✅ Autenticación completada correctamente.")

    # Paso 2: verificación de las dos APIs
    print("\n" + "-" * 60)
    print("  Verificando conexión con las APIs de Google...")
    print("-" * 60)

    ga4_ok = verify_ga4(credentials)
    gsc_ok = verify_gsc(credentials)

    # Resumen final
    print("\n" + "=" * 60)
    print("  Resumen")
    print("=" * 60)
    print(f"  Google Analytics 4:    {'✅ OK' if ga4_ok else '❌ Error'}")
    print(f"  Search Console:        {'✅ OK' if gsc_ok else '❌ Error'}")

    if ga4_ok and gsc_ok:
        print("\n Todo listo. Puedes continuar con el servidor MCP.")
    else:
        print("\n⚠️  Revisa los errores anteriores antes de continuar.")
        print("   El servidor MCP no funcionará correctamente hasta que")
        print("   ambas conexiones estén verificadas.")

    print()