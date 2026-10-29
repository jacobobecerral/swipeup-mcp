# SwipeUp MCP — GA4 + Search Console

Servidor MCP que conecta Google Analytics 4 y Google Search Console a un asistente de IA (Claude Desktop).

Permite hacer preguntas en lenguaje natural sobre el rendimiento web de cada cliente y obtener respuestas con datos reales en tiempo real.

## Ejemplos de uso

- *"¿Qué clientes tenemos registrados?"*
- *"¿Cómo ha ido el tráfico orgánico de Cliente Ejemplo este mes?"*
- *"¿Qué queries tienen muchas impresiones y bajo CTR en Cliente Ejemplo?"*
- *"Busca las propiedades de GA4 que contengan 'ejemplo'"*

---

## Requisitos

- Python 3.11 o superior
- Claude Desktop instalado
- Cuenta de Google con acceso a los GA4 y Search Console de los clientes
- Proyecto en Google Cloud con las APIs habilitadas (ver Setup)

---

## Setup

### 1. Clonar el repositorio

```bash
git clone https://github.com/jacobobecerral/swipeup-mcp.git
cd swipeup-mcp
```

### 2. Crear entorno virtual e instalar dependencias

```bash
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Mac / Linux

pip install -r requirements.txt
```

### 3. Configurar variables de entorno

```bash
cp .env.example .env
```

El `.env` solo contiene rutas de archivos. Los activos de cada cliente se guardan en `clients.json` (ver "Registro de clientes").

### 4. Colocar credenciales de Google

Copia el archivo `credentials.json` (descargado de Google Cloud Console) a la raíz del proyecto.

⚠️ Este archivo **nunca debe subirse a GitHub**. Está en `.gitignore`.

### 5. Autenticación con Google (solo la primera vez)

```bash
python src/auth.py
```

Abrirá el navegador para que autorices el acceso con tu cuenta de Google. Generará un `token.json` automáticamente.

Habilita también en Google Cloud la **Google Analytics Data API**, la **Google Analytics Admin API** (necesaria para `ga4_list_properties`) y la **Search Console API**.

### 6. Configurar Claude Desktop

Localiza el archivo de configuración de Claude Desktop. La ruta depende de cómo esté instalado:

**Instalación estándar (descarga directa desde anthropic.com):**
```
C:\Users\TU_USUARIO\AppData\Roaming\Claude\
```

**Instalación desde Microsoft Store:**
```
C:\Users\TU_USUARIO\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\
```

> Si no sabes cuál tienes: abre Claude Desktop → Archivo → Configuración → Extensiones → Configuración avanzada → "Abrir carpeta de configuración de extensiones". Esa es tu carpeta correcta.

Añade la sección `mcpServers` al archivo de configuración que encuentres ahí (sin borrar el contenido existente):

```json
{
  "mcpServers": {
    "swipeup-mcp": {
      "command": "C:\\ruta\\a\\tu\\proyecto\\venv\\Scripts\\python.exe",
      "args": ["C:\\ruta\\a\\tu\\proyecto\\src\\server.py"]
    }
  }
}
```

Sustituye `C:\\ruta\\a\\tu\\proyecto` por la ruta real donde clonaste el repositorio.

### 7. Ejecutar

Reinicia Claude Desktop completamente. El servidor MCP estará disponible automáticamente en el chat.

---

## Estructura del proyecto

```
swipeup-mcp/
├── .gitignore           # Archivos excluidos de GitHub
├── .env.example         # Plantilla de variables de entorno (SÍ va a GitHub)
├── .env                 # Variables de entorno reales (NO va a GitHub)
├── clients.example.json # Plantilla del registro de clientes (SÍ va a GitHub)
├── clients.json         # Registro real de clientes e IDs (NO va a GitHub)
├── README.md            # Este archivo
├── requirements.txt     # Dependencias Python
├── credentials.json     # Credenciales OAuth (NO va a GitHub)
├── token.json           # Token de acceso (NO va a GitHub, se genera automáticamente)
└── src/
    ├── auth.py          # Script de autenticación OAuth (ejecutar una vez)
    ├── registry.py      # Lectura/validación/escritura de clients.json
    └── server.py        # Servidor MCP principal
```

---

## APIs de Google utilizadas

- **Google Analytics Data API** — consultas de tráfico GA4
- **Google Search Console API** — consultas de rendimiento SEO

Ambas en modo **solo lectura**. El proyecto no escribe ni modifica datos en Google.

---

## Seguridad

- Las credenciales OAuth (`credentials.json`) y el token (`token.json`) **nunca se suben a GitHub**.
- Los permisos solicitados a Google son mínimos y de solo lectura.
- El servidor corre en local — no hay endpoints expuestos a internet en la fase 1.

---

## Registro de clientes (clients.json)

`clients.json` está en `.gitignore`, así que los IDs reales nunca se suben al repositorio. Se relee en cada consulta: no hace falta reiniciar Claude Desktop al editarlo. Cada cliente tiene un nombre, alias, una o varias propiedades de Search Console y, como mucho, una de GA4 (`null` si no tiene).

```json
{
  "clientes": [
    {
      "nombre": "Cliente Ejemplo",
      "alias": ["ejemplo", "ejemplo.com"],
      "gsc": ["sc-domain:ejemplo.com", "https://tienda.ejemplo.com/"],
      "ga4_property_id": "123456789"
    }
  ]
}
```

**Consultar:** todas las herramientas aceptan `cliente="Nombre o alias"` (sin importar mayúsculas ni tildes) y resuelven solas la propiedad de GSC y el ID de GA4. `site` y `property_id` explícitos siguen funcionando y tienen prioridad.

- **Sin valores por defecto:** si no se indica `cliente`, `site` ni `property_id`, la herramienta devuelve un error pidiéndolo. Nunca devuelve datos de un cliente (ni de la agencia) por omisión.
- **Cliente sin GA4** (`"ga4_property_id": null`): las herramientas de GA4 devuelven el aviso *"Este cliente no tiene propiedad de GA4"*.
- **Nombre en las cabeceras:** las respuestas muestran el cliente y el ID, p. ej. `Cliente Ejemplo (123456789)`.
- Un alias ambiguo (p. ej. `ejemplo` si hay varios clientes que lo contienen) devuelve un error con los candidatos.
- `cross_seo_performance` necesita un único par GSC + GA4: si el cliente tiene varias propiedades de GSC, indica `site`.

**Añadir un cliente:**
1. `gsc_list_properties(search="texto")` → URLs exactas de Search Console.
2. `ga4_list_properties(search="texto")` → nombre, ID numérico, cuenta de GA y dominio de cada propiedad de GA4.
3. `save_client(name=..., gsc_sites=[...], ga4_property_ids=[...], aliases=[...])`.
4. `list_clients(verify=True)` → comprueba que la cuenta tiene acceso a todo (y avisa de las propiedades de GSC sin verificar).

> El ID de propiedad GA4 es solo numérico. El ID de medición (`G-XXXX`) o el de Universal Analytics (`UA-…`) **no** sirven; el MCP los rechaza con un aviso.

**Fechas:** los periodos tienen exactamente N días. Search Console termina 3 días antes de hoy (datos consolidados) y GA4 ayer; el periodo anterior tiene la misma duración y no se solapa.

---

## Fases del proyecto

- **Fase 1 (completada):** servidor MCP local, pruebas con Claude Desktop, datos reales, soporte multi-cliente y multi-activo GSC/GA4
- **Fase 2 (futura):** despliegue en Google Cloud Run, service accounts, acceso compartido sin token.json manual

---

*Proyecto interno de SwipeUp — Fase 1*