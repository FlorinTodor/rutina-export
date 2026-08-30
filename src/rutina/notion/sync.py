"""Upsert hacia Notion.

Dos mecanismos para no duplicar ni malgastar peticiones:

  * cada fila lleva una propiedad "Sync ID" con su clave externa (id de Hevy,
    fecha, uid de serie), asi que el estado real siempre se puede reconstruir
    consultando Notion;
  * un fichero de estado local guarda {sync_id: (page_id, hash)} para saltarse
    las filas que no han cambiado. Si se pierde, se reconstruye desde Notion.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..models import BodyMeasurement, TapeMeasurement, Workout
from ..transform.metrics import DayRow, ExerciseStats
from . import client as P
from .client import NotionClient

log = logging.getLogger(__name__)

DIAS_ES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]


def _hash(props: dict) -> str:
    return hashlib.sha1(
        json.dumps(props, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return f"{self.created} creadas, {self.updated} actualizadas, {self.skipped} sin cambios"


@dataclass
class State:
    path: Path
    entries: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> State:
        path = Path(path)
        if path.exists():
            return cls(path, json.loads(path.read_text()))
        return cls(path, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=0, sort_keys=True))

    def bucket(self, name: str) -> dict[str, str]:
        return self.entries.setdefault(name, {})


class NotionSync:
    def __init__(self, client: NotionClient, db_ids: dict[str, str],
                 state_path: str | Path = "data/state/notion_sync.json"):
        self.c = client
        self.db = db_ids
        self.state = State.load(state_path)

    # ---- infraestructura de upsert ----

    def _remote_index(self, db_key: str) -> dict[str, str]:
        """Reconstruye {sync_id: page_id} preguntando a Notion."""
        index = {}
        for page in self.c.query_all(self.db[db_key]):
            texts = page.get("properties", {}).get("Sync ID", {}).get("rich_text", [])
            if texts:
                index[texts[0]["plain_text"]] = page["id"]
        log.info("Indice remoto de %s: %d filas", db_key, len(index))
        return index

    def _upsert(self, db_key: str, sync_id: str, props: dict,
                bucket: dict, stats: SyncStats) -> str:
        props["Sync ID"] = P.rich_text(sync_id)
        digest = _hash(props)
        entry = bucket.get(sync_id)

        if entry and entry.get("hash") == digest:
            stats.skipped += 1
            return entry["page_id"]

        if entry:
            self.c.update_page(entry["page_id"], props)
            page_id = entry["page_id"]
            stats.updated += 1
        else:
            page_id = self.c.create_page(self.db[db_key], props)["id"]
            stats.created += 1

        bucket[sync_id] = {"page_id": page_id, "hash": digest}
        return page_id

    def _prepare(self, db_key: str, rebuild: bool) -> dict:
        bucket = self.state.bucket(db_key)
        if rebuild or not bucket:
            remote = self._remote_index(db_key)
            for sid, pid in remote.items():
                bucket.setdefault(sid, {"page_id": pid, "hash": ""})
        return bucket

    # ---- sincronizadores por base ----

    def sync_dias(self, rows: list[DayRow], rebuild: bool = False) -> dict[date, str]:
        bucket = self._prepare("db_dias", rebuild)
        stats = SyncStats()
        page_by_day: dict[date, str] = {}

        for r in rows:
            h, b = r.health, r.body
            iso_year, iso_week, iso_dow = r.day.isocalendar()
            props = {
                "Fecha": P.title(r.day.isoformat()),
                "Dia": P.date_prop(r.day),
                "Dia semana": P.select(DIAS_ES[iso_dow - 1]),
                "Semana": P.rich_text(f"{iso_year}-W{iso_week:02d}"),
                "Entreno": P.checkbox(r.trained),
                "Volumen (kg)": P.number(r.volume_kg or None),
                "Series efectivas": P.number(r.working_sets or None),
                "Min entrenando": P.number(r.training_min or None),
                "Sesion": P.rich_text(r.workout_titles),
            }
            if h:
                props.update({
                    "Pasos": P.number(h.steps),
                    "Distancia (km)": P.number(h.distance_km),
                    "Kcal activas": P.number(h.active_kcal),
                    "Kcal totales": P.number(h.total_kcal),
                    "Pisos": P.number(h.floors),
                    "FC reposo": P.number(h.resting_hr),
                    "FC media": P.number(h.avg_hr),
                    "HRV (ms)": P.number(h.hrv_ms),
                    "SpO2 (%)": P.number(h.spo2_pct / 100 if h.spo2_pct else None),
                    "Sueno (h)": P.number(h.sleep_hours),
                    "Sueno profundo (h)": P.number(h.sleep_deep_h),
                    "Sueno REM (h)": P.number(h.sleep_rem_h),
                })
            if b:
                props.update({
                    "Peso (kg)": P.number(b.weight_kg),
                    "Grasa (%)": P.number(b.fat_percent),
                    "Musculo (kg)": P.number(b.muscle_mass_kg),
                })
            page_by_day[r.day] = self._upsert("db_dias", r.day.isoformat(),
                                              props, bucket, stats)
        log.info("Dias: %s", stats)
        return page_by_day

    def sync_ejercicios(self, stats_list: list[ExerciseStats],
                        estados: dict[str, dict] | None = None,
                        rebuild: bool = False) -> dict[str, str]:
        bucket = self._prepare("db_ejercicios", rebuild)
        stats = SyncStats()
        page_by_key: dict[str, str] = {}

        for e in stats_list:
            props = {
                "Ejercicio": P.title(e.title),
                "Grupo muscular": P.select(e.muscle_group),
                "Equipo": P.select(e.equipment),
                "Otros nombres": P.rich_text(", ".join(e.aliases)),
                "Sesiones": P.number(e.sessions),
                "Series totales": P.number(e.total_sets),
                "Volumen total (kg)": P.number(e.total_volume_kg),
                "PR peso (kg)": P.number(e.best_weight_kg),
                "PR reps": P.number(e.best_weight_reps),
                "PR e1RM (kg)": P.number(e.best_e1rm_kg),
                "Fecha PR": P.date_prop(e.best_e1rm_date),
                "Ultima vez": P.date_prop(e.last_performed),
            }
            info = (estados or {}).get(e.title)
            if info:
                props.update({
                    "Dias sin hacer": P.number(info["dias"]),
                    "Tendencia (kg)": P.number(info["tendencia"]),
                    "Estado": P.select(info["estado"]),
                })
            page_by_key[e.key] = self._upsert("db_ejercicios", e.key,
                                              props, bucket, stats)
        log.info("Ejercicios: %s", stats)
        return page_by_key

    def sync_entrenos(self, workouts: list[Workout], day_pages: dict[date, str],
                      prs: dict[str, list[str]], templates: dict[str, dict] | None = None,
                      rebuild: bool = False) -> dict[str, str]:
        bucket = self._prepare("db_entrenos", rebuild)
        stats = SyncStats()
        templates = templates or {}
        page_by_workout: dict[str, str] = {}

        for w in workouts:
            muscles = sorted({
                m for s in w.sets
                if (m := templates.get(s.exercise_template_id or "", {})
                    .get("primary_muscle_group"))
            })
            density = round(w.volume_kg / w.duration_min, 1) if w.duration_min else None
            por_serie = round(w.volume_kg / w.working_sets, 1) if w.working_sets else None
            props = {
                "Entreno": P.title(f"{w.day.isoformat()} · {w.title}"),
                "Fecha": P.date_prop(w.day),
                "Duracion (min)": P.number(w.duration_min),
                "Volumen (kg)": P.number(w.volume_kg),
                "Series efectivas": P.number(w.working_sets),
                "Reps totales": P.number(w.total_reps),
                "Ejercicios": P.number(len(w.exercises)),
                "RPE medio": P.number(w.avg_rpe),
                "Densidad (kg/min)": P.number(density),
                "Kg por serie": P.number(por_serie),
                "Musculatura": P.multi_select(muscles),
                "PRs": P.multi_select(prs.get(w.id, [])),
                "Notas": P.rich_text(w.description),
            }
            if w.day in day_pages:
                props["Dia"] = P.relation([day_pages[w.day]])
            page_by_workout[w.id] = self._upsert("db_entrenos", w.id,
                                                 props, bucket, stats)
        log.info("Entrenos: %s", stats)
        return page_by_workout

    def sync_series(self, workouts: list[Workout], workout_pages: dict[str, str],
                    exercise_pages: dict[str, str], templates: dict[str, dict] | None = None,
                    skip_warmups: bool = True, rebuild: bool = False) -> None:
        bucket = self._prepare("db_series", rebuild)
        stats = SyncStats()
        templates = templates or {}

        for w in workouts:
            for s in w.sets:
                if skip_warmups and s.set_type == "warmup":
                    continue
                name = (templates.get(s.exercise_template_id or "", {}).get("title")
                        or s.exercise_title)
                label = f"{name} · {s.weight_kg or 0:g}kg × {s.reps or 0}"
                props = {
                    "Serie": P.title(label),
                    "Fecha": P.date_prop(w.day),
                    "Ejercicio (texto)": P.rich_text(name),
                    "Nº serie": P.number(s.set_index + 1),
                    "Tipo": P.select(s.set_type),
                    "Peso (kg)": P.number(s.weight_kg),
                    "Reps": P.number(s.reps),
                    "RPE": P.number(s.rpe),
                    "Volumen (kg)": P.number(s.volume_kg or None),
                    "e1RM (kg)": P.number(s.e1rm_kg),
                }
                if w.id in workout_pages:
                    props["Entreno"] = P.relation([workout_pages[w.id]])
                if s.exercise_key in exercise_pages:
                    props["Ejercicio"] = P.relation([exercise_pages[s.exercise_key]])
                self._upsert("db_series", s.uid, props, bucket, stats)
        log.info("Series: %s", stats)

    def sync_medidas(self, body: list[BodyMeasurement], day_pages: dict[date, str],
                     tape: list[TapeMeasurement] | None = None,
                     rebuild: bool = False) -> None:
        """Pesajes y cinta metrica en la misma base, unidos por la fecha.

        Comparten tabla porque responden a la misma pregunta y la fecha los
        une; separarlas obligaria a mirar dos sitios. No siempre coinciden: un
        dia puede tener solo pesaje, solo cinta, o las dos cosas, y entonces
        la fila trae lo que haya y el resto en blanco.
        """
        bucket = self._prepare("db_medidas", rebuild)
        stats = SyncStats()

        por_dia: dict[date, tuple[BodyMeasurement | None, TapeMeasurement | None]] = {}
        for b in body:
            por_dia[b.day] = (b, None)
        for t in tape or []:
            por_dia[t.day] = (por_dia.get(t.day, (None, None))[0], t)

        for dia in sorted(por_dia):
            b, t = por_dia[dia]
            props = {
                "Pesaje": P.title(dia.isoformat()),
                "Fecha": P.date_prop(dia),
            }
            if b:
                props.update({
                    "Peso (kg)": P.number(b.weight_kg),
                    "Grasa (%)": P.number(b.fat_percent),
                    "Musculo (kg)": P.number(b.muscle_mass_kg),
                    "Masa magra (kg)": P.number(b.lean_mass_kg),
                    "Masa osea (kg)": P.number(b.bone_mass_kg),
                    "Agua (%)": P.number(b.water_percent),
                    "Grasa visceral": P.number(b.visceral_fat),
                    "IMC": P.number(b.bmi),
                    "Metabolismo basal (kcal)": P.number(b.bmr_kcal),
                    "Grasa (kg)": P.number(b.fat_mass_kg),
                    "Grasa subcutanea (%)": P.number(b.subcutaneous_fat_percent),
                    "Musculo esqueletico (%)": P.number(b.skeletal_muscle_percent),
                    "Proteina (%)": P.number(b.protein_percent),
                    "Edad corporal": P.number(b.body_age),
                })
            if t:
                props.update({
                    "Cuello (cm)": P.number(t.cuello_cm),
                    "Pecho (cm)": P.number(t.pecho_cm),
                    "Cintura (cm)": P.number(t.cintura_cm),
                    "Abdomen (cm)": P.number(t.abdomen_cm),
                    "Cadera (cm)": P.number(t.cadera_cm),
                    "Brazo izq (cm)": P.number(t.brazo_izq_cm),
                    "Brazo der (cm)": P.number(t.brazo_der_cm),
                    "Antebrazo izq (cm)": P.number(t.antebrazo_izq_cm),
                    "Antebrazo der (cm)": P.number(t.antebrazo_der_cm),
                    "Muslo izq (cm)": P.number(t.muslo_izq_cm),
                    "Muslo der (cm)": P.number(t.muslo_der_cm),
                    "Gemelo izq (cm)": P.number(t.gemelo_izq_cm),
                    "Gemelo der (cm)": P.number(t.gemelo_der_cm),
                })
                if t.nota:
                    props["Nota"] = P.rich_text(t.nota)
            if dia in day_pages:
                props["Dia"] = P.relation([day_pages[dia]])
            self._upsert("db_medidas", dia.isoformat(), props, bucket, stats)
        log.info("Medidas: %s", stats)

    def save_state(self) -> None:
        self.state.save()
