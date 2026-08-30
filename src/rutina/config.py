"""Configuracion desde config.toml (local) o variables de entorno (CI).

Las variables de entorno siempre ganan, para que GitHub Actions pueda
inyectar los secretos sin que exista ningun fichero.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HevyConfig:
    # Fecha (YYYY-MM-DD) desde la que cuentan los entrenos. Lo anterior se
    # ignora en todo: recibos, progresiones, volumen, rachas y Notion.
    #
    # Existe porque cambiar de gimnasio invalida las comparaciones: otras
    # maquinas, otras poleas, otros discos. Un press de 60 kg en un sitio no
    # es el mismo que 60 kg en otro, y mezclarlos convierte la progresion en
    # ruido. No borra nada: los entrenos siguen en Hevy y en el historico.
    desde: str = ""

    mode: str = "api"          # "api" | "csv"
    api_key: str = ""
    csv_path: str = "data/raw/hevy_export.csv"


@dataclass
class HealthConfig:
    """Los datos de salud entran por android/, no por configuracion.

    Queda esta clase por si algun dia hace falta un ajuste, pero hoy no hay
    ninguno: la app del movil sube a data/inbox/ y el workflow lo importa.
    """


@dataclass
class NotionConfig:
    token: str = ""
    parent_page_id: str = ""
    db_dias: str = ""
    db_entrenos: str = ""
    db_series: str = ""
    db_ejercicios: str = ""
    db_medidas: str = ""

    @property
    def db_ids(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in
                ("db_dias", "db_entrenos", "db_series", "db_ejercicios", "db_medidas")}

    @property
    def configured(self) -> bool:
        return bool(self.token) and all(self.db_ids.values())


@dataclass
class Config:
    hevy: HevyConfig = field(default_factory=HevyConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    notion: NotionConfig = field(default_factory=NotionConfig)
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path = "config.toml") -> Config:
        path = Path(path)
        raw = tomllib.loads(path.read_text()) if path.exists() else {}
        cfg = cls(
            hevy=HevyConfig(**raw.get("hevy", {})),
            health=HealthConfig(**raw.get("health", {})),
            notion=NotionConfig(**raw.get("notion", {})),
            path=path,
        )
        cfg._apply_env()
        return cfg

    def _apply_env(self) -> None:
        env = {
            ("hevy", "mode"): "HEVY_MODE",
            ("hevy", "api_key"): "HEVY_API_KEY",
            ("hevy", "csv_path"): "HEVY_CSV_PATH",
            ("hevy", "desde"): "HEVY_DESDE",
            ("notion", "token"): "NOTION_TOKEN",
            ("notion", "parent_page_id"): "NOTION_PARENT_PAGE_ID",
            ("notion", "db_dias"): "NOTION_DB_DIAS",
            ("notion", "db_entrenos"): "NOTION_DB_ENTRENOS",
            ("notion", "db_series"): "NOTION_DB_SERIES",
            ("notion", "db_ejercicios"): "NOTION_DB_EJERCICIOS",
            ("notion", "db_medidas"): "NOTION_DB_MEDIDAS",
        }
        for (section, key), var in env.items():
            val = os.environ.get(var)
            if val:
                setattr(getattr(self, section), key, val.strip())

    def save_db_ids(self, ids: dict[str, str]) -> None:
        """Escribe los ids de las bases recien creadas en config.toml."""
        for k, v in ids.items():
            setattr(self.notion, k, v)
        if not self.path:
            return
        lines = self.path.read_text().splitlines() if self.path.exists() else []
        out, seen = [], set()
        for line in lines:
            key = line.split("=")[0].strip()
            if key in ids:
                out.append(f'{key} = "{ids[key]}"')
                seen.add(key)
            else:
                out.append(line)
        for k, v in ids.items():
            if k not in seen:
                out.append(f'{k} = "{v}"')
        self.path.write_text("\n".join(out) + "\n")
