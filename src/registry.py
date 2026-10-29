"""
registry.py — Registro privado de clientes (clients.json)

Cada cliente tiene un nombre, alias, una o varias propiedades de Search
Console y como mucho una propiedad de GA4 (null si no tiene). El archivo
clients.json está en .gitignore: los IDs reales nunca llegan al repositorio.
En git solo va clients.example.json.

Formato de clients.json:
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

También se aceptan los nombres name/aliases/gsc_sites/ga4_property_ids, una
lista de IDs en ga4_property_id, y un objeto {clave: {...}} en lugar de la
lista "clientes". Las claves que empiezan por "_" (_info, _nota) se ignoran.

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


class NotAvailable(ValueError):
    """El cliente existe, pero no tiene el activo pedido: es un aviso, no un fallo."""


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
    """'Cliente Ejemplo' → 'cliente-ejemplo' (sin tildes, minúsculas)."""
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


def _dedupe(items) -> list:
    return list(dict.fromkeys(items))


def _first(entry: dict, *names):
    for n in names:
        if n in entry:
            return entry[n]
    return None


# ── Lectura ────────────────────────────────────────────────────────────────────

def _read_raw() -> tuple[dict, list[dict]]:
    """Devuelve (metadatos del nivel superior, lista de entradas en bruto)."""
    path = clients_path()
    if not path.exists():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} no es un JSON válido: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} debe ser un objeto JSON.")

    if isinstance(data.get("clientes"), list):
        meta = {k: v for k, v in data.items() if k != "clientes"}
        return meta, [e for e in data["clientes"] if isinstance(e, dict)]

    # Formato antiguo: {clave: {name, ...}}
    meta = {k: v for k, v in data.items() if k.startswith("_")}
    entries = []
    for key, e in data.items():
        if not key.startswith("_") and isinstance(e, dict):
            entries.append({"nombre": e.get("name") or e.get("nombre") or key, **e})
    return meta, entries


def _parse(entry: dict) -> Client:
    name = str(_first(entry, "nombre", "name") or "").strip()
    if not name:
        raise ValueError("hay un cliente sin 'nombre'.")
    try:
        return Client(
            key=slugify(name),
            name=name,
            aliases=[str(a) for a in _as_list(_first(entry, "alias", "aliases"))],
            gsc_sites=_dedupe(
                normalize_gsc_site(s) for s in _as_list(_first(entry, "gsc", "gsc_sites"))
            ),
            ga4_property_ids=_dedupe(
                normalize_property_id(p)
                for p in _as_list(_first(entry, "ga4_property_id", "ga4_property_ids"))
            ),
        )
    except ValueError as e:
        raise ValueError(f"Cliente '{name}' en {clients_path().name}: {e}") from e


def load_clients() -> dict[str, Client]:
    """Carga y valida todos los clientes. Devuelve {} si no existe el archivo."""
    clients: dict[str, Client] = {}
    for entry in _read_raw()[1]:
        c = _parse(entry)
        if c.key in clients:
            raise ValueError(f"Cliente duplicado en {clients_path().name}: '{c.name}'.")
        clients[c.key] = c
    return clients


def find_client(query: str) -> Client:
    """Busca un cliente por nombre o alias (sin tildes ni mayúsculas)."""
    clients = load_clients()
    if not clients:
        raise ValueError(
            f"No hay clientes registrados ({clients_path().name} no existe o está vacío). "
            "Usa save_client() o copia clients.example.json a clients.json."
        )
    q = slugify(query)
    for c in clients.values():
        if q in {c.key, *(slugify(a) for a in c.aliases)}:
            return c
    partial = [
        c for c in clients.values()
        if q and any(q in slugify(n) for n in (c.name, *c.aliases))
    ]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise ValueError(
            f"'{query}' es ambiguo: coincide con {', '.join(c.name for c in partial)}. "
            "Indica el nombre completo."
        )
    available = ", ".join(c.name for c in clients.values())
    raise ValueError(f"No existe el cliente '{query}'. Clientes registrados: {available}.")


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

def _ga4_field(ids: list[str]):
    """null, 'id' o ['id', ...] según cuántas propiedades haya."""
    return None if not ids else ids[0] if len(ids) == 1 else ids


def save_client(
    name: str,
    gsc_sites: list[str] | None = None,
    ga4_property_ids: list[str] | None = None,
    aliases: list[str] | None = None,
) -> Client:
    """
    Crea el cliente o AÑADE activos a uno existente (nunca borra los que ya
    hay; para quitar alguno, edita clients.json a mano). Escribe en el
    formato {"clientes": [...]} conservando _info y las notas (_nota).
    """
    key = slugify(name)
    if not key:
        raise ValueError("El nombre del cliente no puede estar vacío.")

    new_sites = [normalize_gsc_site(s) for s in _as_list(gsc_sites)]
    new_props = [normalize_property_id(p) for p in _as_list(ga4_property_ids)]

    meta, entries = _read_raw()
    for e in entries:
        _parse(e)  # valida lo existente antes de reescribir el archivo

    entry = next((e for e in entries if slugify(str(_first(e, "nombre", "name"))) == key), None)
    if entry is None:
        entry = {"nombre": name.strip()}
        entries.append(entry)
    cur = _parse(entry)

    entry.pop("name", None)
    entry["nombre"] = cur.name
    for old in ("aliases", "gsc_sites", "ga4_property_ids"):
        entry.pop(old, None)
    entry["alias"] = _dedupe(cur.aliases + _as_list(aliases))
    entry["gsc"] = _dedupe(cur.gsc_sites + new_sites)
    entry["ga4_property_id"] = _ga4_field(_dedupe(cur.ga4_property_ids + new_props))

    path = clients_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({**meta, "clientes": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return load_clients()[key]
