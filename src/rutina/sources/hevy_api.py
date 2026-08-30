"""Cliente de la API oficial de Hevy (requiere Hevy Pro).

Spec: https://api.hevyapp.com/docs/  ·  auth por cabecera `api-key`.
Ojo: /v1/workouts tiene pageSize maximo 10, de ahi la paginacion agresiva.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from ..models import SetRecord, Workout

log = logging.getLogger(__name__)

BASE = "https://api.hevyapp.com/v1"
MAX_PAGE_SIZE = 10


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HevyAPI:
    def __init__(self, api_key: str, timeout: int = 30):
        if not api_key:
            raise ValueError("Falta la API key de Hevy (requiere suscripcion Pro)")
        self.session = requests.Session()
        self.session.headers.update({"api-key": api_key, "Accept": "application/json"})
        self.timeout = timeout

    def _get(self, path: str, **params) -> dict:
        url = f"{BASE}{path}"
        for attempt in range(5):
            r = self.session.get(url, params=params, timeout=self.timeout)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                log.warning("Hevy rate limit, esperando %ss", wait)
                time.sleep(wait)
                continue
            if r.status_code == 401:
                raise PermissionError(
                    "Hevy devuelve 401: la API key no es valida o la suscripcion Pro no esta activa."
                )
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Hevy no responde tras varios reintentos: {path}")

    def user_info(self) -> dict:
        """La API envuelve la respuesta en {"data": {...}}, la spec no lo refleja."""
        raw = self._get("/user/info")
        return raw.get("data", raw)

    def workout_count(self) -> int:
        return int(self._get("/workouts/count").get("workout_count", 0))

    def iter_workouts(self, page_size: int = MAX_PAGE_SIZE):
        """Recorre el historico completo, de mas reciente a mas antiguo."""
        page = 1
        while True:
            data = self._get("/workouts", page=page, pageSize=min(page_size, MAX_PAGE_SIZE))
            items = data.get("workouts", [])
            for raw in items:
                yield parse_workout(raw)
            if page >= int(data.get("page_count", 1)) or not items:
                return
            page += 1

    def iter_events(self, since: datetime, page_size: int = MAX_PAGE_SIZE):
        """Sincronizacion incremental: entrenos creados/editados/borrados desde `since`.

        Devuelve tuplas (tipo, payload) con tipo en {"updated", "deleted"}.
        """
        page = 1
        since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        while True:
            data = self._get(
                "/workouts/events",
                since=since_str,
                page=page,
                pageSize=min(page_size, MAX_PAGE_SIZE),
            )
            events = data.get("events", [])
            for ev in events:
                kind = ev.get("type")
                if kind == "updated" and ev.get("workout"):
                    yield "updated", parse_workout(ev["workout"])
                elif kind == "deleted":
                    yield "deleted", ev.get("id") or ev.get("workout_id")
            if page >= int(data.get("page_count", 1)) or not events:
                return
            page += 1

    def exercise_templates(self) -> dict[str, dict]:
        """Catalogo completo {template_id: {title, primary_muscle_group, ...}}.

        Es la unica fuente del nombre canonico de un ejercicio.
        """
        out: dict[str, dict] = {}
        page = 1
        while True:
            data = self._get("/exercise_templates", page=page, pageSize=100)
            items = data.get("exercise_templates", [])
            for t in items:
                if t.get("id"):
                    out[t["id"]] = t
            if page >= int(data.get("page_count", 1)) or not items:
                return out
            page += 1

    def body_measurements(self) -> list[dict]:
        out, page = [], 1
        while True:
            data = self._get("/body_measurements", page=page, pageSize=10)
            items = data.get("body_measurements", [])
            out.extend(items)
            if page >= int(data.get("page_count", 1)) or not items:
                return out
            page += 1


def parse_workout(raw: dict) -> Workout:
    sets: list[SetRecord] = []
    for ex in raw.get("exercises") or []:
        for st in ex.get("sets") or []:
            sets.append(
                SetRecord(
                    workout_id=raw["id"],
                    exercise_index=ex.get("index", 0),
                    exercise_title=ex.get("title", "?"),
                    exercise_template_id=ex.get("exercise_template_id"),
                    set_index=st.get("index", 0),
                    set_type=st.get("type") or "normal",
                    weight_kg=st.get("weight_kg"),
                    reps=st.get("reps"),
                    distance_meters=st.get("distance_meters"),
                    duration_seconds=st.get("duration_seconds"),
                    rpe=st.get("rpe"),
                    # la spec dice "supersets_id" pero la API devuelve "superset_id"
                    superset_id=ex.get("superset_id", ex.get("supersets_id")),
                )
            )
    return Workout(
        id=raw["id"],
        title=raw.get("title") or "Entreno",
        start_time=_parse_dt(raw.get("start_time")),
        end_time=_parse_dt(raw.get("end_time")),
        description=raw.get("description") or "",
        routine_id=raw.get("routine_id"),
        updated_at=_parse_dt(raw.get("updated_at")),
        sets=sets,
    )
