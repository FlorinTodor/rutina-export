#!/usr/bin/env python3
"""Saca Health Connect del movil con la app propia y lo importa.

Sustituye a la Google Sheet de Health Data Export: sin hoja de calculo, sin
service account y sin la suscripcion que esa app cobra por exportar sola.

Por que hace falta el PC y no se puede hacer todo en el movil: leer Health
Connect en segundo plano exige el permiso READ_HEALTH_DATA_IN_BACKGROUND, que
es justo lo que se paga. Aqui no se necesita, porque la app no decide cuando
ejecutarse: la despierta este script, la app lee en primer plano y se acabo.

    20:45  el temporizador levanta el PC -> ADB -> la app lee -> pull -> import

Del movil solo se borra el health.json que se acaba de traer, y solo cuando
el import ya ha ido bien. Todo pasa por la guardia de `movil.comprobar()`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movil  # noqa: E402
from movil import Fallo, conectar, despertar  # noqa: E402

log = logging.getLogger("health")

PKG = "com.rutina.export"
ACTIVIDAD = f"{PKG}/.MainActivity"
REMOTO = f"{movil.CARPETA}/health.json"
ROOT = Path(__file__).resolve().parents[1]
FICHEROS = ["data/raw/health_daily.jsonl", "data/raw/body.jsonl"]
APK = ROOT / "android" / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"

# los mismos que declara el manifiesto
PERMISOS = [
    "READ_STEPS", "READ_DISTANCE", "READ_TOTAL_CALORIES_BURNED",
    "READ_ACTIVE_CALORIES_BURNED", "READ_FLOORS_CLIMBED", "READ_SLEEP",
    "READ_HEART_RATE", "READ_RESTING_HEART_RATE", "READ_HEART_RATE_VARIABILITY",
    "READ_OXYGEN_SATURATION", "READ_VO2_MAX", "READ_WEIGHT", "READ_BODY_FAT",
    "READ_BONE_MASS", "READ_LEAN_BODY_MASS", "READ_BODY_WATER_MASS",
    "READ_BASAL_METABOLIC_RATE", "READ_HEIGHT",
]


def instalada() -> bool:
    return PKG in movil.shell(f"pm list packages {PKG}")


def instalar() -> None:
    if not APK.exists():
        raise Fallo(f"No hay APK en {APK}. Compilalo con: android/build.sh")
    log.info("Instalando la app en el movil")
    p = movil.adb("install", "-r", str(APK), timeout=300)
    if "Success" not in p.stdout:
        raise Fallo(f"No pude instalar el APK: {(p.stdout + p.stderr).strip()[:300]}")


def hora_movil() -> datetime:
    """El reloj del movil, que es el que sella el JSON."""
    s = movil.shell("date +%Y-%m-%dT%H:%M:%S").strip()
    return datetime.fromisoformat(s)


def lanzar(dias: int) -> None:
    """Arranca la app, o la despierta si ya estaba abierta.

    AQUI NO SE PARA LA APP A LA FUERZA, y no es un detalle de estilo. Android
    saca a una app force-stopped de la lista de servicios de accesibilidad
    habilitados y no la vuelve a meter: cada ejecucion del temporizador dejaba
    el movil sin exportar FitDays hasta que se reactivaba a mano.

    Lo que hacia falta del force-stop era que la actividad reprocesara el
    intent en vez de reutilizar el de la vez anterior. Eso lo resuelve la app:
    `launchMode="singleTop"` + `onNewIntent`.
    """
    log.info("Abriendo la app (%d dias)", dias)
    movil.shell(f"am start -n {ACTIVIDAD} --ei dias {dias}")


def esperar(desde: datetime, destino: Path, segundos: int = 90) -> str:
    """Espera a que aparezca un JSON entero y MAS NUEVO que la llamada.

    No se borra el fichero anterior para forzar que sea nuevo: se comprueba
    su sello de tiempo. Asi este script no borra nada que no haya traido.
    """
    limite = time.time() + segundos
    ultimo = ""
    while time.time() < limite:
        time.sleep(2)
        if not movil.existe(REMOTO):
            continue
        try:
            movil.traer(REMOTO, destino)
            datos = json.loads(destino.read_text(encoding="utf-8"))
        except (RuntimeError, json.JSONDecodeError) as e:
            ultimo = str(e)          # se pillo a medio escribir; se reintenta
            continue
        if datos.get("error"):
            raise Fallo(f"La app dice: {datos['error']}")
        if not datos.get("fin"):
            ultimo = "el JSON esta incompleto"
            continue
        sello = datos.get("generado", "")
        try:
            if datetime.fromisoformat(sello) < desde:
                ultimo = f"el fichero es de antes ({sello})"
                continue
        except ValueError:
            pass
        return REMOTO

    raise Fallo(
        f"La app no dejo un JSON nuevo en {segundos}s ({ultimo or 'sin pistas'}).\n"
        "La primera vez hay que conceder los permisos A MANO en la pantalla que\n"
        "sale en el movil. Despues ya no vuelve a preguntar.\n"
        f"Para ver que paso:  adb logcat -d -s rutina")


def dar_permisos() -> None:
    """Intenta concederlos desde el PC. Health Connect suele no dejar."""
    ok, no = [], []
    for p in PERMISOS:
        r = movil.adb("shell", "pm", "grant", PKG, f"android.permission.health.{p}")
        (ok if r.returncode == 0 and not r.stderr.strip() else no).append(p)
    log.info("Concedidos desde el PC: %d de %d", len(ok), len(PERMISOS))
    if no:
        print("\nEstos hay que darlos a mano en el movil "
              "(Ajustes > Seguridad y privacidad > Health Connect > Rutina Export):")
        for p in no:
            print("   ", p.replace("READ_", "").lower())
        print("\nO abre la app una vez y concede en la pantalla que sale:")
        print(f"    adb shell am start -n {ACTIVIDAD}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("FITDAYS_ADB_HOST"),
                   help="ip:puerto del movil (por defecto FITDAYS_ADB_HOST)")
    p.add_argument("--dias", type=int, default=7,
                   help="cuantos dias pedir hacia atras (7 por defecto)")
    p.add_argument("--dry-run", action="store_true",
                   help="trae los datos y ensena las diferencias, sin escribir")
    p.add_argument("--conservar", action="store_true",
                   help="no borrar del movil el JSON ya importado")
    p.add_argument("--permisos", action="store_true",
                   help="solo intenta conceder los permisos de Health Connect")
    p.add_argument("--instalar", action="store_true", help="reinstala el APK y sale")
    p.add_argument("--sueno-por", choices=("fin", "inicio"), default="fin",
                   dest="sueno_por", help="a que dia va una noche de sueno")
    p.add_argument("--no-push", action="store_true", help="no tocar git")
    p.add_argument("--no-trigger", action="store_true", help="no lanzar el workflow")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(message)s",
                        datefmt="%H:%M:%S")
    try:
        serial = conectar(args.host)
        os.environ["ANDROID_SERIAL"] = serial
        log.info("Movil: %s", serial)

        if args.instalar:
            instalar()
            return 0
        if not instalada():
            instalar()
        if args.permisos:
            dar_permisos()
            return 0

        despertar(os.environ.get("FITDAYS_PIN"), exigir=False)
        movil.preparar()

        t0 = hora_movil()
        lanzar(args.dias)
        local = ROOT / "data" / "raw" / "health_export.json"
        remoto = esperar(t0, local)
        log.info("Traido %s (%d KB)", Path(remoto).name, local.stat().st_size // 1024)

        orden = [sys.executable, "-m", "rutina", "import-health", str(local),
                 "--sueno-por", args.sueno_por]
        if args.dry_run:
            orden.append("--dry-run")
        r = subprocess.run(orden, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src"},
                           capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode:
            log.error("Dejo %s en el movil: el import fallo y el dato hace falta", remoto)
            raise Fallo(f"El importador fallo: {r.stderr.strip()[:300]}")

        movil.shell("input keyevent KEYCODE_HOME")

        if args.dry_run:
            log.info("Dry-run: no borro nada ni subo nada")
            return 0

        # Importado y a salvo en el repositorio: ya se puede borrar del movil.
        if args.conservar:
            log.info("Lo dejo en el movil: %s", remoto)
        else:
            movil.borrar(remoto)

        if not args.no_push:
            movil.publicar(FICHEROS,
                           f"health: datos al {time.strftime('%Y-%m-%d %H:%M')}", ROOT)
        if not args.no_trigger:
            movil.disparar_nube()
        return 0

    except (Fallo, movil.Rechazado) as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
