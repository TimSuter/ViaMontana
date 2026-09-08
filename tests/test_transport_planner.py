import json
import sqlite3

import pytest
from fastapi import HTTPException

import app


@pytest.fixture
def planner_data(tmp_path, monkeypatch):
    routes = tmp_path / 'routes.sqlite'
    access = tmp_path / 'access.sqlite'
    monkeypatch.setattr(app, 'DEFAULT_DATABASE', routes)
    monkeypatch.setattr(app, 'DEFAULT_ACCESS_DATABASE', access)
    with sqlite3.connect(routes) as connection:
        connection.execute('CREATE TABLE routes (start_hut TEXT, destination_hut TEXT, duration_h REAL, distance_km REAL, ascent_m REAL, descent_m REAL, max_hiking_category TEXT, difficulty_status TEXT, geometry_wkt TEXT)')
        connection.execute("INSERT INTO routes VALUES ('A', 'B', 5, 10, 700, 400, 'Bergwanderweg', 'mapped', 'LINESTRING (8.1 46.1, 8.2 46.2)')")
    with sqlite3.connect(access) as connection:
        connection.execute('CREATE TABLE access_routes (hut TEXT PRIMARY KEY, payload TEXT)')
        for name, lat, lon in [('A', 46.1, 8.1), ('B', 46.2, 8.2)]:
            data = dict(hut_name=name, pt_stop_name=f'Stop {name}', hut_latitude=lat, hut_longitude=lon,
                        pt_stop_latitude=46, pt_stop_longitude=8, duration_h=3, exit_duration_h=2,
                        distance_km=6, ascent_m=900, descent_m=100, max_hiking_category='Bergwanderweg',
                        difficulty_status='mapped', geometry_wkt=f'LINESTRING (8 46, {lon} {lat})')
            connection.execute('INSERT INTO access_routes VALUES (?, ?)', (name, json.dumps(data)))
    return access


def test_two_days_is_one_overnight_and_two_access_legs(planner_data):
    response = app.search_routes('A', 2, include_geometry=True)
    trip, = response.itineraries
    assert trip.huts == ['A']
    assert (trip.days, trip.overnight_stays) == (2, 1)
    assert [leg.kind for leg in trip.legs] == ['arrival', 'departure']
    assert [leg.start_hut for leg in trip.legs] == ['Stop A', 'A']
    assert trip.total_duration_h == 5
    assert trip.total_distance_km == 12
    assert trip.total_ascent_m == trip.total_descent_m == 1000
    assert app.parse_linestring_coordinates(trip.legs[1].geometry_wkt)[0] == (8.1, 46.1)


def test_three_days_adds_one_hut_leg_and_counts_all_totals(planner_data):
    trip, = app.search_routes('A', 3).itineraries
    assert trip.huts == ['A', 'B']
    assert (trip.days, trip.overnight_stays) == (3, 2)
    assert [leg.kind for leg in trip.legs] == ['arrival', 'hut_to_hut', 'departure']
    assert trip.legs[-1].destination_hut == 'Stop B'
    assert trip.total_duration_h == 10
    assert trip.total_distance_km == 22
    assert trip.total_ascent_m == 1700
    assert trip.total_descent_m == 1400
    assert all(leg.geometry_wkt is None for leg in trip.legs)
    assert app.access_leg('B', 'departure').geometry_wkt


@pytest.mark.parametrize('limits', [dict(min_duration_h=2.5), dict(max_duration_h=2.5), dict(max_elevation_change_m=950)])
def test_arrival_and_departure_must_meet_daily_limits(planner_data, limits):
    assert app.search_routes('A', 2, **limits).result_count == 0


def test_missing_exit_excludes_incomplete_trip(planner_data):
    with sqlite3.connect(planner_data) as connection:
        connection.execute("DELETE FROM access_routes WHERE hut='B'")
    assert app.search_routes('A', 3).result_count == 0
    assert app.search_routes('A', 2).result_count == 1


def test_missing_arrival_and_no_one_day_trips(planner_data):
    assert app.search_routes('unknown', 2).result_count == 0
    with pytest.raises(HTTPException) as error:
        app.search_routes('A', 1)
    assert error.value.status_code == 422
