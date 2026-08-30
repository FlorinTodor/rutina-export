#!/usr/bin/env python3
"""Todo lo que hay que sacar del movil, de una vez.

Un solo comando y un solo temporizador para los dos puentes. Antes eran dos
unidades de systemd separadas y eso traia tres problemas:

  * se peleaban por el ADB, porque las dos despertaban el movil y FitDays
    ademas le va pulsando botones;
  * hacian dos commits y dos push por el mismo dato de la misma noche;
  * disparaban el workflow dos veces.

Aqui se conecta una vez, se ejecutan los dos puentes en orden, y al final se
publica UN commit y se dispara el workflow UNA vez.

Los dos son independientes a proposito: **si uno falla, el otro se ejecuta
igual**. Es lo normal, no un caso raro. FitDays necesita el movil desbloqueado
porque maneja su interfaz; Health Connect no, porque nuestra app arranca por
encima de la pantalla de bloqueo. Con el movil bloqueado a las 20:45, lo
esperable es que FitDays se salte el dia y Health Connect entre igual.

    20:45  este script  → FitDays + Health Connect → import → push
    21:00  la nube      → Hevy + lo que el PC subio → Notion + dashboard
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movil  # noqa: E402
from movil import Fallo, conectar  # noqa: E402

log = logging.getLogger("movil-todo")

ROOT = Path(__file__).resolve().parents[1]
AQUI = Path(__file__).resolve().parent

# Health Connect va primero: no necesita la pantalla desbloqueada, asi que es
# el que mas probabilidades tiene de funcionar. FitDays despues, porque deja
# la app abierta y toqueteada.
PUENTES = [
    ("Health Connect", "health_pull.py"),
    ("FitDays", "fitdays_pull.py"),
]

FICHEROS = ["data/raw/health_daily.jsonl", "data/raw/body.jsonl"]


def correr(nombre: str, script: str, extra: list[str]) -> bool:
    """Ejecuta un puente. Nunca sube ni dispara: eso se hace una vez al final."""
    log.info("── %s ──", nombre)
    orden = [sys.executable, str(AQUI / script), "--no-push", "--no-trigger", *extra]
    r = subprocess.run(orden, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src"},
                       capture_output=True, text=True)
    for linea in (r.stdout + r.stderr).splitlines():
        if linea.strip():
            log.info("   %s", linea.rstrip())
    if r.returncode:
        log.warning("%s no pudo terminar; sigo con el resto", nombre)
        return False
    return True


# ------------------------------------------------------------ verificacion


def _filas(texto: str) -> list[dict]:
    import json as _json
    out = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea:
            try:
                out.append(_json.loads(linea))
            except ValueError:
                pass
    return out


def _guardado(fichero: str) -> list[dict]:
    """El fichero tal y como esta en el ultimo commit, para comparar."""
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{fichero}"],
                       capture_output=True, text=True)
    return _filas(r.stdout) if r.returncode == 0 else []


def verificar(salud_ok: bool) -> tuple[bool, list[str]]:
    """Mira que lo que hay en disco merezca subirse.

    Que el fichero haya cambiado no significa que el dato sea bueno. Lo que
    de verdad importa comprobar es que el historico no haya MENGUADO: eso
    seria una perdida de datos, y subirlo la haria permanente. El resto son
    avisos, porque tienen explicaciones normales (el movil apagado, un dia
    sin pesarte) y no justifican bloquear el resto.
    """
    from datetime import date

    hoy = date.today().isoformat()
    informe, grave = [], False

    for fichero in FICHEROS:
        ahora = _filas((ROOT / fichero).read_text(encoding="utf-8"))
        antes = _guardado(fichero)
        nombre = Path(fichero).name

        if len(ahora) < len(antes):
            informe.append(f"   GRAVE  {nombre}: tenia {len(antes)} filas y ahora "
                           f"{len(ahora)}. El historico ha menguado, NO se sube.")
            grave = True
            continue

        ultimo = max((f.get("day", "") for f in ahora), default="")
        nuevas = len(ahora) - len(antes)
        informe.append(f"   ok     {nombre}: {len(ahora)} filas "
                       f"(+{nuevas}), hasta {ultimo or '?'}")

    # el dia de hoy solo se exige si el puente de salud dijo que fue bien
    if salud_ok:
        dias = _filas((ROOT / "data/raw/health_daily.jsonl").read_text(encoding="utf-8"))
        de_hoy = next((d for d in dias if d.get("day") == hoy), None)
        if de_hoy is None:
            informe.append(f"   aviso  no hay fila de hoy ({hoy}) en health_daily.jsonl")
        elif de_hoy.get("steps") is None:
            informe.append(f"   aviso  la fila de hoy no trae pasos")
        else:
            informe.append(f"   ok     hoy {hoy}: {de_hoy['steps']} pasos, "
                           f"{de_hoy.get('sleep_hours') or '-'} h de sueno")

    return not grave, informe


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("FITDAYS_ADB_HOST"),
                   help="ip:puerto del movil (por defecto FITDAYS_ADB_HOST)")
    p.add_argument("--solo", choices=[s for _, s in PUENTES] + ["health", "fitdays"],
                   help="ejecutar solo uno de los dos puentes")
    p.add_argument("--conservar", action="store_true",
                   help="no borrar del movil los ficheros ya importados")
    p.add_argument("--no-push", action="store_true", help="no tocar git")
    p.add_argument("--no-trigger", action="store_true", help="no lanzar el workflow")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(message)s",
                        datefmt="%H:%M:%S")

    try:
        serial = conectar(args.host)
    except Fallo as e:
        log.error("%s", e)
        return 1
    # se fija aqui para que los dos puentes hablen con el mismo movil sin
    # tener que volver a buscarlo
    os.environ["ANDROID_SERIAL"] = serial
    log.info("Movil: %s", serial)

    extra = ["--host", serial]
    if args.conservar:
        extra.append("--conservar")

    resultados = {}
    for nombre, script in PUENTES:
        if args.solo and args.solo not in (script, nombre.split()[0].lower()):
            continue
        resultados[nombre] = correr(nombre, script, extra)

    hechos = [n for n, ok in resultados.items() if ok]
    fallidos = [n for n, ok in resultados.items() if not ok]

    log.info("── Comprobacion antes de subir ──")
    sano, informe = verificar(resultados.get("Health Connect", False))
    for linea in informe:
        log.info("%s", linea)

    if not sano:
        log.error("Los datos no pasan la comprobacion: no subo nada.")
        log.error("Estan en data/raw/ para que los mires; nada se ha perdido.")
        return 1

    subido = False
    if hechos and not args.no_push:
        subido = movil.publicar(
            FICHEROS, f"movil: datos al {time.strftime('%Y-%m-%d %H:%M')}", ROOT)
    # El push ya dispara el workflow por si mismo (sync.yml escucha los
    # cambios en data/). Lanzarlo ADEMAS a mano encolaba una segunda
    # ejecucion que hacia el mismo trabajo sobre un checkout viejo y
    # terminaba con el push rechazado: el repositorio en rojo cada noche
    # sin que nada estuviera mal. Solo se dispara cuando no hubo push,
    # que es el caso que de verdad necesita el respaldo: sin datos nuevos
    # del movil no hay push, pero Hevy si puede tener entrenos que
    # sincronizar.
    if hechos and not args.no_trigger and not subido:
        movil.disparar_nube()

    # el volcado de pantalla de adb_ui tampoco tiene por que quedarse
    from adb_ui import UI_XML
    try:
        movil.borrar(UI_XML)
    except movil.Rechazado as e:
        log.debug("No limpio %s: %s", UI_XML, e)

    log.info("Resumen: %s", ", ".join(f"{n} OK" for n in hechos) or "nada salio bien")
    if fallidos:
        log.warning("Sin datos nuevos de: %s", ", ".join(fallidos))
    # solo es un fallo de verdad si no entro NINGUNO
    return 0 if hechos else 1


if __name__ == "__main__":
    sys.exit(main())
