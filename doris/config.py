"""Shared .env configuration for Doris SQL and Lance table locations."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Mapping

SOURCE_TABLES = (
    "frames", "episodes", "media", "frame_embeddings", "audio_embeddings", "metadata",
    "export_manifest", "export_checkpoints", "export_index_status",
)
ANALYSIS_TABLES = ("grasp_hits", "grasp_review", "episode_policy")
_previous = {}


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Catalog, database and table names must use letters, digits and underscores")
    return value


@dataclass(frozen=True)
class Config:
    host: str
    query_port: int
    http_port: int
    user: str
    password: str | None = field(repr=False)
    ssl_ca: str | None
    dataset_root: str
    catalog: str
    database: str
    analysis_database: str
    source_tables: dict[str, str]
    analysis_tables: dict[str, str]

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None):
        values = os.environ if values is None else values
        def port(key, default):
            try:
                result = int(values.get(key, default))
            except ValueError:
                raise ValueError(f"{key} must be an integer port") from None
            if not 1 <= result <= 65535:
                raise ValueError(f"{key} must be between 1 and 65535")
            return result
        def names(keys, prefix):
            result = {k: identifier(values.get(prefix + k.upper(), k)) for k in keys}
            if len({v.lower() for v in result.values()}) != len(result):
                raise ValueError(f"{prefix} table names must be unique")
            return result
        return cls(
            host=values.get("DORIS_HOST", "127.0.0.1"),
            query_port=port("DORIS_QUERY_PORT", "9030"),
            http_port=port("DORIS_HTTP_PORT", "8030"),
            user=values.get("DORIS_USER", "root"), password=values.get("DORIS_PASSWORD"),
            ssl_ca=values.get("DORIS_SSL_CA") or None,
            dataset_root=values.get("NOTEBOOK_DATASET_ROOT", "oss://YOUR_BUCKET/robotics/droid100"),
            catalog=identifier(values.get("DORIS_CATALOG", "droid100")),
            database=identifier(values.get("DORIS_DATABASE", "default")),
            analysis_database=identifier(values.get("DORIS_ANALYSIS_DATABASE", "droid100_analysis")),
            source_tables=names(SOURCE_TABLES, "LANCE_TABLE_"),
            analysis_tables=names(ANALYSIS_TABLES, "DORIS_TABLE_"),
        )

    def source(self, name: str, *, quoted=True) -> str:
        parts = (self.catalog, self.database, self.source_tables[name])
        return ".".join(f"`{p}`" if quoted else p for p in parts)

    def analysis(self, name: str) -> str:
        return f"`internal`.`{self.analysis_database}`.`{self.analysis_tables[name]}`"

    def uri(self, root: str, name: str) -> str:
        return root.rstrip("/") + "/" + self.source_tables[name] + ".lance"

    def sql(self, text: str) -> str:
        tokens = {
            "CATALOG": f"`{self.catalog}`",
            "SOURCE_DATABASE": f"`{self.catalog}`.`{self.database}`",
            "ANALYSIS_DATABASE": f"`internal`.`{self.analysis_database}`",
        }
        for name in SOURCE_TABLES:
            tokens["SOURCE_" + name.upper()] = self.source(name)
            tokens["SOURCE_" + name.upper() + "_NAME"] = self.source(name, quoted=False)
        for name in ANALYSIS_TABLES:
            tokens["ANALYSIS_" + name.upper()] = self.analysis(name)
        # One pass: replacement text can never be interpreted as another token.
        return re.sub(r"\{\{([A-Z_]+)\}\}",
                      lambda m: tokens.get(m[1], m[0]), text)


def load_config(path: Path | None = None) -> Config:
    """File values override the environment; reloads also remove stale file values.

    Disable variable expansion so passwords containing '$' are kept literally.
    Storage credentials are made available to the existing CLI and child process.
    """
    from dotenv import dotenv_values
    path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / ".env"
    for key, (original, injected) in _previous.items():
        if os.environ.get(key) == injected:
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
    _previous.clear()
    if path.is_file():
        for key, value in dotenv_values(path, interpolate=False).items():
            if value is not None:
                _previous[key] = (os.environ.get(key), value)
                os.environ[key] = value
    return Config.from_env()


def lance_uri(root: str, name: str) -> str:
    return Config.from_env().uri(root, name)
