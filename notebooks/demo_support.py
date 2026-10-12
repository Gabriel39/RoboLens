"""Small adapters for the live notebook; production export and SQL stay shared."""
from __future__ import annotations

import csv
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import traceback

import pandas as pd
import sqlparse

from doris.config import Config


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
        exit_code = process.wait()
        if exit_code:
            raise RuntimeError(f"Export exited with code {exit_code}; full subprocess output is shown above")
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


def connect_doris(password: str | None = None, config: Config | None = None):
    """Connect with a finite query timeout and without multi-statement execution."""
    import pymysql
    config = config or Config.from_env()
    password = config.password if password is None else password
    if password is None:
        raise ValueError("Set DORIS_PASSWORD in .env or provide a password")
    options = dict(host=config.host, port=config.query_port, user=config.user,
                   password=password, charset="utf8mb4", autocommit=True,
                   connect_timeout=10, read_timeout=600, write_timeout=120)
    if config.ssl_ca:
        options.update(ssl_ca=config.ssl_ca, ssl_verify_cert=True, ssl_verify_identity=True)
    return pymysql.connect(**options)


def redact_diagnostics(text: str, connection, sensitive_values=()) -> str:
    """Keep diagnostics readable while masking configured credentials."""
    from pymysql.converters import escape_string
    values = list(sensitive_values)
    values += [value for key, value in os.environ.items()
               if any(part in key for part in ("PASSWORD", "SECRET", "TOKEN", "ACCESS_KEY"))]
    values.append(getattr(connection, "password", None))
    variants = set()
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if not isinstance(value, str) or not value:
            continue
        current = {value}
        # SQL escaping followed by exception repr may escape the value twice.
        for _ in range(3):
            current |= {escape_string(v) for v in current} | {repr(v)[1:-1] for v in current}
        variants.update(current)
    if variants:
        # One pass prevents a short secret from corrupting the redaction marker.
        pattern = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
        text = re.sub(pattern, "[REDACTED]", text)
    return text


def run_sql(connection, text: str, *, sensitive_values=()) -> list[pd.DataFrame]:
    """Execute sequentially; report the failing SQL and full sanitized traceback."""
    frames = []
    statement_number = 0
    for statement in sqlparse.split(text):
        if not sqlparse.format(statement, strip_comments=True).strip().strip(";"):
            continue
        statement_number += 1
        try:
            with connection.cursor() as cursor:
                cursor.execute(statement)
                if cursor.description:
                    frames.append(pd.DataFrame(cursor.fetchall(), columns=[item[0] for item in cursor.description]))
        except Exception:
            report = (f"Doris SQL statement #{statement_number} failed. "
                      "Earlier statements may already be committed.\n"
                      f"Failed SQL:\n{statement}\n\nOriginal traceback:\n{traceback.format_exc()}")
            # Embed the complete sanitized chain instead of exposing the raw cause.
            raise RuntimeError(redact_diagnostics(report, connection, sensitive_values)) from None
    return frames


def ensure_doris_objects(connection, text: str, dataset_root: str, config: Config,
                         sensitive_values=()) -> list[pd.DataFrame]:
    """Create missing objects and validate existing objects without replacing data."""
    def query(sql):
        result = run_sql(connection, sql, sensitive_values=sensitive_values)
        return result[0] if result else pd.DataFrame()
    statements = [sqlparse.format(part, strip_comments=True).strip()
                  for part in sqlparse.split(text)]
    catalog_ddl = next(part for part in statements if part.startswith("CREATE CATALOG"))
    catalog_rows = query("SHOW CATALOGS")
    existing = set(catalog_rows["CatalogName"].astype(str))
    if config.catalog not in existing:
        query(catalog_ddl)
        print(f"Catalog {config.catalog}: created")
    else:
        print(f"Catalog {config.catalog}: exists; validating")
    properties = query(f"SHOW CATALOG `{config.catalog}`")
    properties = dict(zip(properties["Key"], properties["Value"]))
    if (properties.get("type", "").lower() != "lance"
            or properties.get("warehouse", "").rstrip("/") != dataset_root.rstrip("/")
            or properties.get("lance.catalog.type", "filesystem") != "filesystem"):
        raise ValueError(f"Existing catalog {config.catalog} points to a different source; "
                         "choose another DORIS_CATALOG or matching NOTEBOOK_DATASET_ROOT")
    databases = query(f"SHOW DATABASES FROM `{config.catalog}`")
    if config.database not in set(databases.iloc[:, 0].astype(str)):
        raise ValueError(f"Lance database {config.database} is not available; check DORIS_DATABASE and the export path")
    tables = query(f"SHOW TABLES FROM `{config.catalog}`.`{config.database}`")
    missing = set(config.source_tables.values()) - set(tables.iloc[:, 0].astype(str))
    if missing:
        raise ValueError("Lance tables are not yet available: " + ", ".join(sorted(missing))
                         + "; complete or resume the export before Doris setup")
    # Confirm export readiness before creating any business tables.
    checkpoint = query(f"SELECT complete FROM {config.source('export_checkpoints')} "
                       "ORDER BY next_episode_offset DESC LIMIT 1")
    indexes = query(f"SELECT complete FROM {config.source('export_index_status')}")
    if checkpoint.empty or indexes.empty or not bool(checkpoint.iloc[0, 0]) or not bool(indexes.iloc[0, 0]):
        raise ValueError("Lance data or indexes are incomplete; run the export again to finish them")
    databases = query("SHOW DATABASES FROM internal")
    if config.analysis_database not in set(databases.iloc[:, 0].astype(str)):
        query(f"CREATE DATABASE IF NOT EXISTS `internal`.`{config.analysis_database}`")
        print(f"Database {config.analysis_database}: created")
    else:
        print(f"Database {config.analysis_database}: reused")
    tables = query(f"SHOW TABLES FROM `internal`.`{config.analysis_database}`")
    existing = set(tables.iloc[:, 0].astype(str))
    for name, table in config.analysis_tables.items():
        qualified = config.analysis(name)
        ddl = next(part for part in statements if part.startswith("CREATE TABLE") and qualified in part)
        if table not in existing:
            query(ddl)
            print(f"Table {qualified}: created")
        else:
            print(f"Table {qualified}: exists; validating")
        description = query(f"DESC {qualified}")
        expected = re.findall(r"^\s+([a-z_]+)\s+([A-Z]+(?:\(\d+\))?)([^\n]*)", ddl, re.MULTILINE)
        actual = {row["Field"]: row for row in description.to_dict("records")}
        def normalized(value):
            return re.sub(r"\s+", "", str(value)).lower().replace("integer", "int")
        if set(actual) != {column for column, _, _ in expected}:
            raise ValueError(f"Existing table {qualified} has different columns; choose another table name")
        for column, dtype, attributes in expected:
            row = actual[column]
            if (normalized(row["Type"]) != normalized(dtype)
                    or str(row["Null"]).upper() != ("NO" if "NOT NULL" in attributes else "YES")):
                raise ValueError(f"Existing table {qualified} has an incompatible {column} column")
        create = query(f"SHOW CREATE TABLE {qualified}")
        expected_key = re.search(r"UNIQUE KEY\(([^)]+)\)", ddl).group(1)
        actual_key = re.search(r"UNIQUE\s+KEY\s*\(([^)]+)\)", str(create.iloc[0, 1]), re.IGNORECASE)
        if actual_key is None or normalized(actual_key.group(1).replace("`", "")) != normalized(expected_key):
            raise ValueError(f"Existing table {qualified} has an incompatible key definition")
    print("Doris objects are ready; existing data was preserved")
    return [query(f"SHOW INDEX FROM {config.source('frame_embeddings')}")]


