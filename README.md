# SwipeUp MCP — GA4 + Search Console

Servidor MCP que conecta Google Analytics 4 y Google Search Console a un asistente de IA (Claude Desktop).

Permite hacer preguntas en lenguaje natural sobre el rendimiento web de swipeup.biz y obtener respuestas con datos reales en tiempo real.

## Ejemplos de uso

- *"¿Cómo ha ido el tráfico orgánico este mes?"*
- *"¿Qué páginas han recibido más clics desde Google?"*
- *"¿Qué queries tienen muchas impresiones y bajo CTR?"*
- *"¿Ha habido alguna caída respecto al mes pasado?"*

---

## Requisitos

- Python 3.11 o superior
- Claude Desktop instalado
- Cuenta de Google con acceso a GA4 y Search Console de swipeup.biz
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

Edita el archivo `.env` y rellena:
- `GA4_PROPERTY_ID` — ID numérico de tu propiedad GA4
- `GSC_SITE_URL` — URL exacta de tu sitio en Search Console

### 4. Colocar credenciales de Google

Copia el archivo `credentials.json` (descargado de Google Cloud Console) a la raíz del proyecto.

⚠️ Este archivo **nunca debe subirse a GitHub**. Está en `.gitignore`.

### 5. Autenticación con Google (solo la primera vez)

```bash
python src/auth.py
```

Abrirá el navegador para que autorices el acceso con tu cuenta de Google. Generará un `token.json` automáticamente.

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
├── README.md            # Este archivo
├── requirements.txt     # Dependencias Python
├── credentials.json     # Credenciales OAuth (NO va a GitHub)
├── token.json           # Token de acceso (NO va a GitHub, se genera automáticamente)
└── src/
    ├── auth.py          # Script de autenticación OAuth (ejecutar una vez)
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

## Fases del proyecto

- **Fase 1 (actual):** servidor MCP local, pruebas con Claude Desktop, datos de swipeup.biz
- **Fase 2:** herramientas de análisis cruzado GA4 + Search Console
- **Fase 3 (futura):** despliegue en Google Cloud Run, service accounts, multi-cliente

---

*Proyecto interno de SwipeUp — Fase 1*