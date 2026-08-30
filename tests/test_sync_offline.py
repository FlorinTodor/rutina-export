"""Prueba el motor de upsert sin tocar Notion, con un cliente falso.
Lo critico: la segunda pasada no debe reescribir nada."""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rutina.models import BodyMeasurement, DailyHealth, SetRecord, Workout
from rutina.notion.sync import NotionSync
from rutina.transform.metrics import build_day_rows, build_exercise_stats, find_prs


class FakeNotion:
    """Imita lo justo de NotionClient para contar operaciones."""

    def __init__(self):
        self.pages = {}
        self.created = self.updated = 0
        self.calls = 0

    def create_page(self, database_id, properties):
        self.calls += 1
        self.created += 1
        pid = f"page-{len(self.pages) + 1}"
        self.pages[pid] = properties
        return {"id": pid}

    def update_page(self, page_id, properties):
        self.calls += 1
        self.updated += 1
        self.pages[page_id] = properties
        return {"id": page_id}

    def query_all(self, database_id, **kw):
        return iter(())


def make_data():
    def mk(wid, day, title, sets):
        return Workout(
            wid, title, datetime(2026, 8, day, 18, 0), datetime(2026, 8, day, 19, 10),
            # cada ejercicio con su propio template_id, como en la API real
            sets=[SetRecord(wid, 0, n, f"tpl-{abs(hash(n)) % 999:03d}", i, t, w, r,
                            None, None, rpe)
                  for i, (n, t, w, r, rpe) in enumerate(sets)],
        )

    workouts = [
        mk("w1", 24, "Empuje A", [("Press banca", "warmup", 40, 10, None),
                                  ("Press banca", "normal", 80, 8, 8.0),
                                  ("Press banca", "normal", 80, 7, 9.0)]),
        mk("w2", 26, "Tiron B", [("Peso muerto", "normal", 140, 5, 9.0),
                                 ("Dominadas", "normal", 0, 12, 8.0)]),
        mk("w3", 28, "Empuje A", [("Press banca", "normal", 85, 8, 9.0)]),
    ]
    health = [DailyHealth(date(2026, 8, d), steps=8000 + d * 100, resting_hr=55,
                          sleep_hours=7.2) for d in range(24, 29)]
    body = [BodyMeasurement(date(2026, 8, 24), weight_kg=78.4, fat_percent=16.2,
                            muscle_mass_kg=59.1, bmi=23.9)]
    return workouts, health, body


def run_pass(sync, workouts, health, body):
    rows = build_day_rows(workouts, health, body)
    days = sync.sync_dias(rows)
    ex = sync.sync_ejercicios(build_exercise_stats(workouts))
    wp = sync.sync_entrenos(workouts, days, find_prs(workouts))
    sync.sync_series(workouts, wp, ex)
    sync.sync_medidas(body, days)
    sync.save_state()


def main():
    import tempfile

    workouts, health, body = make_data()
    dbs = {k: f"db-{k}" for k in
           ["db_dias", "db_entrenos", "db_series", "db_ejercicios", "db_medidas"]}

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state.json"
        fake = FakeNotion()

        sync = NotionSync(fake, dbs, state)
        run_pass(sync, workouts, health, body)
        first = (fake.created, fake.updated)
        print(f"1a pasada: {fake.created} creadas, {fake.updated} actualizadas")

        fake.created = fake.updated = 0
        sync = NotionSync(fake, dbs, state)  # relee el estado del disco
        run_pass(sync, workouts, health, body)
        print(f"2a pasada: {fake.created} creadas, {fake.updated} actualizadas")
        assert (fake.created, fake.updated) == (0, 0), "la 2a pasada deberia ser un no-op"

        # un cambio real debe propagarse, y solo ese
        workouts[2].sets[0].reps = 9
        fake.created = fake.updated = 0
        sync = NotionSync(fake, dbs, state)
        run_pass(sync, workouts, health, body)
        print(f"tras editar 1 serie: {fake.created} creadas, {fake.updated} actualizadas")
        assert fake.created == 0 and 0 < fake.updated <= 5, "cambio mal propagado"

        print(f"\nOK · {first[0]} paginas en el backfill inicial")


if __name__ == "__main__":
    main()
