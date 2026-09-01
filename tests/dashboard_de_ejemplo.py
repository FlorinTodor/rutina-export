#!/usr/bin/env python3
"""Genera un dashboard con datos inventados, para poder probarlo sin datos reales.

El test del dashboard necesita un HTML ya renderizado. En el repositorio de
alguien que lo use, ese HTML lo produce el workflow a partir de su historial;
aqui no hay historial ninguno, y no debe haberlo. Asi que se fabrica una serie
con una tendencia y ruido, se renderiza y se prueba eso.

Prueba lo mismo que importa: la plantilla, el payload y que la pagina se
ejecute en un navegador sin errores. Que los numeros sean inventados da igual.

    python tests/dashboard_de_ejemplo.py [salida.html]
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rutina.dashboard import build_payload, render          # noqa: E402
from rutina.models import BodyMeasurement, DailyHealth, SetRecord, Workout  # noqa: E402
from rutina.transform.metrics import build_day_rows, build_exercise_stats   # noqa: E402

EJERCICIOS = [
    ("EJ01", "Bench Press (Barbell)", "chest", "barbell"),
    ("EJ02", "Lat Pulldown (Cable)", "lats", "machine"),
    ("EJ03", "Squat (Barbell)", "quadriceps", "barbell"),
    ("EJ04", "Lateral Raise (Dumbbell)", "shoulders", "dumbbell"),
    # Dos que no se miden en kilos, para que la demo ensene lo que hace el
    # panel con ellos: las flexiones se cuentan en repeticiones y la comba en
    # minutos. Con solo barras y maquinas esas ramas no se verian nunca.
    ("EJ05", "Push Up", "chest", "none"),
    ("EJ06", "Jump Rope", "cardio", "none"),
]
PESADOS = EJERCICIOS[:4]


def fabricar(dias: int = 56, semilla: int = 7):
    r = random.Random(semilla)
    inicio = date.today() - timedelta(days=dias)
    workouts, salud, cuerpo = [], [], []
    peso, grasa = 82.4, 22.8

    for i in range(dias):
        d = inicio + timedelta(days=i)
        entrena = d.weekday() in (0, 2, 4) and r.random() > 0.12

        if entrena:
            series = []
            for idx, (eid, titulo, musculo, _) in enumerate(PESADOS):
                base = 60 + idx * 12 + i * 0.18
                # una sesion de cada cinco se va a repeticiones altas: por
                # encima de 12 el 1RM estimado no se puede calcular, y el panel
                # tiene que seguir ensenando la carga del dia sin el
                altas = idx == 1 and r.random() < 0.2
                for s in range(3):
                    series.append(SetRecord(
                        workout_id=f"W{i}", exercise_index=idx, exercise_title=titulo,
                        exercise_template_id=eid, set_index=s, set_type="normal",
                        weight_kg=round(base + r.uniform(-4, 4), 1),
                        reps=r.randint(14, 18) if altas else r.randint(6, 12),
                        distance_meters=None,
                        duration_seconds=None, rpe=r.choice([7.0, 8.0, 8.5, None])))

            # peso corporal: hay repeticiones y no hay kilos
            if r.random() < 0.55:
                for s in range(3):
                    series.append(SetRecord(
                        workout_id=f"W{i}", exercise_index=4, exercise_title="Push Up",
                        exercise_template_id="EJ05", set_index=s, set_type="normal",
                        weight_kg=None, reps=r.randint(10, 22), distance_meters=None,
                        duration_seconds=None, rpe=None))

            # por tiempo: ni kilos ni repeticiones, solo minutos
            if r.random() < 0.35:
                series.append(SetRecord(
                    workout_id=f"W{i}", exercise_index=5, exercise_title="Jump Rope",
                    exercise_template_id="EJ06", set_index=0, set_type="normal",
                    weight_kg=None, reps=None, distance_meters=None,
                    duration_seconds=60 * r.randint(6, 15), rpe=None))
            inicio_dt = datetime.combine(d, datetime.min.time()).replace(hour=18)
            workouts.append(Workout(
                id=f"W{i}", title="Sesión de ejemplo", start_time=inicio_dt,
                end_time=inicio_dt + timedelta(minutes=r.randint(50, 85)), sets=series))

        # las fases tienen que sumar el total: la grafica de sueno las apila y
        # sin ellas sale vacia aunque haya horas dormidas
        total = max(4.2, r.gauss(7.0, 0.8))
        prof = total * r.uniform(0.16, 0.24)
        rem = total * r.uniform(0.18, 0.26)
        salud.append(DailyHealth(
            day=d,
            steps=max(1200, int(r.gauss(11000 if entrena else 7000, 2600))),
            total_kcal=round(r.gauss(2750 if entrena else 2180, 190), 1),
            sleep_hours=round(total, 2),
            sleep_deep_h=round(prof, 2),
            sleep_rem_h=round(rem, 2),
            sleep_light_h=round(total - prof - rem, 2),
            sleep_awake_h=round(r.uniform(0.2, 0.9), 2),
            resting_hr=r.randint(52, 62)))

        if d.weekday() == 6:
            peso -= r.uniform(0.15, 0.55)
            grasa -= r.uniform(0.05, 0.28)
            cuerpo.append(BodyMeasurement(
                day=d, weight_kg=round(peso, 1), fat_percent=round(grasa, 1),
                muscle_mass_kg=round(peso * 0.46, 1), bmr_kcal=1980))

    return workouts, salud, cuerpo


def main() -> int:
    salida = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/dashboard/index.html")
    workouts, salud, cuerpo = fabricar()
    filas = build_day_rows(workouts, salud, cuerpo)
    plantillas = {e[0]: {"title": e[1], "primary_muscle_group": e[2], "equipment": e[3]}
                  for e in EJERCICIOS}
    payload = build_payload(workouts, filas, build_exercise_stats(workouts, plantillas),
                            plantillas)
    salida.parent.mkdir(parents=True, exist_ok=True)
    render(payload, salida)
    print(f"{len(workouts)} entrenos y {len(salud)} dias inventados -> {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
