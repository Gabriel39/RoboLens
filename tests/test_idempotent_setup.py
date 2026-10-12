"""Repeated setup must preserve data and repair only missing objects."""
from pathlib import Path
import re
import pytest
from pymysql.converters import escape_item
from doris.config import Config
from notebooks.demo_support import initialize_doris

ROOT = Path(__file__).resolve().parents[1]


class SetupConnection:
    escape = staticmethod(escape_item)

    def __init__(self):
        self.config = Config.from_env({})
        self.catalog = False
        self.database = False
        self.tables = {}
        self.calls = []
        self.warehouse = "oss://bucket/robotics/droid100"
        self.missing_source = False
        self.wrong_column = False
        self.ready = True

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def fetchall(self):
        return self.rows

    def result(self, columns, rows):
        self.description = [(name,) for name in columns]
        self.rows = rows

    def execute(self, sql):
        self.calls.append(sql)
        self.description = None
        if sql == "SHOW CATALOGS":
            self.result(["CatalogName"], [(self.config.catalog,)] if self.catalog else [])
        elif sql.startswith("CREATE CATALOG"):
            self.catalog = True
        elif sql.startswith("SHOW CATALOG "):
            self.result(["Key", "Value"], [("type", "lance"), ("warehouse", self.warehouse),
                                           ("lance.catalog.type", "filesystem")])
        elif sql == "SHOW DATABASES FROM internal":
            self.result(["Database"], [(self.config.analysis_database,)] if self.database else [])
        elif sql.startswith("SHOW DATABASES FROM"):
            self.result(["Database"], [(self.config.database,)])
        elif sql.startswith("SHOW TABLES FROM `internal`"):
            self.result(["Tables"], [(name,) for name in self.tables])
        elif sql.startswith("SHOW TABLES FROM"):
            names = list(self.config.source_tables.values())
            self.result(["Tables"], [(name,) for name in (names[1:] if self.missing_source else names)])
        elif sql.startswith("SELECT complete"):
            self.result(["complete"], [(self.ready,)])
        elif sql.startswith("CREATE DATABASE"):
            self.database = True
        elif sql.startswith("CREATE TABLE"):
            name = re.search(r"CREATE TABLE IF NOT EXISTS `internal`\.`\w+`\.`(\w+)`", sql)[1]
            if name not in self.tables:
                self.tables[name] = sql
        elif sql.startswith("DESC "):
            name = sql.split("`")[-2]
            columns = re.findall(r"^\s+([a-z_]+)\s+([A-Z]+(?:\(\d+\))?)([^\n]*)", self.tables[name], re.MULTILINE)
            self.result(["Field", "Type", "Null"], [(name, "INT" if self.wrong_column else dtype,
                         "NO" if "NOT NULL" in attrs else "YES") for name, dtype, attrs in columns])
        elif sql.startswith("SHOW CREATE TABLE"):
            name = sql.split("`")[-2]
            self.result(["Table", "Create Table"], [(name, self.tables[name])])
        elif sql.startswith("SHOW INDEX"):
            self.result(["Key_name"], [("vector_index",)])
        else:
            raise AssertionError("Unexpected SQL: " + sql)


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.delenv("OSS_SECURITY_TOKEN", raising=False)
    return SetupConnection()


def setup(connection):
    return initialize_doris(connection, ROOT, "oss://bucket/robotics/droid100", connection.config)


def test_setup_twice_and_repair_partial_setup(connection):
    setup(connection)
    assert connection.catalog and connection.database and len(connection.tables) == 3
    original = dict(connection.tables)
    connection.calls.clear()
    setup(connection)
    assert connection.tables == original
    assert not any(sql.startswith("CREATE") for sql in connection.calls)
    del connection.tables["grasp_review"]
    connection.calls.clear()
    setup(connection)
    assert connection.tables == original
    creates = [sql for sql in connection.calls if sql.startswith("CREATE")]
    assert len(creates) == 1 and "`grasp_review`" in creates[0]
    assert not any("DROP " in sql or "TRUNCATE " in sql for sql in connection.calls)


def test_wrong_catalog_never_creates_analysis_tables(connection):
    connection.catalog = True
    connection.warehouse = "oss://different-bucket/data"
    with pytest.raises(ValueError, match="different source"):
        setup(connection)
    assert not any(sql.startswith("CREATE") for sql in connection.calls)


def test_incompatible_table_is_not_replaced(connection):
    setup(connection)
    original = dict(connection.tables)
    connection.calls.clear()
    connection.wrong_column = True
    with pytest.raises(ValueError, match="incompatible"):
        setup(connection)
    assert connection.tables == original
    assert not any(sql.startswith("CREATE") for sql in connection.calls)


@pytest.mark.parametrize("problem", ["missing_source", "not_ready"])
def test_missing_source_or_incomplete_export_does_not_create_business_tables(connection, problem):
    connection.catalog = True
    connection.missing_source = problem == "missing_source"
    connection.ready = problem != "not_ready"
    with pytest.raises(ValueError, match="not yet available|incomplete"):
        setup(connection)
    assert not connection.database and not connection.tables
