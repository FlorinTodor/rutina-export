"""El escenario que rompería el sistema: la app exporta con rango "Last day"
y la hoja pasa a contener un único día. El histórico debe sobrevivir."""

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rutina import history
from rutina.models import BodyMeasurement, DailyHealth


def main():
    with tempfile.TemporaryDirectory() as tmp:
        history.RAW = Path(tmp)

        # 1. primera sincronización: la hoja trae 30 días
        wide = [DailyHealth(day=date(2026, 8, d), steps=10000 + d) for d in range(1, 31)]
        merged = history.merge(wide, DailyHealth, "health_daily.jsonl")
        _write(merged, Path(tmp) / "health_daily.jsonl")
        print(f"1a sincronizacion: hoja 30 dias -> histórico {len(merged)}")
        assert len(merged) == 30

        # 2. la exportación automática pasa a "Last day": la hoja solo trae hoy
        narrow = [DailyHealth(day=date(2026, 8, 31), steps=12345)]
        merged = history.merge(narrow, DailyHealth, "health_daily.jsonl")
        _write(merged, Path(tmp) / "health_daily.jsonl")
        print(f"2a sincronizacion: hoja  1 dia   -> histórico {len(merged)}")
        assert len(merged) == 31, "se perdió el histórico"
        assert merged[0].day == date(2026, 8, 1), "falta el día más antiguo"
        assert merged[-1].steps == 12345, "no entró el día nuevo"

        # 3. un dato reexportado corregido debe pisar al viejo
        fixed = [DailyHealth(day=date(2026, 8, 15), steps=99999)]
        merged = history.merge(fixed, DailyHealth, "health_daily.jsonl")
        _write(merged, Path(tmp) / "health_daily.jsonl")
        d15 = next(x for x in merged if x.day == date(2026, 8, 15))
        print(f"3a sincronizacion: correccion del dia 15 -> {d15.steps}")
        assert d15.steps == 99999, "la corrección no pisó al dato viejo"
        assert len(merged) == 31, "la corrección alteró el número de días"

        # 4. los pesajes van por el mismo camino
        b = history.merge([BodyMeasurement(day=date(2026, 8, 29), weight_kg=107.7)],
                          BodyMeasurement, "body.jsonl")
        _write(b, Path(tmp) / "body.jsonl")
        b = history.merge([BodyMeasurement(day=date(2026, 8, 30), weight_kg=107.1)],
                          BodyMeasurement, "body.jsonl")
        print(f"pesajes acumulados: {[(str(x.day), x.weight_kg) for x in b]}")
        assert len(b) == 2

        print("\nOK · el histórico sobrevive aunque la hoja se quede en un día")


def _write(items, path):
    import json
    from rutina.models import to_jsonable
    path.write_text("\n".join(json.dumps(to_jsonable(i)) for i in items) + "\n")


if __name__ == "__main__":
    main()
