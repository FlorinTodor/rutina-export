"""Acumula el histórico de salud en local.

Health Data Export REEMPLAZA el contenido de la hoja en cada exportación, no
lo acumula: si la exportación automática corre con rango "Last day", la hoja
pasa a contener un único día. Sin esto, el histórico de pasos, sueño y peso
se perdería en la siguiente sincronización.

El repositorio es la fuente de verdad; la hoja es solo la ventana más
reciente. Lo que llega de la hoja pisa lo que había para esa misma fecha
(un dato reexportado es más fiable que uno viejo), y las fechas que la hoja
ya no cubre se conservan intactas.
"""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path

from .models import BodyMeasurement, DailyHealth

log = logging.getLogger(__name__)

RAW = Path("data/raw")


def _revive(cls, row: dict):
    """Reconstruye la dataclass convirtiendo las fechas de vuelta."""
    kinds = {f.name: f.type for f in fields(cls)}
    out = {}
    for k, v in row.items():
        if k not in kinds or v is None:
            out[k] = v
            continue
        if k == "day":
            out[k] = date.fromisoformat(v) if isinstance(v, str) else v
        elif k == "measured_at":
            out[k] = datetime.fromisoformat(v) if isinstance(v, str) else v
        else:
            out[k] = v
    return cls(**out)


def _load(path: Path, cls) -> dict[date, object]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = _revive(cls, json.loads(line))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("Fila ilegible en %s: %s", path.name, exc)
            continue
        out[item.day] = item
    return out


def merge(fresh: list, cls, filename: str) -> list:
    """Funde lo recién leído con lo ya guardado y devuelve el histórico entero."""
    path = RAW / filename
    stored = _load(path, cls)
    before = len(stored)
    for item in fresh:
        stored[item.day] = item          # lo nuevo manda para esa fecha
    merged = [stored[d] for d in sorted(stored)]
    if merged:
        kept = before - len(set(i.day for i in fresh) & set(_load(path, cls)))
        log.info("%s: %d dias en la hoja, %d en el histórico%s",
                 filename, len(fresh), len(merged),
                 f" ({len(merged) - len(fresh)} conservados de antes)"
                 if len(merged) > len(fresh) else "")
    return merged


def merge_fields(fresh: list, cls, filename: str) -> list:
    """Como `merge`, pero campo a campo en vez de reemplazar la fila entera.

    El export de FitDays trae 15 métricas y Health Connect solo 5, pero puede
    que en una fecha concreta una fuente tenga algo que la otra no. Se rellena
    hueco a hueco en vez de perder datos.
    """
    path = RAW / filename
    stored = _load(path, cls)
    for item in fresh:
        old = stored.get(item.day)
        if old is None:
            stored[item.day] = item
            continue
        for f in fields(cls):
            new = getattr(item, f.name, None)
            if new is not None:
                setattr(old, f.name, new)   # lo nuevo manda si trae dato
    return [stored[d] for d in sorted(stored)]
