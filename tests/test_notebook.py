"""Validate notebook assets and the SQL/annotation boundary without a live server."""
import csv
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sqlparse")
from notebooks.demo_support import load_annotations, run_sql

ROOT = Path(__file__).resolve().parents[1]


class RecordingConnection:
    def __init__(self):
        self.calls = []
        self.description = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query):
        self.calls.append(query)
        self.description = [("value",)] if "SELECT" in query else None

    def fetchall(self):
        return [("a;b",)]

    def executemany(self, query, values):
        self.calls.append((query, values))


def test_notebook_and_all_diagrams_are_portable():
    implementations = []
    for suffix, asset_directory in (("", "assets"), (".en", "assets/en")):
        notebook = json.loads((ROOT / f"notebooks/droid100_end_to_end{suffix}.ipynb").read_text())
        assert notebook["nbformat"] == 4
        diagrams, code_cells = [], []
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                source = "".join(cell["source"])
                assert source.isascii()
                compile(source, "<notebook>", "exec")
                code_cells.append(source.replace("assets/en/", "assets/"))
                if "diagram" in cell["metadata"].get("tags", []):
                    diagrams.append(source)
        assert len(diagrams) == 9
        implementations.append(code_cells)
        for path in (ROOT / "notebooks" / asset_directory).glob("*.svg"):
            assert any(path.name in code for code in diagrams)
            ElementTree.parse(path)
    assert implementations[0] == implementations[1]
    assert (ROOT / "notebooks/demo_support.py").read_text().isascii()


def test_sql_runner_preserves_semicolons_inside_literals_and_skips_comments():
    connection = RecordingConnection()
    frames = run_sql(connection, "-- first\nSELECT 'a;b'; INSERT INTO x VALUES (1); -- final")
    assert len(connection.calls) == 2
    assert "'a;b'" in connection.calls[0]
    assert frames[0].iloc[0]["value"] == "a;b"


@pytest.fixture
def labels(tmp_path):
    review = {
        "run_id": "test_001", "sample_id": "0:observation.images.wrist_image_left:0",
        "episode_index": "0", "reviewed": "true", "is_target_grasp": "true",
        "quality_ok": "true", "object_type": "marker", "scene_type": "desk",
        "outcome": "failure", "failure_stage": "contact", "failure_reason": "slip",
        "clip_start_s": "0", "clip_end_s": "1", "label_source": "human_test",
    }
    policy = {"episode_index": "0", "scene_group": "session_001",
              "split_assignment": "train", "used_for_training": "false"}
    return tmp_path, review, policy


def write_labels(fixture):
    directory, review, policy = fixture
    paths = []
    for name, row in [("grasp_review", review), ("episode_policy", policy)]:
        path = directory / f"{name}.csv"
        columns = (ROOT / "doris/templates" / f"{name}.csv").read_text().strip().split(",")
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerow(row)
        paths.append(path)
    return paths


def ingest(connection, labels, episode_ids=None):
    paths = write_labels(labels)
    hits = pd.DataFrame([{"sample_id": "0:observation.images.wrist_image_left:0", "episode_index": 0}])
    return load_annotations(connection, ROOT, *paths, "test_001", hits,
                            {0} if episode_ids is None else episode_ids)


def test_annotation_inserts_bind_values_without_sql_interpolation(labels):
    labels[1]["failure_reason"] = "object's edge; SELECT 1"
    connection = RecordingConnection()
    assert ingest(connection, labels) == {"grasp_review": 1, "episode_policy": 1}
    statement, values = connection.calls[0]
    assert "object's edge" not in statement
    assert "%s" in statement
    assert values[0][10] == "object's edge; SELECT 1"
    assert values[0][3] is True
    assert connection.calls[1][1][0][-1] is False


@pytest.mark.parametrize("field,value", [
    ("run_id", "other_run"), ("episode_index", "99"),
    ("reviewed", "yes"), ("clip_end_s", "nan"),
    ("clip_end_s", "0"), ("outcome", "maybe"),
])
def test_invalid_review_is_rejected_before_any_write(labels, field, value):
    labels[1][field] = value
    connection = RecordingConnection()
    with pytest.raises(ValueError):
        ingest(connection, labels)
    assert connection.calls == []


def test_incomplete_policy_is_rejected_before_review_write(labels):
    connection = RecordingConnection()
    with pytest.raises(ValueError, match="every exported episode"):
        ingest(connection, labels, episode_ids={0, 1})
    assert connection.calls == []


@pytest.mark.parametrize("endpoint,virtual,region,expected", [
    (None, None, "us-east-1", "https://bucket.s3.us-east-1.amazonaws.com"),
    (None, None, "cn-north-1", "https://bucket.s3.cn-north-1.amazonaws.com.cn"),
    ("https://minio.example.com", "false", "us-east-1", "https://minio.example.com"),
    ("https://bucket.s3.example.com", "true", "us-east-1", "https://bucket.s3.example.com"),
])
def test_s3_notebook_catalog_mapping(monkeypatch, endpoint, virtual, region, expected):
    from notebooks.demo_support import initialize_doris
    import pymysql.converters
    for key in ("AWS_ENDPOINT", "AWS_SESSION_TOKEN", "AWS_VIRTUAL_HOSTED_STYLE_REQUEST", "AWS_ALLOW_HTTP"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_REGION", region)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    if endpoint:
        monkeypatch.setenv("AWS_ENDPOINT", endpoint)
    if virtual:
        monkeypatch.setenv("AWS_VIRTUAL_HOSTED_STYLE_REQUEST", virtual)
    connection = RecordingConnection()
    connection.escape = pymysql.converters.escape_item
    initialize_doris(connection, ROOT, "s3://bucket/robotics/droid100")
    import sqlparse
    catalog = sqlparse.format(connection.calls[0], strip_comments=True)
    assert expected in catalog
    assert '"fs.s3.support" = "true"' in catalog
    assert '"use_path_style" = "' + str(virtual == "false").lower() + '"' in catalog
    assert "oss." not in catalog and "REPLACE_" not in catalog
    assert "CREATE TABLE internal.droid100_analysis.grasp_hits" in "\n".join(connection.calls)


def test_s3_notebook_temporary_credentials_do_not_create_catalog(monkeypatch):
    from notebooks.demo_support import initialize_doris
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test-token")
    connection = RecordingConnection()
    with pytest.raises(ValueError, match="RUN_SETUP=False"):
        initialize_doris(connection, ROOT, "s3://bucket/robotics/droid100")
    assert connection.calls == []


def test_storage_setup_templates_share_analysis_schema():
    marker = "SHOW TABLES FROM"
    oss = (ROOT / "doris/sql/01_setup.sql").read_text().split(marker, 1)[1]
    s3 = (ROOT / "doris/sql/01_setup_s3.sql").read_text().split(marker, 1)[1]
    assert oss == s3
