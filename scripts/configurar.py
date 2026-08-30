#!/usr/bin/env python3
"""Deja el repositorio de GitHub listo para que el workflow funcione.

Sin esto hay que crear las bases de Notion, copiar seis identificadores a mano
desde config.toml a la pantalla de Secrets de GitHub, y acordarse de encender
Pages. Es la parte mas tediosa de montar esto y la que mas se equivoca uno.

    python scripts/configurar.py              # hace lo que falte
    python scripts/configurar.py --dry-run    # solo dice que haria

Es idempotente: se puede ejecutar las veces que haga falta. Solo escribe los
secretos que existan en config.toml, asi que no borra nada por descuido.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# secreto en GitHub -> de donde sale en config.toml
SECRETOS = {
    "HEVY_API_KEY":         ("hevy", "api_key"),
    "NOTION_TOKEN":         ("notion", "token"),
    "NOTION_PARENT_PAGE_ID": ("notion", "parent_page_id"),
    "NOTION_DB_DIAS":       ("notion", "db_dias"),
    "NOTION_DB_ENTRENOS":   ("notion", "db_entrenos"),
    "NOTION_DB_SERIES":     ("notion", "db_series"),
    "NOTION_DB_EJERCICIOS": ("notion", "db_ejercicios"),
    "NOTION_DB_MEDIDAS":    ("notion", "db_medidas"),
}
VARIABLES = {"HEVY_MODE": ("hevy", "mode"), "HEVY_DESDE": ("hevy", "desde")}


def gh(*args: str, entrada: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], input=entrada,
                          capture_output=True, text=True)


def repositorio() -> str:
    r = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    if r.returncode or not r.stdout.strip():
        raise SystemExit("No se de que repositorio se trata. ¿Estas dentro de uno?")
    return r.stdout.strip()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="no escribe nada")
    p.add_argument("-c", "--config", default="config.toml")
    args = p.parse_args()

    from rutina.config import Config
    cfg = Config.load(args.config)
    repo = repositorio()
    print(f"Repositorio: {repo}\n")

    # --- las bases de Notion tienen que existir antes que los secretos ---
    if cfg.notion.token and not cfg.notion.configured:
        print("Faltan las bases de Notion. Creandolas...")
        if args.dry_run:
            print("   [dry-run] python -m rutina init-notion")
        else:
            r = subprocess.run([sys.executable, "-m", "rutina", "init-notion"],
                               cwd=ROOT, env={**__import__("os").environ,
                                              "PYTHONPATH": "src"})
            if r.returncode:
                print("   No pude crearlas. Ejecuta a mano:")
                print("   python -m rutina init-notion --parent-page <id>")
                return 1
            cfg = Config.load(args.config)      # releer los ids nuevos

    def valor(seccion: str, campo: str) -> str:
        return str(getattr(getattr(cfg, seccion), campo, "") or "")

    # --- secretos ---
    print("Secretos:")
    ya = {l.split()[0] for l in gh("secret", "list").stdout.splitlines() if l.strip()}
    for nombre, (sec, campo) in SECRETOS.items():
        v = valor(sec, campo)
        if not v:
            print(f"   - {nombre:<22} sin valor en config.toml, lo dejo")
            continue
        if args.dry_run:
            print(f"   [dry-run] {nombre}")
            continue
        r = gh("secret", "set", nombre, entrada=v)
        estado = "actualizado" if nombre in ya else "creado"
        print(f"   ✓ {nombre:<22} {estado}" if not r.returncode
              else f"   ✗ {nombre:<22} {r.stderr.strip()[:50]}")

    # --- variables (no son secretas: se ven en la interfaz) ---
    print("\nVariables:")
    for nombre, (sec, campo) in VARIABLES.items():
        v = valor(sec, campo)
        if not v:
            continue
        if args.dry_run:
            print(f"   [dry-run] {nombre}={v}")
            continue
        r = gh("variable", "set", nombre, "--body", v)
        print(f"   ✓ {nombre}={v}" if not r.returncode
              else f"   ✗ {nombre}: {r.stderr.strip()[:50]}")

    # --- Pages, que es lo que sirve el dashboard ---
    print("\nGitHub Pages:")
    r = gh("api", f"repos/{repo}/pages", "--jq", ".html_url")
    if r.returncode == 0 and r.stdout.strip():
        print(f"   ya activo en {r.stdout.strip()}")
    elif args.dry_run:
        print("   [dry-run] activar sobre main /docs")
    else:
        r = gh("api", "-X", "POST", f"repos/{repo}/pages",
               "-f", "source[branch]=main", "-f", "source[path]=/docs")
        print("   activado sobre main /docs" if not r.returncode else
              f"   no pude activarlo: {r.stderr.strip()[:70]}\n"
              "   Hazlo en Settings > Pages (repo privado necesita GitHub Pro)")

    print("\nListo. Comprueba con:  python -m rutina check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
