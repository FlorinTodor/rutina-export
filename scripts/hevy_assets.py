#!/usr/bin/env python3
"""Saca del APK de Hevy la lista de sus animaciones de ejercicio.

Por que asi y no por la API: la API publica de Hevy NO devuelve imagenes. Una
plantilla trae id, titulo, tipo, musculos y equipamiento, y nada mas;
/exercise_templates/{id}/media da 404, y las paginas de hevy.com redirigen al
login. Comprobado.

Pero la app SI las ensena, y las descarga de un CloudFront publico:

    https://d2l9nsnmtah87f.cloudfront.net/exercise-assets/<fichero>.mp4

El nombre lleva el ejercicio dentro (00251201-Barbell-Bench-Press_Chest.mp4),
asi que con la lista de ficheros basta para emparejar por nombre. La lista
esta dentro del bundle de JavaScript del APK, que se saca del propio movil.

    python scripts/hevy_assets.py            # actualiza data/raw/hevy_assets.json

Hay que rehacerlo solo cuando Hevy anada ejercicios, no cada dia.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movil  # noqa: E402

CDN = "https://d2l9nsnmtah87f.cloudfront.net/exercise-assets"
PAQUETE = "com.hevy"
DESTINO = Path(__file__).resolve().parents[1] / "data" / "raw" / "hevy_assets.json"

# 00251201-Barbell-Bench-Press_Chest  ·  el numero es del catalogo de origen,
# el nombre va con guiones y detras del "_" viene la parte del cuerpo
# Los nombres bien escritos estan en las rutas de las miniaturas que el APK
# empaqueta: "/assets/14791201-Lever-Incline-Chest-Press_Chest_thumbnail@3x.jpg".
# Buscar el patron a secas en el binario daba nombres truncados, porque las
# cadenas del bundle van pegadas sin separador; anclando en "/assets/" y en
# "_thumbnail" se recortan bien. De 364 chapuceros a 868 correctos.
PATRON = re.compile(
    r"/assets/([0-9]{8,9}-[A-Za-z0-9()_.-]+?)_thumbnail[a-z_]*@?[0-9]?x?\.jpg")
SUFIJOS = re.compile(r"-FIX.*|_small.*")


def apk_del_movil(destino: Path) -> Path:
    salida = movil.adb("shell", "pm", "path", PAQUETE).stdout
    base = next((l.split(":", 1)[1].strip() for l in salida.splitlines()
                 if l.strip().endswith("base.apk")), None)
    if not base:
        raise SystemExit(f"{PAQUETE} no esta instalada en el movil")
    p = movil.adb("pull", base, str(destino), timeout=300)
    if not destino.exists():
        raise SystemExit(f"No pude traerme el APK: {p.stderr.strip()[:200]}")
    return destino


def extraer(apk: Path) -> list[str]:
    with zipfile.ZipFile(apk) as z:
        bundle = z.read("assets/index.android.bundle")
    # el bundle es binario; las cadenas van pegadas unas a otras sin separador
    texto = bundle.decode("utf-8", errors="ignore")
    return sorted({SUFIJOS.sub("", f) for f in PATRON.findall(texto)})


def como_catalogo(ficheros: list[str]) -> list[dict]:
    out = []
    for f in ficheros:
        nombre = re.sub(r"^[0-9]+-", "", f).split("_")[0].replace("-", " ")
        parte = f.split("_")[-1].replace("-FIX", "").replace("-", " ").lower()
        out.append({"file": f, "name": nombre, "muscle": parte,
                    "url": f"{CDN}/{f}.mp4"})
    return out


def validar(catalogo: list[dict]) -> list[dict]:
    """Tira los nombres que no existen en el CDN.

    Hace falta: las cadenas del bundle van pegadas unas a otras sin separador,
    asi que el patron recorta a ciegas y casi la mitad salen truncadas. De 364
    extraidas, 196 existian de verdad. Sin este paso, esas URLs dan 403 y el
    hueco del ejercicio se queda en negro.
    """
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    def vive(c):
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(c["url"], method="HEAD"), timeout=20)
            return c if r.status == 200 else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=16) as ex:
        vivos = [c for c in ex.map(vive, catalogo) if c]
    print(f"{len(vivos)} de {len(catalogo)} existen en el CDN")
    return vivos


def main() -> int:
    with TemporaryDirectory() as tmp:
        apk = apk_del_movil(Path(tmp) / "hevy.apk")
        print(f"APK: {apk.stat().st_size // 1024 // 1024} MB")
        ficheros = extraer(apk)

    catalogo = validar(como_catalogo(ficheros))
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(json.dumps(catalogo, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    print(f"{len(catalogo)} animaciones -> {DESTINO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
