"""Cliente minimo de la API de Notion con control de ritmo y reintentos.

Notion limita a ~3 peticiones/segundo y responde 429 con Retry-After.
Como el backfill inicial puede crear miles de paginas (una por serie),
el limitador no es opcional.

Se fija Notion-Version 2022-06-28: la version 2025-09-03 introdujo el
modelo de "data sources" y rompe las llamadas a /databases. Mientras cada
base tenga una unica fuente de datos, esta version sigue siendo valida.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger(__name__)

BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class RateLimiter:
    """Cubo simple: garantiza un intervalo minimo entre peticiones."""

    def __init__(self, per_second: float = 2.5):
        self.min_interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


class NotionClient:
    def __init__(self, token: str, per_second: float = 2.5, timeout: int = 30):
        if not token:
            raise ValueError("Falta el token de integracion de Notion")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })
        self.limiter = RateLimiter(per_second)
        self.timeout = timeout
        self.calls = 0

    def request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{BASE}{path}"
        for attempt in range(6):
            self.limiter.wait()
            self.calls += 1
            r = self.session.request(method, url, timeout=self.timeout, **kwargs)

            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                log.warning("Notion 429, esperando %.1fs", wait)
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise NotionError(r.status_code, r.text, method, path)
            return r.json()
        raise RuntimeError(f"Notion no responde tras varios reintentos: {method} {path}")

    # --- bases de datos ---

    def create_database(self, parent_page_id: str, title: str, properties: dict,
                        icon: str | None = None) -> dict:
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        }
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        return self.request("POST", "/databases", json=body)

    def update_database(self, database_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"/databases/{database_id}",
                            json={"properties": properties})

    def query_all(self, database_id: str, **filters):
        """Itera todas las paginas de una base (100 por peticion)."""
        cursor = None
        while True:
            body = {"page_size": 100, **filters}
            if cursor:
                body["start_cursor"] = cursor
            data = self.request("POST", f"/databases/{database_id}/query", json=body)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    # --- paginas ---

    def create_page(self, database_id: str, properties: dict) -> dict:
        return self.request("POST", "/pages", json={
            "parent": {"database_id": database_id},
            "properties": properties,
        })

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"/pages/{page_id}",
                            json={"properties": properties})

class NotionError(RuntimeError):
    def __init__(self, status: int, body: str, method: str, path: str):
        self.status = status
        self.body = body
        super().__init__(f"Notion {status} en {method} {path}: {body[:400]}")


# --- helpers para construir valores de propiedades ---

def title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": text[:2000]}}]}


def rich_text(text: str | None) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": (text or "")[:2000]}}]
            if text else []}


def number(value) -> dict:
    return {"number": float(value) if value is not None else None}


def date_prop(value) -> dict:
    return {"date": {"start": value.isoformat()} if value else None}


def checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


def select(value: str | None) -> dict:
    return {"select": {"name": value[:100]} if value else None}


def multi_select(values) -> dict:
    return {"multi_select": [{"name": str(v)[:100]} for v in (values or [])][:100]}


def relation(page_ids) -> dict:
    ids = [p for p in (page_ids or []) if p]
    return {"relation": [{"id": p} for p in ids]}


def upload_file(client: NotionClient, path, content_type: str = "image/png") -> str:
    """Sube un fichero a Notion y devuelve su file_upload id.

    Flujo en dos pasos: se pide un hueco de subida y luego se envia el binario
    a la URL que devuelve. Hay que adjuntarlo a un bloque en menos de 1 hora o
    caduca. Vale para ficheros de hasta 20 MB en una sola parte.
    """
    from pathlib import Path

    path = Path(path)
    slot = client.request("POST", "/file_uploads", json={
        "filename": path.name, "content_type": content_type})

    # La subida no pasa por request(): es multipart contra otra URL. Hay que
    # quitar el Content-Type de la sesion (application/json) para que requests
    # ponga el suyo con el boundary; si no, Notion responde "invalid_json".
    client.limiter.wait()
    headers = {k: v for k, v in client.session.headers.items()
               if k.lower() != "content-type"}
    with path.open("rb") as fh:
        r = requests.post(
            slot["upload_url"],
            headers=headers,
            files={"file": (path.name, fh, content_type)},
            timeout=120,
        )
    if r.status_code >= 400:
        raise NotionError(r.status_code, r.text, "POST", "upload_url")
    return slot["id"]
