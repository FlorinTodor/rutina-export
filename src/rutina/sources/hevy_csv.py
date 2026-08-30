"""Plan B sin Hevy Pro: parser del export CSV de la app.

El CSV es plano (una fila = una serie) y NO trae id de entreno, asi que
sintetizamos uno estable a partir de titulo + hora de inicio. Eso mantiene
la sincronizacion idempotente entre ejecuciones.

Columnas observadas en el export de Hevy:
  title, start_time, end_time, description, exercise_title, superset_id,
  exercise_notes, set_index, set_type, weight_kg, reps, distance_km,
  duration_seconds, rpe
"""

from __future__ import annotations

import csv
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from dateutil import parser as dateparser

from ..models import SetRecord, Workout

log = logging.getLogger(__name__)


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


def _dt(value: str | None) -> datetime | None:
    """Hevy ha usado varios formatos ("28 Aug 2026, 18:00", ISO...)."""
    if not value or not value.strip():
        return None
    try:
        return dateparser.parse(value.strip(), dayfirst=True)
    except (ValueError, OverflowError):
        log.warning("Fecha no reconocida en el CSV: %r", value)
        return None


def synth_id(title: str, start: datetime) -> str:
    raw = f"{title}|{start.isoformat()}".encode()
    return "csv-" + hashlib.sha1(raw).hexdigest()[:16]


def load_workouts(path: str | Path) -> list[Workout]:
    path = Path(path)
    if not path.exists():
        log.warning("No hay export CSV de Hevy en %s", path)
        return []

    workouts: dict[str, Workout] = {}
    ex_order: dict[str, dict[str, int]] = {}

    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            start = _dt(row.get("start_time"))
            if not start:
                continue
            title = (row.get("title") or "Entreno").strip()
            wid = synth_id(title, start)

            if wid not in workouts:
                workouts[wid] = Workout(
                    id=wid,
                    title=title,
                    start_time=start,
                    end_time=_dt(row.get("end_time")),
                    description=(row.get("description") or "").strip(),
                )
                ex_order[wid] = {}

            ex_title = (row.get("exercise_title") or "?").strip()
            order = ex_order[wid]
            if ex_title not in order:
                order[ex_title] = len(order)

            dist_km = _num(row.get("distance_km"))
            workouts[wid].sets.append(
                SetRecord(
                    workout_id=wid,
                    exercise_index=order[ex_title],
                    exercise_title=ex_title,
                    exercise_template_id=None,
                    set_index=_int(row.get("set_index")) or 0,
                    set_type=(row.get("set_type") or "normal").strip().lower(),
                    weight_kg=_num(row.get("weight_kg")),
                    reps=_int(row.get("reps")),
                    distance_meters=dist_km * 1000 if dist_km else None,
                    duration_seconds=_int(row.get("duration_seconds")),
                    rpe=_num(row.get("rpe")),
                    superset_id=_int(row.get("superset_id")),
                )
            )

    log.info("CSV de Hevy: %d entrenos, %d series",
             len(workouts), sum(len(w.sets) for w in workouts.values()))
    return sorted(workouts.values(), key=lambda w: w.start_time, reverse=True)
