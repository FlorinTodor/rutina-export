"""Lee las medidas de cinta metrica que sube la app del movil.

Health Connect no tiene ningun tipo de dato para perimetros corporales y la
bascula tampoco los mide, asi que esta es la unica via: se teclean a mano en
el movil y suben al repositorio como todo lo demas.

Una tanda por dia. Si en un mismo dia se mide dos veces, gana la ultima, que
es la correccion de la primera.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from ..models import TapeMeasurement

log = logging.getLogger(__name__)

CAMPOS = tuple(f for f in TapeMeasurement.__dataclass_fields__ if f.endswith("_cm"))


class ExportIncompleto(RuntimeError):
    """El fichero no acaba en `"fin": true`: se leyo a medio escribir."""


def load(path: str | Path) -> list[TapeMeasurement]:
    datos = json.loads(Path(path).read_text(encoding="utf-8"))

    if datos.get("error"):
        raise RuntimeError(f"La app del movil fallo: {datos['error']}")
    if not datos.get("fin"):
        raise ExportIncompleto("El JSON de medidas no esta completo")

    por_dia: dict[date, TapeMeasurement] = {}
    for fila in datos.get("medidas", []):
        d = _fecha(fila.get("day"))
        if not d:
            continue
        m = TapeMeasurement(day=d)
        for campo in CAMPOS:
            v = fila.get(campo)
            if v is None:
                continue
            try:
                m_v = float(v)
            except (TypeError, ValueError):
                continue
            # un perimetro humano no sale de este rango; fuera de el es un
            # dedazo al teclear (un 1120 en vez de 112.0) y no un dato
            if 5 <= m_v <= 300:
                setattr(m, campo, round(m_v, 1))
        nota = (fila.get("nota") or "").strip()
        if nota:
            m.nota = nota
        if m.algo_medido:
            por_dia[d] = m          # la ultima tanda del dia manda

    medidas = [por_dia[d] for d in sorted(por_dia)]
    log.info("Medidas de cinta: %d tandas", len(medidas))
    return medidas


def _fecha(v) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None
