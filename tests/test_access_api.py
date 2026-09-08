import json
import sqlite3

import pytest
from fastapi import HTTPException

import app


@pytest.fixture
def access_database(tmp_path, monkeypatch):
    path = tmp_path / "access.sqlite"
    monkeypatch.setattr(app, "DEFAULT_ACCESS_DATABASE", path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE access_routes (hut TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        for hut, geometry in [("Valley hut", "LINESTRING (8 46, 8.1 46.1)"), ("Ridge hut", None)]:
            connection.execute("INSERT INTO access_routes VALUES (?, ?)", (hut, json.dumps({"hut_name": hut, "geometry_wkt": geometry})))
    return path


def test_access_huts_include_results_without_paths(access_database):
    assert app.access_huts() == {"huts": ["Ridge hut", "Valley hut"]}
    assert app.access_route("Valley hut")["geometry_wkt"].startswith("LINESTRING")
    assert app.access_route("Ridge hut")["geometry_wkt"] is None


def test_unknown_hut_uses_exact_parameterized_lookup(access_database):
    with pytest.raises(HTTPException) as error:
        app.access_route("' OR 1=1 --")
    assert error.value.status_code == 404


def test_missing_database_does_not_create_empty_file(tmp_path, monkeypatch):
    path = tmp_path / "missing.sqlite"
    monkeypatch.setattr(app, "DEFAULT_ACCESS_DATABASE", path)
    with pytest.raises(HTTPException) as error:
        app.access_huts()
    assert error.value.status_code == 503
    assert not path.exists()
