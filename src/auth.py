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
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient

# ── Configuración ──────────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).parent.parent

# Cargamos las variables del archivo .env (ruta absoluta, independiente del cwd)
load_dotenv(ROOT_DIR / ".env")

# Permisos que pedimos a Google (solo lectura)
SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# Rutas a los archivos de credenciales
# Buscamos siempre desde la raíz del proyecto, independientemente
# de desde dónde se ejecute el script
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
            print("   Selecciona la cuenta que tiene acceso a los GA4 y Search Console de tus marcas\n")

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
    Verifica el acceso a GA4 listando las propiedades visibles para la cuenta
    (Admin API). No depende de ningún ID concreto en el .env.
    """
    try:
        print("\n🔍 Verificando conexión con GA4 (Admin API)...")
        admin = AnalyticsAdminServiceClient(credentials=credentials)
        props = [
            (ps.display_name, ps.property.split("/")[-1])
            for summary in admin.list_account_summaries()
            for ps in summary.property_summaries
        ]
        print(f"   ✅ GA4 conectado correctamente. Propiedades visibles: {len(props)}")
        for name, pid in props[:10]:
            print(f"      - {name} ({pid})")
        if len(props) > 10:
            print(f"      ... y {len(props) - 10} más (usa ga4_list_properties en el MCP)")
        return True

    except Exception as e:
        print(f"   ❌ Error al conectar con GA4: {e}")
        print("\n   Posibles causas:")
        print(f"   - Tu cuenta no tiene acceso a ninguna propiedad de GA4")
        print(f"   - 'Google Analytics Admin API' no está habilitada en Google Cloud")
        print(f"   - 'Google Analytics Data API' no está habilitada en Google Cloud")
        return False


def verify_gsc(credentials: Credentials) -> bool:
    """
    Verifica que podemos conectarnos a la Google Search Console API
    listando las propiedades disponibles.
    """
    try:
        print("\n🔍 Verificando conexión con Search Console...")
        service = build("searchconsole", "v1", credentials=credentials)
        sites = [s["siteUrl"] for s in service.sites().list().execute().get("siteEntry", [])]

        print(f"   ✅ Search Console conectado correctamente. Propiedades: {len(sites)}")
        for site in sites[:10]:
            print(f"      - {site}")
        if len(sites) > 10:
            print(f"      ... y {len(sites) - 10} más (usa gsc_list_properties en el MCP)")
        return True

    except Exception as e:
        print(f"   ❌ Error al conectar con Search Console: {e}")
        print("\n   Posibles causas:")
        print(f"   - Tu cuenta no tiene acceso a ninguna propiedad de Search Console")
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