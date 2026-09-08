import sqlite3

import pytest
from fastapi import HTTPException

import app


@pytest.fixture
def route_database(tmp_path, monkeypatch):
    path = tmp_path / "routes.sqlite"
    monkeypatch.setattr(app, "DEFAULT_DATABASE", path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE routes (start_hut TEXT, destination_hut TEXT, duration_h REAL, distance_km REAL, ascent_m REAL, descent_m REAL, max_hiking_category TEXT, difficulty_status TEXT, geometry_wkt TEXT)")
        connection.executemany("INSERT INTO routes VALUES (?, ?, ?, 5, ?, ?, 'Wanderweg', 'mapped', 'LINESTRING (8 46, 8.1 46.1)')", [
            ("A", "B", 4, 500, 100), ("A", "C", 8, 900, 200),
            ("A", "D", 9, 500, 100), ("A", "E", 5, 1500, 1000),
            ("B", "A", 3, 100, 500),
        ])
    return path


def test_next_huts_apply_inclusive_filters_and_return_geometries(route_database):
    legs = app.next_huts("A", 4, 8, 600, 1100)
    assert [leg.destination_hut for leg in legs] == ["B", "C"]
    assert all(leg.geometry_wkt for leg in legs)
    assert app.next_huts("missing") == []


def test_next_huts_no_search_result_cap(route_database):
    with sqlite3.connect(route_database) as connection:
        connection.executemany("INSERT INTO routes VALUES ('Many', ?, 5, 5, 500, 100, 'Wanderweg', 'mapped', 'LINESTRING (8 46, 8.1 46.1)')", [(str(i),) for i in range(205)])
    assert len(app.next_huts("Many")) == 205


def test_next_huts_invalid_limits(route_database):
    with pytest.raises(HTTPException) as error:
        app.next_huts("A", 8, 4)
    assert error.value.status_code == 400


def test_exit_reverses_geometry_and_uses_outbound_time(monkeypatch):
    inbound = {"geometry_wkt": "LINESTRING (8 46, 8.1 46.1)", "duration_h": 4, "exit_duration_h": 2.5, "ascent_m": 900, "descent_m": 100}
    monkeypatch.setattr(app, "access_route", lambda hut: dict(inbound))
    outbound = app.exit_route("A")
    assert app.parse_linestring_coordinates(outbound["geometry_wkt"]) == [(8.1, 46.1), (8, 46)]
    assert (outbound["duration_h"], outbound["ascent_m"], outbound["descent_m"]) == (2.5, 100, 900)


def test_exit_unavailable(monkeypatch):
    monkeypatch.setattr(app, "access_route", lambda hut: {"geometry_wkt": None})
    with pytest.raises(HTTPException) as error:
        app.exit_route("A")
    assert error.value.status_code == 404


def test_suggestions_keep_matches_then_rank_near_misses(route_database):
    legs = app.next_huts("A", 4, 8, 600, 1100, include_suggestions=True)
    assert [(leg.destination_hut, leg.is_suggestion) for leg in legs] == [
        ("B", False), ("C", False), ("D", True), ("E", True)]


def test_no_matches_returns_three_closest_suggestions(route_database):
    legs = app.next_huts("A", 6, 7, 600, 1100, include_suggestions=True)
    assert [leg.destination_hut for leg in legs] == ["C", "B", "D"]
    assert all(leg.is_suggestion and leg.geometry_wkt for leg in legs)


def test_visited_huts_excluded_before_selecting_suggestions(route_database):
    legs = app.next_huts("A", 4, 8, 600, 1100, include_suggestions=True, excluded_huts=["B", "D"])
    assert [leg.destination_hut for leg in legs] == ["C", "E"]
    assert app.next_huts("B", include_suggestions=True, excluded_huts=["A"]) == []


def test_three_matches_need_no_alternatives(route_database):
    legs = app.next_huts("A", 4, 9, 600, 1100, include_suggestions=True)
    assert [leg.destination_hut for leg in legs] == ["B", "C", "D"]
    assert not any(leg.is_suggestion for leg in legs)
