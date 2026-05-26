from __future__ import annotations

import sqlite3
from functools import lru_cache
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


DEFAULT_DATABASE = Path("data") / "hiking_routes.sqlite"
DEFAULT_HUTS = Path("my_geodataframe.pkl")
STATIC_DIR = Path(__file__).parent / "web"
MEDIA_DIR = Path(__file__).parent / "media"
MAX_DAYS = 10
DEFAULT_RESULT_LIMIT = 50
MAX_RESULT_LIMIT = 200
EARTH_RADIUS_KM = 6371.0088
GEOMETRY_KEY_PRECISION = 5


def normalize_result_limit(value: str | None) -> int:
    if value in (None, "", "null"):
        return DEFAULT_RESULT_LIMIT
    try:
        limit = int(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="limit must be an integer.") from error
    if not 1 <= limit <= MAX_RESULT_LIMIT:
        raise HTTPException(status_code=422, detail=f"limit must be between 1 and {MAX_RESULT_LIMIT}.")
    return limit


class RouteLeg(BaseModel):
    start_hut: str
    destination_hut: str
    duration_h: float
    distance_km: float
    ascent_m: float
    descent_m: float
    elevation_change_m: float
    max_hiking_category: str
    difficulty_status: str
    geometry_wkt: str | None = None


class HutMarker(BaseModel):
    hut: str
    latitude: float
    longitude: float
    altitude_m: float | None = None


class Itinerary(BaseModel):
    huts: list[str]
    days: int
    target_duration_h: float
    duration_match_score: float
    average_daily_duration_h: float
    total_duration_h: float
    total_distance_km: float
    total_ascent_m: float
    total_descent_m: float
    total_elevation_change_m: float
    max_hiking_category: str
    difficulty_status: str
    legs: list[RouteLeg]


class SearchResponse(BaseModel):
    start_hut: str
    days: int
    result_count: int
    itineraries: list[Itinerary]


HIKING_CATEGORY_RANK = {
    "unknown": 0,
    "Wanderweg": 1,
    "Bergwanderweg": 2,
    "Alpinwanderweg": 3,
}


app = FastAPI(title="ViaMontana API")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def database_path() -> Path:
    return DEFAULT_DATABASE


def connect_database() -> sqlite3.Connection:
    path = database_path()
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Route database not found at {path}. Run fill_hiking_route_database.py first.",
        )
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


@lru_cache(maxsize=1)
def hut_coordinate_lookup() -> dict[str, HutMarker]:
    if not DEFAULT_HUTS.exists():
        return {}

    from find_nearby_huts import load_huts, marker_points

    huts = load_huts(DEFAULT_HUTS)
    points = marker_points(huts)
    result: dict[str, HutMarker] = {}
    for index, row in huts.iterrows():
        name = row.get("name")
        if name is None:
            continue
        point = points.loc[index]
        result[str(name)] = HutMarker(
            hut=str(name),
            latitude=float(point.y),
            longitude=float(point.x),
            altitude_m=first_optional_float(
                row.get("ele"), row.get("elevation"), row.get("alt"), row.get("quota")
            ),
        )
    return result


def first_optional_float(*values: object) -> float | None:
    for value in values:
        parsed = parse_optional_float(value)
        if parsed is not None:
            return parsed
    return None


def parse_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except TypeError:
        pass
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def route_leg_from_row(row: sqlite3.Row, include_geometry: bool) -> RouteLeg:
    ascent_m = float(row["ascent_m"])
    descent_m = float(row["descent_m"])
    return RouteLeg(
        start_hut=str(row["start_hut"]),
        destination_hut=str(row["destination_hut"]),
        duration_h=float(row["duration_h"]),
        distance_km=float(row["distance_km"]),
        ascent_m=ascent_m,
        descent_m=descent_m,
        elevation_change_m=ascent_m + descent_m,
        max_hiking_category=str(row["max_hiking_category"]),
        difficulty_status=str(row["difficulty_status"]),
        geometry_wkt=str(row["geometry_wkt"]) if include_geometry else None,
    )


def parse_linestring_endpoints(geometry_wkt: str) -> tuple[tuple[float, float], tuple[float, float]]:
    coordinates = parse_linestring_coordinates(geometry_wkt)
    return coordinates[0], coordinates[-1]


def parse_linestring_coordinates(geometry_wkt: str) -> list[tuple[float, float]]:
    prefix = "LINESTRING"
    text = geometry_wkt.strip()
    if not text.upper().startswith(prefix):
        raise ValueError("Only LINESTRING route geometries are supported.")
    coordinate_text = text[text.find("(") + 1 : text.rfind(")")]
    coordinates = []
    for pair in coordinate_text.split(","):
        values = pair.strip().split()
        if len(values) < 2:
            continue
        coordinates.append((float(values[0]), float(values[1])))
    if len(coordinates) < 2:
        raise ValueError("Route geometry does not contain enough coordinates.")
    return coordinates


