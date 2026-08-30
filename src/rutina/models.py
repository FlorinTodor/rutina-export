"""Modelo normalizado. Cada fuente traduce a estas estructuras y el resto
del pipeline no vuelve a saber de dónde vinieron los datos."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, asdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

# Hevy devuelve las horas en UTC. Sin convertir, un entreno empezado a la
# 01:00 de Madrid (23:00 UTC del dia anterior) se guardaria en el dia que no
# es. Ajustable con RUTINA_TZ por si algun dia cambias de pais.
LOCAL_TZ = ZoneInfo(os.environ.get("RUTINA_TZ", "Europe/Madrid"))


def to_local(value: datetime | None) -> datetime | None:
    """Pasa a hora local. Lo que llega sin zona ya viene en local."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(LOCAL_TZ)


@dataclass
class SetRecord:
    """Una serie concreta dentro de un ejercicio."""

    workout_id: str
    exercise_index: int
    exercise_title: str
    exercise_template_id: str | None
    set_index: int
    set_type: str  # normal | warmup | dropset | failure
    weight_kg: float | None
    reps: int | None
    distance_meters: float | None
    duration_seconds: int | None
    rpe: float | None
    superset_id: int | None = None

    @property
    def volume_kg(self) -> float:
        """Las series de calentamiento no cuentan para el volumen."""
        if self.set_type == "warmup" or not self.weight_kg or not self.reps:
            return 0.0
        return round(self.weight_kg * self.reps, 2)

    @property
    def e1rm_kg(self) -> float | None:
        """1RM estimado (Epley). Sin sentido por encima de ~12 reps."""
        if not self.weight_kg or not self.reps or self.set_type == "warmup":
            return None
        if self.reps > 12:
            return None
        return round(self.weight_kg * (1 + self.reps / 30), 2)

    @property
    def uid(self) -> str:
        return f"{self.workout_id}:{self.exercise_index}:{self.set_index}"

    @property
    def exercise_key(self) -> str:
        """Clave estable del ejercicio.

        El titulo NO sirve: Hevy congela en cada entreno el nombre traducido
        del momento, asi que cambiar el idioma de la app parte el historial de
        un ejercicio en dos. El template_id es inmutable.
        """
        return self.exercise_template_id or f"title:{self.exercise_title}"


@dataclass
class Workout:
    id: str
    title: str
    start_time: datetime
    end_time: datetime | None
    description: str = ""
    routine_id: str | None = None
    updated_at: datetime | None = None
    sets: list[SetRecord] = field(default_factory=list)

    @property
    def local_start(self) -> datetime:
        return to_local(self.start_time)

    @property
    def local_end(self) -> datetime | None:
        return to_local(self.end_time)

    @property
    def day(self) -> date:
        """El dia en hora local, no en UTC."""
        return self.local_start.date()

    @property
    def duration_min(self) -> int | None:
        if not self.end_time:
            return None
        return round((self.end_time - self.start_time).total_seconds() / 60)

    @property
    def volume_kg(self) -> float:
        return round(sum(s.volume_kg for s in self.sets), 1)

    @property
    def working_sets(self) -> int:
        return sum(1 for s in self.sets if s.set_type != "warmup")

    @property
    def total_reps(self) -> int:
        return sum(s.reps or 0 for s in self.sets if s.set_type != "warmup")

    @property
    def exercises(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.sets:
            seen.setdefault(s.exercise_title, None)
        return list(seen)

    @property
    def avg_rpe(self) -> float | None:
        vals = [s.rpe for s in self.sets if s.rpe and s.set_type != "warmup"]
        return round(sum(vals) / len(vals), 1) if vals else None


@dataclass
class DailyHealth:
    """Una fila por día, procedente de Health Connect."""

    day: date
    steps: int | None = None
    distance_km: float | None = None
    active_kcal: float | None = None
    total_kcal: float | None = None
    floors: float | None = None
    resting_hr: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    hrv_ms: float | None = None
    spo2_pct: float | None = None
    sleep_hours: float | None = None
    sleep_deep_h: float | None = None
    sleep_rem_h: float | None = None
    sleep_light_h: float | None = None
    sleep_awake_h: float | None = None
    vo2max: float | None = None


@dataclass
class BodyMeasurement:
    """Un pesaje de la báscula FitDays."""

    day: date
    measured_at: datetime | None = None
    weight_kg: float | None = None
    fat_percent: float | None = None
    muscle_mass_kg: float | None = None
    lean_mass_kg: float | None = None
    bone_mass_kg: float | None = None
    water_percent: float | None = None
    visceral_fat: float | None = None
    bmi: float | None = None
    bmr_kcal: float | None = None
    protein_percent: float | None = None
    # solo llegan por el export propio de FitDays: Health Connect no tiene
    # tipo de dato para ellas y se pierden por el camino automatico
    fat_mass_kg: float | None = None       # derivado: peso x grasa%
    height_m: float | None = None
    subcutaneous_fat_percent: float | None = None
    skeletal_muscle_percent: float | None = None
    body_age: float | None = None
    heart_rate: int | None = None


@dataclass
class TapeMeasurement:
    """Medidas con cinta metrica, tomadas a mano.

    No las da la bascula ni Health Connect: Health Connect no tiene ningun
    tipo de dato para perimetros corporales. Se meten desde la app del movil.

    Estan todas las de una tanda estandar aunque hoy solo se usen dos, porque
    anadir un campo mas adelante obliga a tocar el modelo, la app, el importador
    y el dashboard. Lo que no se mide se queda a None y no se pinta en ningun
    sitio: el dashboard descarta las metricas sin ningun valor.

    Izquierda y derecha por separado en las extremidades: la asimetria es
    justo lo que se quiere ver, y promediarlas la esconderia.
    """

    day: date
    cuello_cm: float | None = None
    pecho_cm: float | None = None
    cintura_cm: float | None = None
    abdomen_cm: float | None = None
    cadera_cm: float | None = None
    brazo_izq_cm: float | None = None
    brazo_der_cm: float | None = None
    antebrazo_izq_cm: float | None = None
    antebrazo_der_cm: float | None = None
    muslo_izq_cm: float | None = None
    muslo_der_cm: float | None = None
    gemelo_izq_cm: float | None = None
    gemelo_der_cm: float | None = None
    nota: str | None = None

    @property
    def algo_medido(self) -> bool:
        return any(getattr(self, f.name) is not None
                   for f in fields(self) if f.name.endswith("_cm"))


def to_jsonable(obj: Any) -> Any:
    """asdict + fechas en ISO, para volcar a JSONL."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, list):
        return [to_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return to_jsonable(asdict(obj))
    return obj
