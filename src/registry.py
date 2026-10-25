"""
registry.py — Registro privado de marcas (clients.json)

Cada marca puede tener varios activos de Search Console y varias propiedades
de GA4. El archivo clients.json está en .gitignore: los IDs reales nunca
llegan al repositorio. En git solo va clients.example.json.

Formato de clients.json:
{
  "marca-ejemplo": {
    "name": "Marca Ejemplo",
    "aliases": ["ejemplo"],
    "gsc_sites": ["sc-domain:ejemplo.com", "https://tienda.ejemplo.com/"],
    "ga4_property_ids": ["123456789", "987654321"]
  }
}

El archivo se relee en cada consulta, así que se puede editar sin reiniciar
el servidor MCP.
"""

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent


def clients_path() -> Path:
    return ROOT_DIR / os.getenv("CLIENTS_FILE", "clients.json")


@dataclass
class Client:
    key: str
    name: str
    aliases: list[str] = field(default_factory=list)
    gsc_sites: list[str] = field(default_factory=list)
    ga4_property_ids: list[str] = field(default_factory=list)


# ── Normalización y validación ─────────────────────────────────────────────────

def slugify(text: str) -> str:
    """'Marca Ejemplo' → 'marca-ejemplo' (sin tildes, minúsculas)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def normalize_property_id(value: str) -> str:
    """Acepta '123456789' o 'properties/123456789'. Rechaza IDs de medición."""
    v = str(value).strip()
    if v.lower().startswith("properties/"):
        v = v.split("/", 1)[1]
    if re.fullmatch(r"(G|UA)-[\w-]+", v, flags=re.IGNORECASE):
        raise ValueError(
            f"'{v}' no es un ID de propiedad: es un ID de medición (G-…) o de "
            "Universal Analytics (UA-…). El ID de propiedad es solo numérico; "
            "búscalo con ga4_list_properties()."
        )
    if not v.isdigit():
        raise ValueError(f"ID de propiedad GA4 no válido: '{v}' (debe ser numérico).")
    return v


def normalize_gsc_site(value: str) -> str:
    """
    Devuelve la URL de propiedad GSC en el formato exacto que espera la API:
    'sc-domain:dominio.com' o 'https://dominio.com/' (con barra final).
    """
    v = str(value).strip()
    if v.lower().startswith("sc-domain:"):
        return "sc-domain:" + v[len("sc-domain:"):].strip().lower().rstrip("/")
    if re.match(r"https?://", v, flags=re.IGNORECASE):
        return v if v.endswith("/") else v + "/"
    raise ValueError(
        f"Propiedad GSC no válida: '{v}'. Usa 'sc-domain:dominio.com' o la URL "
        "completa 'https://dominio.com/'. Consulta gsc_list_properties()."
    )


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# ── Lectura ────────────────────────────────────────────────────────────────────

def _read_raw() -> dict:
    path = clients_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} no es un JSON válido: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} debe ser un objeto {{marca: {{...}}}}.")
    return data


def load_clients() -> dict[str, Client]:
    """Carga y valida todas las marcas. Devuelve {} si no existe el archivo."""
    clients = {}
    for key, entry in _read_raw().items():
        if key.startswith("_"):  # permite claves de comentario
            continue
        try:
            clients[key] = Client(
                key=key,
                name=str(entry.get("name") or key),
                aliases=[str(a) for a in _as_list(entry.get("aliases"))],
                gsc_sites=_dedupe(
                    normalize_gsc_site(s) for s in _as_list(entry.get("gsc_sites"))
                ),
                ga4_property_ids=_dedupe(
                    normalize_property_id(p) for p in _as_list(entry.get("ga4_property_ids"))
                ),
            )
        except ValueError as e:
            raise ValueError(f"Marca '{key}' en {clients_path().name}: {e}") from e
    return clients


def find_client(query: str) -> Client:
    """Busca una marca por clave, nombre o alias (sin tildes ni mayúsculas)."""
    clients = load_clients()
    if not clients:
        raise ValueError(
            f"No hay marcas registradas ({clients_path().name} no existe o está vacío). "
            "Usa save_client() o copia clients.example.json a clients.json."
        )
    q = slugify(query)
    for c in clients.values():
        if q in {slugify(c.key), slugify(c.name), *(slugify(a) for a in c.aliases)}:
            return c
    partial = [
        c for c in clients.values()
        if q and any(q in slugify(n) for n in (c.key, c.name, *c.aliases))
    ]
    if len(partial) == 1:
        return partial[0]
    available = ", ".join(c.name for c in clients.values())
    if partial:
        raise ValueError(
            f"'{query}' es ambiguo: {', '.join(c.name for c in partial)}."
        )
    raise ValueError(f"No existe la marca '{query}'. Marcas registradas: {available}.")


def client_for_site(site_url: str) -> Client | None:
    for c in load_clients().values():
        if site_url in c.gsc_sites:
            return c
    return None


def client_for_property(property_id: str) -> Client | None:
    for c in load_clients().values():
        if property_id in c.ga4_property_ids:
            return c
    return None


# ── Escritura ──────────────────────────────────────────────────────────────────

def save_client(
    name: str,
    gsc_sites: list[str] | None = None,
    ga4_property_ids: list[str] | None = None,
    aliases: list[str] | None = None,
) -> Client:
    """
    Crea la marca o AÑADE activos a una existente (nunca borra los que ya hay;
    para quitar alguno, edita clients.json a mano).
    """
    key = slugify(name)
    if not key:
        raise ValueError("El nombre de la marca no puede estar vacío.")

    new_sites = [normalize_gsc_site(s) for s in _as_list(gsc_sites)]
    new_props = [normalize_property_id(p) for p in _as_list(ga4_property_ids)]

    raw = _read_raw()
    entry = raw.get(key, {})
    merged = {
        "name": entry.get("name") or name.strip(),
        "aliases": _dedupe(_as_list(entry.get("aliases")) + _as_list(aliases)),
        "gsc_sites": _dedupe(
            [normalize_gsc_site(s) for s in _as_list(entry.get("gsc_sites"))] + new_sites
        ),
        "ga4_property_ids": _dedupe(
            [normalize_property_id(p) for p in _as_list(entry.get("ga4_property_ids"))]
            + new_props
        ),
    }
    raw[key] = merged

    path = clients_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return load_clients()[key]
