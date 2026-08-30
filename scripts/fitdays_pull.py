#!/usr/bin/env python3
"""Saca el export de FitDays del movil y lo importa. Pensado para un cron.

Tiene que correr en el PC, no en GitHub Actions: la nube no puede hablar con
tu movil. Se ejecuta antes que el workflow para que este encuentre los datos
ya subidos al repositorio.

El recorrido en la app (version 1.28):
    Tablas -> Datos del usuario -> menu ⋮ -> Exportar -> Todas
           -> icono de compartir -> Guardar en local

Los botones se buscan por su texto, no por coordenadas: asi aguanta cambios
de diseno y de resolucion.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movil  # noqa: E402
from adb_ui import dump, find, sh, tap  # noqa: E402
from movil import Fallo, conectar, despertar  # noqa: E402

log = logging.getLogger("fitdays")

PKG = "cn.fitdays.fitdays"
EXPORT_DIR = "/storage/emulated/0/Documents"
ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------- pasos

def pulsar(texto: str, intentos: int = 6, espera: float = 1.5) -> None:
    """Busca por texto y pulsa. Reintenta: la app tarda en pintar."""
    for i in range(intentos):
        n = find(dump(), texto)
        if n:
            log.info("  pulso %r en %s", texto, n["xy"])
            tap(n)
            return
        time.sleep(espera)
    raise Fallo(f"No encuentro {texto!r} en pantalla tras {intentos} intentos")


def exportar() -> None:
    log.info("Abriendo FitDays")
    sh("shell", "am", "force-stop", PKG)          # empezar desde un estado conocido
    time.sleep(1)
    sh("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(5)

    pulsar("Tablas")
    pulsar("Datos del usuario")
    time.sleep(2)

    # el aviso de ayuda de la app se come el primer toque en el menu
    sh("shell", "input", "tap", "540", "1600")
    time.sleep(1)
    pulsar("choose_iv")                            # menu de tres puntos
    pulsar("Exportar")
    pulsar("Todas")

    # el icono de compartir esta entre los dos botones y no tiene texto
    n = find(dump(), "comparison_data_tips")
    if not n:
        raise Fallo("No encuentro el boton de compartir del export")
    log.info("  pulso el icono de compartir en %s", n["xy"])
    tap(n)
    time.sleep(2)

    pulsar("Guardar en local")
    time.sleep(4)


def traer(destino: Path) -> str:
    """Trae el export a `destino` y devuelve su ruta EXACTA en el movil.

    No borra nada. El borrado se hace al final, y solo del fichero que se ha
    traido, cuando ya se sabe que el import ha funcionado: si se borrase aqui
    y el importador fallara, el dato del movil ya no estaria para reintentar.

    Los exports viejos que la app haya ido dejando sueltos por Documents se
    recogen en la carpeta del pipeline, pero NO se borran: se quedan ahi para
    que los mires y los borres tu si quieres.
    """
    salida = movil.shell(f"ls -1 {shlex.quote(EXPORT_DIR)}")
    sueltos = [f"{EXPORT_DIR}/{n.strip()}" for n in salida.splitlines()
               if n.strip().startswith("FitdaysData_")]
    if not sueltos:
        raise Fallo(f"La app no dejo ningun fichero en {EXPORT_DIR}")

    recogidos = []
    for f in sueltos:
        try:
            recogidos.append(movil.mover(f))
        except (movil.Rechazado, RuntimeError) as e:
            log.warning("Dejo %s donde estaba: %s", Path(f).name, e)
    if not recogidos:
        raise Fallo("No pude recoger ningun export en la carpeta del pipeline")
    if len(recogidos) > 1:
        log.info("Habia %d exports sueltos; los he juntado en %s",
                 len(recogidos), movil.CARPETA)

    # el mas reciente por nombre: la app los sella con la fecha
    remoto = sorted(recogidos)[-1]
    movil.traer(remoto, destino)
    log.info("Traido %s (%d KB)", Path(remoto).name, destino.stat().st_size // 1024)
    return remoto


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=os.environ.get("FITDAYS_ADB_HOST"),
                   help="ip:puerto del movil (por defecto FITDAYS_ADB_HOST)")
    p.add_argument("--no-push", action="store_true", help="no tocar git")
    p.add_argument("--conservar", action="store_true",
                   help="no borrar del movil el export ya importado")
    p.add_argument("--no-trigger", action="store_true",
                   help="no lanzar el workflow de GitHub al terminar")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(message)s",
                        datefmt="%H:%M:%S")
    try:
        serial = conectar(args.host)
        os.environ["ANDROID_SERIAL"] = serial
        log.info("Movil: %s", serial)

        despertar(os.environ.get("FITDAYS_PIN"))
        exportar()
        local = ROOT / "data" / "raw" / "fitdays_export.csv"
        remoto = traer(local)

        r = subprocess.run([sys.executable, "-m", "rutina", "import-fitdays",
                            str(local)],
                           cwd=ROOT, env={**os.environ, "PYTHONPATH": "src"},
                           capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode:
            log.error("Dejo %s en el movil: el import fallo y el dato hace falta",
                      remoto)
            raise Fallo(f"El importador fallo: {r.stderr.strip()[:300]}")

        # Ya esta importado y guardado en el repositorio: se puede borrar del
        # movil. Solo este fichero, por su ruta exacta, y con la guardia de
        # movil.comprobar() de por medio.
        if args.conservar:
            log.info("Lo dejo en el movil: %s", remoto)
        else:
            movil.borrar(remoto)

        sh("shell", "input", "keyevent", "KEYCODE_HOME")
        subido = False
        if not args.no_push:
            subido = movil.publicar(
                ["data/raw/body.jsonl"],
                f"fitdays: pesajes al {time.strftime('%Y-%m-%d %H:%M')}", ROOT)
        # El push ya dispara el workflow por si mismo (sync.yml escucha los
        # cambios en data/). Lanzarlo ADEMAS a mano encolaba una segunda
        # ejecucion que hacia el mismo trabajo sobre un checkout viejo y
        # terminaba con el push rechazado: el repositorio en rojo cada noche
        # sin que nada estuviera mal. Solo se dispara cuando no hubo push,
        # que es el caso que de verdad necesita el respaldo: sin datos nuevos
        # del movil no hay push, pero Hevy si puede tener entrenos que
        # sincronizar.
        if not args.no_trigger and not subido:
            movil.disparar_nube()
        return 0

    except (Fallo, subprocess.CalledProcessError) as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
