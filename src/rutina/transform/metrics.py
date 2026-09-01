"""Cruza entrenos y datos de salud, y deriva lo que ninguna fuente da hecho:
volumen diario, PRs por ejercicio y medias moviles."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from ..models import BodyMeasurement, DailyHealth, Workout, TapeMeasurement


@dataclass
class ExerciseStats:
    """Resumen historico de un ejercicio: para la base Ejercicios de Notion."""

    key: str
    title: str
    template_id: str | None = None
    muscle_group: str | None = None
    equipment: str | None = None
    aliases: list[str] = field(default_factory=list)
    sessions: int = 0
    total_sets: int = 0
    total_volume_kg: float = 0.0
    best_weight_kg: float | None = None
    best_weight_reps: int | None = None
    best_weight_date: date | None = None
    best_e1rm_kg: float | None = None
    best_e1rm_date: date | None = None
    last_performed: date | None = None


@dataclass
class DayRow:
    """Una fila de la base Dias: salud + entreno del mismo dia."""

    day: date
    health: DailyHealth | None = None
    body: BodyMeasurement | None = None
    tape: TapeMeasurement | None = None
    workouts: list[Workout] = field(default_factory=list)

    @property
    def trained(self) -> bool:
        return bool(self.workouts)

    @property
    def volume_kg(self) -> float:
        return round(sum(w.volume_kg for w in self.workouts), 1)

    @property
    def training_min(self) -> int:
        return sum(w.duration_min or 0 for w in self.workouts)

    @property
    def working_sets(self) -> int:
        return sum(w.working_sets for w in self.workouts)

    @property
    def workout_titles(self) -> str:
        return " + ".join(w.title for w in self.workouts)


def build_day_rows(
    workouts: list[Workout],
    health: list[DailyHealth],
    body: list[BodyMeasurement],
    tape: list[TapeMeasurement] | None = None,
) -> list[DayRow]:
    rows: dict[date, DayRow] = {}

    def row(day: date) -> DayRow:
        return rows.setdefault(day, DayRow(day=day))

    for h in health:
        row(h.day).health = h
    for b in body:
        row(b.day).body = b
    for t in tape or []:
        row(t.day).tape = t
    for w in workouts:
        row(w.day).workouts.append(w)

    return [rows[d] for d in sorted(rows)]


def build_exercise_stats(
    workouts: list[Workout], templates: dict[str, dict] | None = None
) -> list[ExerciseStats]:
    """Agrega por `exercise_key`, no por titulo: un mismo ejercicio puede
    aparecer con varios nombres si se cambio el idioma de la app."""
    templates = templates or {}
    stats: dict[str, ExerciseStats] = {}
    seen_sessions: dict[str, set[date]] = defaultdict(set)
    seen_titles: dict[str, set[str]] = defaultdict(set)

    for w in sorted(workouts, key=lambda x: x.start_time):
        for s in w.sets:
            key = s.exercise_key
            tpl = templates.get(s.exercise_template_id or "", {})
            st = stats.setdefault(
                key,
                ExerciseStats(
                    key=key,
                    title=tpl.get("title") or s.exercise_title,
                    template_id=s.exercise_template_id,
                    muscle_group=tpl.get("primary_muscle_group"),
                    equipment=tpl.get("equipment"),
                ),
            )
            seen_titles[key].add(s.exercise_title)
            if s.set_type == "warmup":
                continue
            st.total_sets += 1
            st.total_volume_kg = round(st.total_volume_kg + s.volume_kg, 1)
            st.last_performed = w.day
            seen_sessions[key].add(w.day)

            # A igualdad de peso gana la de mas repeticiones: 70x12 es mejor
            # serie que 70x10 con el mismo disco. Con ">" a secas se quedaba
            # la primera que apareciera, que solia ser la peor.
            if s.weight_kg and ((s.weight_kg, s.reps or 0)
                                > (st.best_weight_kg or 0, st.best_weight_reps or 0)):
                st.best_weight_kg = s.weight_kg
                st.best_weight_reps = s.reps
                st.best_weight_date = w.day

            e1rm = s.e1rm_kg
            if e1rm and (st.best_e1rm_kg is None or e1rm > st.best_e1rm_kg):
                st.best_e1rm_kg = e1rm
                st.best_e1rm_date = w.day

    for key, st in stats.items():
        st.sessions = len(seen_sessions[key])
        st.aliases = sorted(seen_titles[key] - {st.title})
    return sorted(stats.values(), key=lambda s: s.total_volume_kg, reverse=True)


def find_prs(
    workouts: list[Workout], templates: dict[str, dict] | None = None
) -> dict[str, list[str]]:
    """Devuelve, por workout_id, los ejercicios en los que ese dia se batio el
    record de e1RM. Compara por `exercise_key` para que un cambio de idioma no
    reinicie el record."""
    templates = templates or {}
    best: dict[str, float] = {}
    names: dict[str, str] = {}
    prs: dict[str, list[str]] = defaultdict(list)

    for w in sorted(workouts, key=lambda x: x.start_time):
        day_best: dict[str, float] = {}
        for s in w.sets:
            e = s.e1rm_kg
            if e and e > day_best.get(s.exercise_key, 0):
                day_best[s.exercise_key] = e
                tpl = templates.get(s.exercise_template_id or "", {})
                names[s.exercise_key] = tpl.get("title") or s.exercise_title
        for key, e in day_best.items():
            if e > best.get(key, 0):
                if key in best:  # el primer registro es linea base, no un PR
                    prs[w.id].append(names[key])
                best[key] = e
    return dict(prs)


