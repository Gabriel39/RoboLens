"""Small adapters for the live notebook; production export and SQL stay shared."""
from __future__ import annotations

import csv
import math
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import sqlparse


def find_project(start: Path) -> Path:
    """Allow Jupyter to start from either the project root or notebooks directory."""
    for directory in (start.resolve(), *start.resolve().parents):
        if (directory / "pipeline/export_droid.py").is_file():
            return directory
    raise FileNotFoundError("Start Jupyter inside the droid100-lance-doris project")


def run_export(project: Path, output: str, work: Path, max_episodes: int | None,
               frame_stride: int, device: str, resume: bool) -> None:
    """Stream the existing CLI output and stop immediately on export failure."""
    command = [sys.executable, "-m", "pipeline.export_droid", "--output", output,
               "--work-dir", str(work), "--frame-stride", str(frame_stride),
               "--device", device]
    if max_episodes is not None:
        command += ["--max-episodes", str(max_episodes)]
    if resume:
        command.append("--resume")
    # Pass arguments directly; never interpolate credentials into a shell command.
    process = subprocess.Popen(command, cwd=project, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in process.stdout:
            print(line, end="", flush=True)
        if process.wait():
            raise RuntimeError("Export failed; review the log before continuing")
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        process.stdout.close()


def connect_doris(password: str):
    """Connect with a finite query timeout and without multi-statement execution."""
    import pymysql
    options = dict(host=os.environ.get("DORIS_HOST", "127.0.0.1"),
                   port=int(os.environ.get("DORIS_QUERY_PORT", "9030")),
                   user=os.environ.get("DORIS_USER", "root"), password=password,
                   charset="utf8mb4", autocommit=True, connect_timeout=10,
                   read_timeout=600, write_timeout=120)
    if os.environ.get("DORIS_SSL_CA"):
        options.update(ssl_ca=os.environ["DORIS_SSL_CA"],
                       ssl_verify_cert=True, ssl_verify_identity=True)
    return pymysql.connect(**options)


def run_sql(connection, text: str) -> list[pd.DataFrame]:
    """Execute shared SQL sequentially and return only result-bearing statements."""
    frames = []
    for statement in sqlparse.split(text):
        if not sqlparse.format(statement, strip_comments=True).strip().strip(";"):
            continue
        with connection.cursor() as cursor:
            cursor.execute(statement)
            if cursor.description:
                frames.append(pd.DataFrame(cursor.fetchall(),
                                           columns=[item[0] for item in cursor.description]))
    return frames


def initialize_doris(connection, project: Path, dataset_root: str) -> list[pd.DataFrame]:
    """Fill the trusted setup template in memory without displaying credentials."""
    from pipeline.export_droid import storage_options
    options = storage_options(dataset_root, None, None)
    if not dataset_root.startswith("oss://"):
        raise ValueError("Live Doris setup requires an OSS dataset root")
    if options.get("oss_security_token"):
        raise ValueError("Configure an STS-enabled catalog outside this setup helper")
    text = (project / "doris/sql/01_setup.sql").read_text()
    replacements = {
        '"oss://REPLACE_BUCKET/robotics/droid100"': dataset_root,
        '"https://oss-cn-hangzhou.aliyuncs.com"': options["oss_endpoint"],
        '"cn-hangzhou"': os.environ.get("OSS_REGION", "cn-hangzhou"),
        '"REPLACE_ACCESS_KEY"': options["oss_access_key_id"],
        '"REPLACE_SECRET_KEY"': options["oss_secret_access_key"],
    }
    for token, value in replacements.items():
        # Use the driver's escaping rather than concatenating raw SQL literals.
        text = text.replace(token, connection.escape(value))
    try:
        return run_sql(connection, text)
    except Exception:
        # DDL errors may echo credentials. Keep sensitive SQL out of notebook output.
        raise RuntimeError("Catalog/table setup failed; inspect Doris privately, then resume without setup") from None


def boolean(value: str) -> bool:
    """Reject ambiguous labels instead of coercing nonempty strings to True."""
    lowered = value.strip().lower()
    if lowered not in {"true", "false"}:
        raise ValueError("Boolean CSV fields must be true or false")
    return lowered == "true"


def load_annotations(connection, project: Path, review_path: Path, policy_path: Path,
                     run_id: str, hits: pd.DataFrame, episode_ids: set[int]) -> dict:
    """Validate small review CSVs before parameterized Doris inserts.

    No labels are synthesized. Both CSVs are validated before either write starts.
    Writes are separate autocommit statements, not a cross-table transaction.
    Unique keys make rerunning unchanged records an upsert.
    """
    allowed_hits = {(str(row.sample_id), int(row.episode_index))
                    for row in hits.itertuples(index=False)}
    pending = {}
    for name, path in [("grasp_review", review_path), ("episode_policy", policy_path)]:
        template = project / "doris/templates" / f"{name}.csv"
        columns = template.read_text().strip().split(",")
        with path.open(newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != columns:
                raise ValueError(f"Unexpected CSV columns: {name}")
            rows = list(reader)
        seen = set()
        for row in rows:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed CSV row")
            row["episode_index"] = int(row["episode_index"])
            if row["episode_index"] not in episode_ids:
                raise ValueError("Annotation episode is absent from this export")
            if name == "grasp_review":
                key = row["sample_id"]
                if row["run_id"] != run_id or (key, row["episode_index"]) not in allowed_hits:
                    raise ValueError("Review keys must match the current search run")
                for field in ("reviewed", "is_target_grasp", "quality_ok"):
                    row[field] = boolean(row[field])
                if row["outcome"] not in {"success", "failure", "unknown"}:
                    raise ValueError("Unsupported outcome")
                for field in ("clip_start_s", "clip_end_s"):
                    row[field] = float(row[field])
                    if not math.isfinite(row[field]):
                        raise ValueError("Clip times must be finite")
                if not 0 <= row["clip_start_s"] < row["clip_end_s"]:
                    raise ValueError("Invalid clip interval")
            else:
                key = row["episode_index"]
                row["used_for_training"] = boolean(row["used_for_training"])
                if row["split_assignment"] not in {"train", "validation", "test"}:
                    raise ValueError("Unsupported dataset split")
                if not row["scene_group"].strip():
                    raise ValueError("scene_group must not be empty")
            if key in seen:
                raise ValueError(f"Duplicate CSV key: {name}")
            seen.add(key)
        if name == "episode_policy" and rows and seen != episode_ids:
            raise ValueError("Episode policy must cover every exported episode, including held-out data")
        pending[name] = (columns, rows)
    for name, (columns, rows) in pending.items():
        if not rows:
            continue
        query = (f"INSERT INTO internal.droid100_analysis.{name} "
                 f"({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})")
        with connection.cursor() as cursor:
            cursor.executemany(query, [tuple(row[c] for c in columns) for row in rows])
    return {name: len(rows) for name, (_, rows) in pending.items()}