def haversine_km(start: tuple[float, float], end: tuple[float, float]) -> float:
    start_lon, start_lat = start
    end_lon, end_lat = end
    delta_lat = radians(end_lat - start_lat)
    delta_lon = radians(end_lon - start_lon)
    start_lat_rad = radians(start_lat)
    end_lat_rad = radians(end_lat)
    value = (
        sin(delta_lat / 2) ** 2
        + cos(start_lat_rad) * cos(end_lat_rad) * sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * asin(sqrt(value))


def segment_key(
    start: tuple[float, float], end: tuple[float, float]
) -> tuple[tuple[float, float], tuple[float, float]]:
    rounded_start = (round(start[0], GEOMETRY_KEY_PRECISION), round(start[1], GEOMETRY_KEY_PRECISION))
    rounded_end = (round(end[0], GEOMETRY_KEY_PRECISION), round(end[1], GEOMETRY_KEY_PRECISION))
    return tuple(sorted((rounded_start, rounded_end)))


@lru_cache(maxsize=10000)
def route_segments(
    geometry_wkt: str,
) -> tuple[tuple[tuple[tuple[float, float], tuple[float, float]], float], ...]:
    coordinates = parse_linestring_coordinates(geometry_wkt)
    return tuple(
        (segment_key(start, end), haversine_km(start, end))
        for start, end in zip(coordinates, coordinates[1:])
    )


def route_overlap_ratio(legs: list[RouteLeg]) -> float:
    segment_lengths: dict[tuple[tuple[float, float], tuple[float, float]], float] = {}
    total_length_km = 0.0
    duplicate_length_km = 0.0

    for leg in legs:
        if leg.geometry_wkt is None:
            continue
        for key, length_km in route_segments(leg.geometry_wkt):
            previous_length = segment_lengths.get(key)
            if previous_length is None:
                segment_lengths[key] = length_km
            else:
                duplicate_length_km += min(length_km, previous_length)
            total_length_km += length_km

    if total_length_km == 0:
        return 0.0
    return duplicate_length_km / total_length_km


def hide_itinerary_geometry(itinerary: Itinerary) -> None:
    for leg in itinerary.legs:
        leg.geometry_wkt = None


def max_hiking_category(categories: list[str]) -> str:
    return max(categories, key=lambda category: HIKING_CATEGORY_RANK.get(category, 0))


def combined_difficulty_status(statuses: list[str]) -> str:
    unique = set(statuses)
    if unique == {"mapped"}:
        return "mapped"
    if "mapped" in unique or "partial" in unique:
        return "partial"
    return "unknown"


def duration_match_score(legs: list[RouteLeg], target_duration_h: float) -> float:
    return sum(abs(leg.duration_h - target_duration_h) for leg in legs)


def itinerary_from_legs(legs: list[RouteLeg], target_duration_h: float) -> Itinerary:
    huts = [legs[0].start_hut] + [leg.destination_hut for leg in legs]
    total_duration_h = sum(leg.duration_h for leg in legs)
    return Itinerary(
        huts=huts,
        days=len(legs),
        target_duration_h=round(target_duration_h, 3),
        duration_match_score=round(duration_match_score(legs, target_duration_h), 3),
        average_daily_duration_h=round(total_duration_h / len(legs), 3),
        total_duration_h=round(total_duration_h, 3),
        total_distance_km=round(sum(leg.distance_km for leg in legs), 3),
        total_ascent_m=round(sum(leg.ascent_m for leg in legs), 1),
        total_descent_m=round(sum(leg.descent_m for leg in legs), 1),
        total_elevation_change_m=round(sum(leg.elevation_change_m for leg in legs), 1),
        max_hiking_category=max_hiking_category([leg.max_hiking_category for leg in legs]),
        difficulty_status=combined_difficulty_status([leg.difficulty_status for leg in legs]),
        legs=legs,
    )


@app.get("/")
def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found.")
    return FileResponse(index_path)


@app.get("/api/huts")
def huts(search: str = "", limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, list[str]]:
    query = """
        SELECT start_hut AS hut FROM routes
        UNION
        SELECT destination_hut AS hut FROM routes
    """
    params: list[object] = []
    if search.strip():
        query = f"SELECT hut FROM ({query}) WHERE hut LIKE ?"
        params.append(f"%{search.strip()}%")
    query += " ORDER BY hut LIMIT ?"
    params.append(limit)

    with connect_database() as connection:
        rows = connection.execute(query, params).fetchall()
    return {"huts": [str(row["hut"]) for row in rows]}


@app.get("/api/hut-markers", response_model=list[HutMarker])
def hut_markers() -> list[HutMarker]:
    markers: dict[str, HutMarker] = {}
    coordinate_lookup = hut_coordinate_lookup()
    with connect_database() as connection:
        hut_rows = connection.execute(
            """
            SELECT start_hut AS hut FROM routes
            UNION
            SELECT destination_hut AS hut FROM routes
            ORDER BY hut
            """
        ).fetchall()

    missing_huts = []
    for row in hut_rows:
        hut = str(row["hut"])
        marker = coordinate_lookup.get(hut)
        if marker is None:
            missing_huts.append(hut)
            continue
        markers[hut] = marker

    if missing_huts:
        with connect_database() as connection:
            for hut in missing_huts:
                row = connection.execute(
                    """
                    SELECT start_hut, destination_hut, geometry_wkt
                    FROM routes
                    WHERE start_hut = ?
                       OR destination_hut = ?
                    LIMIT 1
                    """,
                    (hut, hut),
                ).fetchone()
                if row is None:
                    continue
                try:
                    start_coord, destination_coord = parse_linestring_endpoints(
                        str(row["geometry_wkt"])
                    )
                except ValueError:
                    continue
                coord = start_coord if row["start_hut"] == hut else destination_coord
                markers[hut] = HutMarker(hut=hut, longitude=coord[0], latitude=coord[1])

    return sorted(markers.values(), key=lambda marker: marker.hut)


@app.get("/api/route", response_model=RouteLeg)
def route(start_hut: str, destination_hut: str) -> RouteLeg:
    with connect_database() as connection:
        row = connection.execute(
            """
            SELECT
                start_hut,
                destination_hut,
                duration_h,
                distance_km,
                ascent_m,
                descent_m,
                max_hiking_category,
                difficulty_status,
                geometry_wkt
            FROM routes
            WHERE start_hut = ?
              AND destination_hut = ?
            """,
            (start_hut, destination_hut),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Route not found.")
    return route_leg_from_row(row, include_geometry=True)


@app.get("/api/search", response_model=SearchResponse)
def search_routes(
    start_hut: str,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)],
    min_duration_h: Annotated[float, Query(ge=0)] = 2.0,
    max_duration_h: Annotated[float, Query(ge=0)] = 14.0,
    min_elevation_change_m: Annotated[float, Query(ge=0)] = 0.0,
    max_elevation_change_m: Annotated[float, Query(ge=0)] = 5000.0,
    limit: str | None = None,
    include_geometry: bool = False,
) -> SearchResponse:
    result_limit = normalize_result_limit(limit)
    if max_duration_h < min_duration_h:
        raise HTTPException(status_code=400, detail="max_duration_h must be >= min_duration_h.")
    if max_elevation_change_m < min_elevation_change_m:
        raise HTTPException(
            status_code=400,
            detail="max_elevation_change_m must be >= min_elevation_change_m.",
        )

    with connect_database() as connection:
        rows = connection.execute(
            """
            SELECT
                start_hut,
                destination_hut,
                duration_h,
                distance_km,
                ascent_m,
                descent_m,
                max_hiking_category,
                difficulty_status,
                geometry_wkt
            FROM routes
            WHERE duration_h BETWEEN ? AND ?
              AND (ascent_m + descent_m) BETWEEN ? AND ?
            ORDER BY start_hut, duration_h, destination_hut
            """,
            (
                min_duration_h,
                max_duration_h,
                min_elevation_change_m,
                max_elevation_change_m,
            ),
        ).fetchall()

    adjacency: dict[str, list[RouteLeg]] = {}
    for row in rows:
        leg = route_leg_from_row(row, include_geometry=True)
        adjacency.setdefault(leg.start_hut, []).append(leg)

    itineraries: list[Itinerary] = []
    target_duration_h = (min_duration_h + max_duration_h) / 2

    def expand(current_hut: str, visited_huts: set[str], legs: list[RouteLeg]) -> None:
        if len(legs) == days:
            itineraries.append(itinerary_from_legs(legs, target_duration_h))
            return

        for leg in adjacency.get(current_hut, []):
            if leg.destination_hut in visited_huts:
                continue
            expand(
                leg.destination_hut,
                visited_huts | {leg.destination_hut},
                legs + [leg],
            )

    expand(start_hut, {start_hut}, [])
    itineraries.sort(
        key=lambda itinerary: (
            route_overlap_ratio(itinerary.legs),
            itinerary.duration_match_score,
            abs(itinerary.average_daily_duration_h - target_duration_h),
            itinerary.total_distance_km,
        )
    )
    limited_itineraries = itineraries[:result_limit]
    if not include_geometry:
        for itinerary in limited_itineraries:
            hide_itinerary_geometry(itinerary)

    return SearchResponse(
        start_hut=start_hut,
        days=days,
        result_count=len(limited_itineraries),
        itineraries=limited_itineraries,
    )
