"""Configuration must route setup, search, labels and physical Lance data together."""
from pathlib import Path
import json
import pytest
from doris.config import Config, load_config

from test_export import source
from test_notebook import labels

ROOT = Path(__file__).resolve().parents[1]


def test_config_rejects_unsafe_or_ambiguous_names():
    for values in ({"DORIS_CATALOG": "x; DROP TABLE y"},
                   {"DORIS_TABLE_GRASP_HITS": "grasp_review"},
                   {"LANCE_TABLE_FRAMES": "media"},
                   {"DORIS_QUERY_PORT": "70000"}):
        with pytest.raises(ValueError):
            Config.from_env(values)


def test_file_reload_and_literal_password(tmp_path, monkeypatch):
    monkeypatch.setenv("DORIS_HOST", "outside")
    monkeypatch.delenv("DORIS_PASSWORD", raising=False)
    path = tmp_path / ".env"
    path.write_text("DORIS_HOST=first\nDORIS_PASSWORD='a${HOME} # secret'\n")
    first = load_config(path)
    assert first.host == "first"
    assert first.password == "a${HOME} # secret"
    assert first.password not in repr(first)
    path.write_text("DORIS_HOST=second\n")
    second = load_config(path)
    assert second.host == "second" and second.password is None
    path.write_text("")
    assert load_config(path).host == "outside"


def custom_config():
    return Config.from_env({
        "DORIS_HOST": "192.0.2.15", "DORIS_QUERY_PORT": "19030",
        "DORIS_USER": "demo", "DORIS_PASSWORD": "test-secret",
        "DORIS_CATALOG": "robot_data", "DORIS_DATABASE": "custom_db",
        "DORIS_ANALYSIS_DATABASE": "robot_analysis",
        "DORIS_TABLE_GRASP_HITS": "hits_v2",
        "DORIS_TABLE_GRASP_REVIEW": "reviews_v2",
        "DORIS_TABLE_EPISODE_POLICY": "policy_v2",
        "LANCE_TABLE_FRAME_EMBEDDINGS": "vectors_v2",
    })


def test_connection_uses_config(monkeypatch):
    import pymysql
    from notebooks.demo_support import connect_doris
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: kwargs)
    result = connect_doris(config=custom_config())
    assert (result["host"], result["port"], result["user"], result["password"]) == (
        "192.0.2.15", 19030, "demo", "test-secret")


def test_setup_and_queries_use_the_same_names(tmp_path, monkeypatch):
    import numpy as np
    import pymysql.converters
    from doris.prepare_query import render
    from notebooks.demo_support import initialize_doris
    from test_notebook import RecordingConnection
    config = custom_config()
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.delenv("OSS_SECURITY_TOKEN", raising=False)
    connection = RecordingConnection()
    connection.escape = pymysql.converters.escape_item
    initialize_doris(connection, ROOT, "oss://test-bucket/data", config)
    ddl = "\n".join(connection.calls)
    assert "CREATE CATALOG `robot_data`" in ddl
    assert "CREATE TABLE `internal`.`robot_analysis`.`hits_v2`" in ddl
    assert "`robot_data`.`custom_db`.`vectors_v2`" in ddl
    reference = {"embedding": (np.ones(768) / np.sqrt(768)).tolist(),
                 "episode_index": 0, "camera": "observation.images.wrist_image_left"}
    files = render(reference, tmp_path / "queries", "config_test", config)
    sql = "\n".join(p.read_text() for p in files)
    assert '"table" = "robot_data.custom_db.vectors_v2"' in sql
    for table in config.analysis_tables.values():
        assert f"`internal`.`robot_analysis`.`{table}`" in sql
    assert "droid100" not in sql and "{{" not in sql and config.password not in sql
    assert config.uri("oss://bucket/data/", "frame_embeddings") == "oss://bucket/data/vectors_v2.lance"


def test_custom_lance_names_roundtrip(source, tmp_path, monkeypatch):
    import lance
    from test_export import TestEncoders
    from pipeline.export_droid import export, build_indexes
    from doris.prepare_query import read_reference
    # A real tiny export checks writer, index builder and reference reader agree.
    monkeypatch.setenv("LANCE_TABLE_FRAME_EMBEDDINGS", "vectors_v2")
    monkeypatch.setenv("LANCE_TABLE_MEDIA", "videos_v2")
    output = str(tmp_path / "out")
    export(source, output, {}, tmp_path, TestEncoders(), frame_stride=1)
    assert not (tmp_path / "out/frame_embeddings.lance").exists()
    assert lance.dataset(output + "/vectors_v2.lance").count_rows() > 0
    build_indexes(output, {}, index_type="IVF_FLAT")
    reference = read_reference(output, f"0:{source.cameras[0]}:0", {})
    assert len(reference["embedding"]) == 768
    manifest = lance.dataset(output + "/export_manifest.lance").to_table()["config_json"][0].as_py()
    assert json.loads(manifest)["table_names"]["media"] == "videos_v2"


def test_annotations_use_configured_tables(labels):
    import pandas as pd
    from test_notebook import RecordingConnection, write_labels
    from notebooks.demo_support import load_annotations
    connection = RecordingConnection()
    paths = write_labels(labels)
    hits = pd.DataFrame([{"sample_id": "0:observation.images.wrist_image_left:0", "episode_index": 0}])
    load_annotations(connection, ROOT, *paths, "test_001", hits, {0}, custom_config())
    assert "`internal`.`robot_analysis`.`reviews_v2`" in connection.calls[0][0]
    assert "`internal`.`robot_analysis`.`policy_v2`" in connection.calls[1][0]