def initialize_doris(connection, project: Path, dataset_root: str, config: Config | None = None) -> list[pd.DataFrame]:
    """Fill the trusted setup template in memory without displaying credentials."""
    from pipeline.export_droid import storage_options
    config = config or Config.from_env()
    options = storage_options(dataset_root, None, None)
    if dataset_root.startswith("s3://"):
        from urllib.parse import urlparse
        if options.get("aws_session_token") or not options.get("aws_access_key_id"):
            raise ValueError("Configure an STS/IAM-enabled S3 catalog separately and leave RUN_SETUP=False")
        bucket = urlparse(dataset_root).netloc
        region = options["aws_region"]
        virtual = options["aws_virtual_hosted_style_request"] == "true"
        suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
        endpoint = options.get("aws_endpoint") or f"https://{bucket + '.' if virtual else ''}s3.{region}.{suffix}"
        text = (project / "doris/sql/01_setup_s3.sql").read_text()
        replacements = {
            '"s3://REPLACE_BUCKET/robotics/droid100"': dataset_root,
            '"https://REPLACE_BUCKET.s3.us-east-1.amazonaws.com"': endpoint,
            '"us-east-1"': region,
            '"REPLACE_ACCESS_KEY"': options["aws_access_key_id"],
            '"REPLACE_SECRET_KEY"': options["aws_secret_access_key"],
        }
        text = text.replace('"use_path_style" = "false"',
                            '"use_path_style" = "' + str(not virtual).lower() + '"')
    elif dataset_root.startswith("oss://"):
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
    else:
        raise ValueError("Live Doris setup requires an S3 or OSS dataset root")
    text = config.sql(text)
    for token, value in replacements.items():
        # Use the driver's escaping rather than concatenating raw SQL literals.
        text = text.replace(token, connection.escape(value))
    sensitive_values = [config.password] + [
        value for key, value in options.items()
        if any(part in key for part in ("secret", "token", "access_key"))
    ]
    return ensure_doris_objects(connection, text, dataset_root, config, sensitive_values)


def boolean(value: str) -> bool:
    """Reject ambiguous labels instead of coercing nonempty strings to True."""
    lowered = value.strip().lower()
    if lowered not in {"true", "false"}:
        raise ValueError("Boolean CSV fields must be true or false")
    return lowered == "true"


def load_annotations(connection, project: Path, review_path: Path, policy_path: Path,
                     run_id: str, hits: pd.DataFrame, episode_ids: set[int], config: Config | None = None) -> dict:
    """Validate small review CSVs before parameterized Doris inserts.

    No labels are synthesized. Both CSVs are validated before either write starts.
    Writes are separate autocommit statements, not a cross-table transaction.
    Unique keys make rerunning unchanged records an upsert.
    """
    config = config or Config.from_env()
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
        query = (f"INSERT INTO {config.analysis(name)} "
                 f"({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})")
        with connection.cursor() as cursor:
            cursor.executemany(query, [tuple(row[c] for c in columns) for row in rows])
    return {name: len(rows) for name, (_, rows) in pending.items()}
