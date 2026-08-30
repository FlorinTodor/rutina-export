"""Lee el JSON que deja la app propia (android/) en el movil.

Sustituye a `health_sheets.py` sin pasar por Google Sheets: no hay hoja de
calculo, ni service account, ni suscripcion de 0,99 EUR para que la
exportacion sea automatica.

La app es deliberadamente tonta y entrega los datos casi crudos. Todas las
decisiones viven aqui, que es donde se pueden cambiar sin recompilar:

  * A que dia pertenece una noche de sueno. Health Connect solo sabe cuando
    empezo y cuando acabo; que la noche del 29 al 30 sea "sueno del 30" es
    una convencion, y ademas tiene que coincidir con la que traia la hoja
    para no partir el historico en dos. Por defecto manda el dia en que te
    despiertas, que es lo que hacen Samsung Health y la hoja.
  * El agua corporal: Health Connect la da en kg y el modelo la quiere en %.
  * El IMC y la masa grasa, que no los mide nadie y se derivan.

Los totales diarios (pasos, distancia, calorias) NO necesitan el apano de
`health_sheets.py` de tomar el maximo por fecha: la app ya los pide con
`aggregateGroupByPeriod`, y ahi Health Connect deduplica por su lista de
prioridad antes de sumar.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from ..models import BodyMeasurement, DailyHealth

log = logging.getLogger(__name__)

# campo en el JSON -> campo del modelo, para los totales diarios
DIARIOS = ("steps", "distance_km", "active_kcal", "total_kcal", "floors",
           "resting_hr", "avg_hr", "max_hr")
ENTEROS = ("steps", "resting_hr", "avg_hr", "max_hr")

# medidas puntuales que se promedian por dia
PUNTUALES = {"hrv_ms": "avg", "spo2_pct": "avg", "vo2max": "max"}


class ExportIncompleto(RuntimeError):
    """El fichero no acaba en `"fin": true`: se leyo a medio escribir."""


def load(path: str | Path, sueno_por: str = "fin") -> tuple[list[DailyHealth],
                                                            list[BodyMeasurement]]:
    datos = json.loads(Path(path).read_text(encoding="utf-8"))

    if datos.get("error"):
        raise RuntimeError(f"La app del movil fallo: {datos['error']}")
    if not datos.get("fin"):
        raise ExportIncompleto(
            "El JSON no esta completo. Si se copio mientras la app escribia, "
            "vuelve a lanzarla; si no, mira el fallo con: adb logcat -d -s rutina")

    # De que apps depende de verdad el pipeline. Util para saber cual se
    # puede desinstalar: si una no aparece aqui, no escribe nada que usemos.
    if datos.get("origenes"):
        for tipo, apps in sorted(datos["origenes"].items()):
            log.debug("%s <- %s", tipo, ", ".join(sorted(apps)))

    if datos.get("faltan"):
        log.warning("Sin permiso en Health Connect para %d tipos: %s",
                    len(datos["faltan"]),
                    ", ".join(p.rsplit(".", 1)[-1] for p in datos["faltan"]))

    dias: dict[date, DailyHealth] = {}

    def dia(d: date) -> DailyHealth:
        return dias.setdefault(d, DailyHealth(day=d))

    # --- totales diarios, ya agregados por Health Connect ---
    for fila in datos.get("dias", []):
        d = _fecha(fila.get("day"))
        if not d:
            continue
        h = dia(d)
        for campo in DIARIOS:
            v = fila.get(campo)
            if v is None:
                continue
            setattr(h, campo, int(round(v)) if campo in ENTEROS else round(float(v), 2))

    # --- sueno: cada sesion cae entera en el dia que diga la convencion ---
    minutos: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for s in datos.get("suenos", []):
        inicio, fin = _hora(s.get("inicio")), _hora(s.get("fin"))
        if not inicio or not fin:
            continue
        d = (fin if sueno_por == "fin" else inicio).date()
        partes = minutos[d]
        desglosado = False
        for origen, destino in (("deep_min", "sleep_deep_h"), ("rem_min", "sleep_rem_h"),
                                ("light_min", "sleep_light_h"), ("awake_min", "sleep_awake_h")):
            v = s.get(origen)
            if v:
                partes[destino] += float(v)
                desglosado = True
        # una siesta o un reloj sin fases no trae desglose: al menos el total
        if not desglosado and s.get("total_min"):
            partes["_sin_fases"] += float(s["total_min"])

    for d, partes in minutos.items():
        h = dia(d)
        for campo, m in partes.items():
            if not campo.startswith("_"):
                setattr(h, campo, round(m / 60, 2))
        # el rato despierto en la cama no es sueno
        dormido = sum(m for c, m in partes.items()
                      if c not in ("sleep_awake_h", "_sin_fases")) + partes.get("_sin_fases", 0)
        h.sleep_hours = round(dormido / 60, 2) if dormido else None

    # --- medidas sueltas: se resumen por dia ---
    sueltas: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for p in datos.get("puntos", []):
        cuando = _hora(p.get("cuando"))
        campo, valor = p.get("campo"), p.get("valor")
        if cuando and campo in PUNTUALES and valor is not None:
            sueltas[cuando.date()][campo].append(float(valor))
    for d, campos in sueltas.items():
        h = dia(d)
        for campo, vals in campos.items():
            v = max(vals) if PUNTUALES[campo] == "max" else sum(vals) / len(vals)
            setattr(h, campo, round(v, 1))

    cuerpo = _pesajes(datos.get("cuerpo", []))
    log.info("App del movil: %d dias, %d pesajes (%s a %s)",
             len(dias), len(cuerpo), datos.get("desde"), datos.get("hasta"))
    return [dias[d] for d in sorted(dias)], cuerpo


def _pesajes(medidas: list[dict]) -> list[BodyMeasurement]:
    """Agrupa las medidas por dia. Dentro de un dia, la mas reciente manda."""
    por_dia: dict[date, BodyMeasurement] = {}
    agua: dict[date, float] = {}
    alturas: dict[date, float] = {}
    altura_ultima: float | None = None

    for m in sorted(medidas, key=lambda x: str(x.get("cuando", ""))):
        cuando = _hora(m.get("cuando"))
        campo, valor = m.get("campo"), m.get("valor")
        if not cuando or campo is None or valor is None:
            continue
        d = cuando.date()

        # la altura es del perfil, no del pesaje: se mide una vez y vale para
        # los dias que vengan despues
        if campo == "height_m":
            altura_ultima = float(valor)
            alturas[d] = altura_ultima
            continue

        p = por_dia.setdefault(d, BodyMeasurement(day=d, measured_at=cuando))
        p.measured_at = max(p.measured_at or cuando, cuando)
        if campo == "water_kg":
            agua[d] = float(valor)
        else:
            setattr(p, campo, round(float(valor), 2))

    for d, p in por_dia.items():
        # Health Connect da el agua en kg y el modelo la guarda en %
        if p.weight_kg and agua.get(d):
            p.water_percent = round(agua[d] / p.weight_kg * 100, 1)
        if p.muscle_mass_kg is None and p.lean_mass_kg is not None:
            p.muscle_mass_kg = (round(p.lean_mass_kg - p.bone_mass_kg, 2)
                                if p.bone_mass_kg else p.lean_mass_kg)
        h = alturas.get(d, altura_ultima)
        if h:
            p.height_m = round(h, 3)
            if p.weight_kg:
                p.bmi = round(p.weight_kg / (h ** 2), 1)
        if p.weight_kg and p.fat_percent:
            p.fat_mass_kg = round(p.weight_kg * p.fat_percent / 100, 2)

    return [por_dia[d] for d in sorted(por_dia)]


def _hora(v) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def _fecha(v) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None
