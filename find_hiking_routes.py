from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pickle
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import networkx as nx
import pandas as pd
import pyogrio
import requests
from branca.element import MacroElement, Template
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiLineString
from shapely import wkt

from find_nearby_huts import (
    DEFAULT_HUT_NAME,
    DEFAULT_INCLUSION_CSV,
    DEFAULT_INPUT,
    METRIC_CRS,
    WGS84_CRS,
    filter_huts_by_inclusion_column,
    filter_huts_by_inclusion_csv,
    find_input_hut,
    load_huts,
    marker_points,
    nearest_huts,
    write_hut_inclusion_template,
)


SWISSTOPO_WANDERWEGE_URL = (
    "https://data.geo.admin.ch/ch.swisstopo.swisstlm3d-wanderwege/"
    "swisstlm3d-wanderwege/swisstlm3d-wanderwege_2056_5728.gpkg.zip"
)

DATA_DIR = Path("data") / "swisstopo_wanderwege"
DEFAULT_ZIP = DATA_DIR / "swisstlm3d-wanderwege_2056_5728.gpkg.zip"
DEFAULT_GPKG = DATA_DIR / "swisstlm3d-wanderwege_2056_5728.gpkg"
DEFAULT_GRAPH = DATA_DIR / "swisstlm3d_wanderwege_graph.pkl"
DEFAULT_ROUTE_CACHE = DATA_DIR / "hut_route_cache.sqlite"
DEFAULT_ROUTES = Path("hut_hiking_routes.csv")
DEFAULT_ITINERARIES = Path("hut_hiking_itineraries.csv")
DEFAULT_MAP = Path("hiking_routes_map.html")
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
ROUTE_CACHE_SCHEMA_VERSION = "1"

DEFAULT_DAYS = 3
DEFAULT_MAX_MAP_ITINERARIES = 25
DEFAULT_NEIGHBOR_RADIUS_KM = 20.0
DEFAULT_MIN_HOURS = 5.0
DEFAULT_MAX_HOURS = 8.0
DEFAULT_WALKING_SPEED_KMH = 4.0
DEFAULT_ASCENT_M_PER_HOUR = 400.0
DEFAULT_DESCENT_M_PER_HOUR = 800.0
DEFAULT_MIN_NIGHTS_BETWEEN_SAME_HUT = 4
NODE_PRECISION_M = 0.01
ROUTE_COLUMNS = [
    "source_index",
    "source_hut",
    "destination_index",
    "destination_hut",
    "duration_h",
    "distance_km",
    "ascent_m",
    "descent_m",
    "source_snap_m",
    "destination_snap_m",
    "geometry_wkt",
]
ITINERARY_COLUMNS = [
    "itinerary_id",
    "days",
    "hut_indices",
    "hut_names",
    "total_duration_h",
    "total_distance_km",
    "total_ascent_m",
    "total_descent_m",
    "leg_durations_h",
    "leg_distances_km",
    "leg_geometry_wkts",
]


def is_valid_zip(path: Path) -> bool:
    if not path.exists():
        return False
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None
    except zipfile.BadZipFile:
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Route huts over official swisstopo swissTLM3D Wanderwege using NetworkX."
        )
    )
    parser.add_argument("--hut", default=DEFAULT_HUT_NAME)
    parser.add_argument("--huts", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--inclusion-csv",
        type=Path,
        help=(
            "Optional hut review CSV with include_in_evaluation. "
            "Rows set to 1/true/yes are included in routing."
        ),
    )
    parser.add_argument(
        "--write-inclusion-template",
        type=Path,
        nargs="?",
        const=DEFAULT_INCLUSION_CSV,
        metavar="CSV",
        help=(
            "Write a hut review CSV with names, coordinates, metadata, and "
            "include_in_evaluation initialized to 1, then exit. "
            f"Defaults to {DEFAULT_INCLUSION_CSV}."
        ),
    )
    parser.add_argument("--wanderwege-url", default=SWISSTOPO_WANDERWEGE_URL)
    parser.add_argument("--zip-path", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--gpkg-path", type=Path, default=DEFAULT_GPKG)
    parser.add_argument("--graph-path", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--route-cache", type=Path, default=DEFAULT_ROUTE_CACHE)
    parser.add_argument(
        "--init-route-cache",
        action="store_true",
        help=(
            "Create the SQLite route cache schema and register the current routing "
            "profile, then exit without calculating routes."
        ),
    )
    parser.add_argument("--routes-output", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--itineraries-output", type=Path, default=DEFAULT_ITINERARIES)
    parser.add_argument("--map-output", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--neighbor-radius-km", type=float, default=DEFAULT_NEIGHBOR_RADIUS_KM)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--max-map-itineraries", type=int, default=DEFAULT_MAX_MAP_ITINERARIES)
    parser.add_argument("--min-hours", type=float, default=DEFAULT_MIN_HOURS)
    parser.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS)
    parser.add_argument("--walking-speed-kmh", type=float, default=DEFAULT_WALKING_SPEED_KMH)
    parser.add_argument("--ascent-m-per-hour", type=float, default=DEFAULT_ASCENT_M_PER_HOUR)
    parser.add_argument("--descent-m-per-hour", type=float, default=DEFAULT_DESCENT_M_PER_HOUR)
    parser.add_argument(
        "--min-nights-between-same-hut",
        type=int,
        default=DEFAULT_MIN_NIGHTS_BETWEEN_SAME_HUT,
        help=(
            "Minimum overnight stays required between repeat visits to the same hut. "
            f"Defaults to {DEFAULT_MIN_NIGHTS_BETWEEN_SAME_HUT}. "
            "The final return to the starting hut is always allowed for itineraries "
            "with at least 4 overnight stays."
        ),
    )
    parser.add_argument(
        "--all-huts",
        action="store_true",
        help="Build the route table for every hut instead of only --hut.",
    )
    parser.add_argument(
        "--rebuild-graph",
        action="store_true",
        help="Rebuild the cached NetworkX graph from the swisstopo GeoPackage.",
    )
    parser.add_argument(
        "--skip-map",
        action="store_true",
        help="Only write the route CSV table.",
    )
    return parser.parse_args()


def download_file(url: str, path: Path) -> None:
    if path.exists():
        if is_valid_zip(path):
            print(f"Using existing swisstopo ZIP: {path}")
            return
        print(f"Existing swisstopo ZIP is incomplete or invalid: {path}")
        print("Removing invalid ZIP and downloading it again.")
        path.unlink()

    print(f"Downloading swisstopo Wanderwege ZIP from: {url}")
    print(f"Saving to: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = path.with_suffix(path.suffix + ".part")
    if partial_path.exists():
        print(f"Removing stale partial download: {partial_path}")
        partial_path.unlink()

    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        total_bytes = int(response.headers.get("content-length", 0))
        downloaded_bytes = 0
        with partial_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    file.write(chunk)
                    downloaded_bytes += len(chunk)
                    if total_bytes:
                        percent = downloaded_bytes / total_bytes * 100
                        print(f"Downloaded {downloaded_bytes / 1_000_000:.1f} MB ({percent:.1f}%)")
                    else:
                        print(f"Downloaded {downloaded_bytes / 1_000_000:.1f} MB")

    if total_bytes and downloaded_bytes != total_bytes:
        partial_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded {downloaded_bytes} bytes, expected {total_bytes} bytes. "
            "Please rerun the command to try again."
        )
    if not is_valid_zip(partial_path):
        partial_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file is not a valid ZIP. Please rerun the command.")

    partial_path.replace(path)
    print("Download complete.")


def extract_gpkg(zip_path: Path, gpkg_path: Path) -> None:
    if gpkg_path.exists():
        print(f"Using existing swisstopo GeoPackage: {gpkg_path}")
        return

    print(f"Extracting GeoPackage from: {zip_path}")
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            gpkg_names = [name for name in archive.namelist() if name.lower().endswith(".gpkg")]
            if not gpkg_names:
                raise FileNotFoundError(f"No GeoPackage found in {zip_path}.")
            print(f"Found GeoPackage in ZIP: {gpkg_names[0]}")
            with archive.open(gpkg_names[0]) as source, gpkg_path.open("wb") as target:
                target.write(source.read())
    except zipfile.BadZipFile as error:
        raise zipfile.BadZipFile(
            f"{zip_path} is not a complete valid ZIP. Delete it or rerun the script; "
            "the downloader now replaces invalid cached ZIPs automatically."
        ) from error
    print(f"Extracted GeoPackage to: {gpkg_path}")


def iter_lines(geometry: Any) -> list[LineString]:
    if geometry is None:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    return []


def coordinate_key(coord: tuple[float, ...]) -> tuple[int, int]:
    return (
        round(float(coord[0]) / NODE_PRECISION_M),
        round(float(coord[1]) / NODE_PRECISION_M),
    )


def coord_z(coord: tuple[float, ...]) -> float | None:
    if len(coord) >= 3 and not math.isnan(float(coord[2])):
        return float(coord[2])
    return None


def edge_seconds(
    from_coord: tuple[float, ...],
    to_coord: tuple[float, ...],
    length_m: float,
    walking_speed_kmh: float,
    ascent_m_per_hour: float,
    descent_m_per_hour: float,
) -> float:
    seconds = (length_m / 1000) / walking_speed_kmh * 3600
    from_z = coord_z(from_coord)
    to_z = coord_z(to_coord)
    if from_z is None or to_z is None:
        return seconds

    delta = to_z - from_z
    if delta > 0:
        seconds += delta / ascent_m_per_hour * 3600
    elif delta < 0:
        seconds += abs(delta) / descent_m_per_hour * 3600
    return seconds


def build_graph_from_gpkg(
    gpkg_path: Path,
    walking_speed_kmh: float,
    ascent_m_per_hour: float,
    descent_m_per_hour: float,
) -> nx.DiGraph:
    print(f"Building NetworkX graph from: {gpkg_path}")
    graph = nx.DiGraph()
    layers = pyogrio.list_layers(gpkg_path)
    print(f"Found {len(layers)} GeoPackage layers.")

    for layer_name, geometry_type in layers:
        if "LineString" not in str(geometry_type):
            print(f"Skipping non-line layer: {layer_name} ({geometry_type})")
            continue

        print(f"Reading trail layer: {layer_name} ({geometry_type})")
        trails = gpd.read_file(gpkg_path, layer=layer_name)
        print(f"Loaded {len(trails)} trail features from layer: {layer_name}")
        if trails.crs is None:
            trails = trails.set_crs(METRIC_CRS)
        elif str(trails.crs).upper() != METRIC_CRS:
            print(f"Reprojecting layer {layer_name} to {METRIC_CRS}")
            trails = trails.to_crs(METRIC_CRS)

        feature_count = 0
        for geometry in trails.geometry:
            feature_count += 1
            if feature_count % 10_000 == 0:
                print(
                    f"Processed {feature_count}/{len(trails)} features in {layer_name}; "
                    f"graph has {graph.number_of_nodes()} nodes and {graph.number_of_edges()} edges."
                )
            for line in iter_lines(geometry):
                coords = list(line.coords)
                for from_coord, to_coord in zip(coords[:-1], coords[1:]):
                    from_key = coordinate_key(from_coord)
                    to_key = coordinate_key(to_coord)
                    if from_key == to_key:
                        continue

                    dx = float(to_coord[0]) - float(from_coord[0])
                    dy = float(to_coord[1]) - float(from_coord[1])
                    length_m = math.hypot(dx, dy)
                    if length_m <= 0:
                        continue

                    graph.add_node(
                        from_key,
                        x=float(from_coord[0]),
                        y=float(from_coord[1]),
                        z=coord_z(from_coord),
                    )
                    graph.add_node(
                        to_key,
                        x=float(to_coord[0]),
                        y=float(to_coord[1]),
                        z=coord_z(to_coord),
                    )
                    graph.add_edge(
                        from_key,
                        to_key,
                        length_m=length_m,
                        seconds=edge_seconds(
                            from_coord,
                            to_coord,
                            length_m,
                            walking_speed_kmh,
                            ascent_m_per_hour,
                            descent_m_per_hour,
                        ),
                    )
                    graph.add_edge(
                        to_key,
                        from_key,
                        length_m=length_m,
                        seconds=edge_seconds(
                            to_coord,
                            from_coord,
                            length_m,
                            walking_speed_kmh,
                            ascent_m_per_hour,
                            descent_m_per_hour,
                        ),
                    )
        print(
            f"Finished layer {layer_name}; graph has "
            f"{graph.number_of_nodes()} nodes and {graph.number_of_edges()} edges."
        )

    if graph.number_of_edges() == 0:
        raise ValueError(f"No LineString trail layers found in {gpkg_path}.")
    print(
        f"Finished graph build: {graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} directed edges."
    )
    return graph


def load_or_build_graph(args: argparse.Namespace) -> nx.DiGraph:
    if args.graph_path.exists() and not args.rebuild_graph:
        print(f"Loading cached graph: {args.graph_path}")
        with args.graph_path.open("rb") as file:
            graph = pickle.load(file)
        print(
            f"Loaded cached graph with {graph.number_of_nodes()} nodes and "
            f"{graph.number_of_edges()} directed edges."
        )
        return graph

    print("Cached graph missing or --rebuild-graph was passed.")
    download_file(args.wanderwege_url, args.zip_path)
    extract_gpkg(args.zip_path, args.gpkg_path)
    graph = build_graph_from_gpkg(
        args.gpkg_path,
        args.walking_speed_kmh,
        args.ascent_m_per_hour,
        args.descent_m_per_hour,
    )
    print(f"Saving graph cache to: {args.graph_path}")
    args.graph_path.parent.mkdir(parents=True, exist_ok=True)
    with args.graph_path.open("wb") as file:
        pickle.dump(graph, file, protocol=pickle.HIGHEST_PROTOCOL)
    print("Graph cache saved.")
    return graph


def hut_points_metric(huts: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return huts.to_crs(METRIC_CRS).geometry.representative_point()


def nearest_graph_nodes(graph: nx.DiGraph, points: gpd.GeoSeries) -> dict[int, tuple[int, float]]:
    print(f"Building nearest-node index for {graph.number_of_nodes()} graph nodes.")
    node_ids = list(graph.nodes)
    node_xy = [(graph.nodes[node]["x"], graph.nodes[node]["y"]) for node in node_ids]
    tree = cKDTree(node_xy)

    print(f"Snapping {len(points)} huts to nearest trail graph nodes.")
    result: dict[int, tuple[int, float]] = {}
    for count, (index, point) in enumerate(points.items(), start=1):
        if count % 250 == 0:
            print(f"Snapped {count}/{len(points)} huts.")
        distance_m, node_pos = tree.query((point.x, point.y))
        result[int(index)] = (node_ids[int(node_pos)], float(distance_m))
    print("Finished snapping huts to graph.")
    return result


def path_stats(graph: nx.DiGraph, path: list[Any]) -> tuple[float, float, float, float]:
    length_m = 0.0
    seconds = 0.0
    ascent_m = 0.0
    descent_m = 0.0

    for from_node, to_node in zip(path[:-1], path[1:]):
        edge = graph.edges[from_node, to_node]
        length_m += float(edge["length_m"])
        seconds += float(edge["seconds"])

        from_z = graph.nodes[from_node].get("z")
        to_z = graph.nodes[to_node].get("z")
        if from_z is None or to_z is None:
            continue
        delta = float(to_z) - float(from_z)
        if delta > 0:
            ascent_m += delta
        else:
            descent_m += abs(delta)

    return length_m / 1000, seconds / 3600, ascent_m, descent_m


def path_wgs84(graph: nx.DiGraph, path: list[Any]) -> LineString:
    transformer = Transformer.from_crs(METRIC_CRS, WGS84_CRS, always_xy=True)
    coordinates = []
    for node in path:
        x = graph.nodes[node]["x"]
        y = graph.nodes[node]["y"]
        lon, lat = transformer.transform(x, y)
        coordinates.append((lon, lat))
    return LineString(coordinates)


def build_routes_for_source(
    graph: nx.DiGraph,
    huts: gpd.GeoDataFrame,
    source_index: int,
    max_hours: float,
    min_hours: float,
    neighbor_radius_km: float,
    snapped_nodes: dict[int, tuple[Any, float]],
) -> list[dict[str, Any]]:
    source_hut = huts.loc[source_index]
    nearby = nearest_huts(huts, source_hut, neighbor_radius_km)
    source_node, source_snap_m = snapped_nodes[source_index]

    print(
        f"Running Dijkstra from {source_hut.get('name')} to "
        f"{len(nearby) - 1} nearby huts within {neighbor_radius_km:g} km."
    )
    durations, paths = nx.single_source_dijkstra(
        graph,
        source_node,
        cutoff=max_hours * 3600,
        weight="seconds",
    )
    print(f"Dijkstra reached {len(durations)} graph nodes within {max_hours:g} hours.")

    rows: list[dict[str, Any]] = []
    for count, (destination_index, destination_hut) in enumerate(nearby.iterrows(), start=1):
        if count % 25 == 0:
            print(f"Checked {count}/{len(nearby)} nearby huts for {source_hut.get('name')}.")
        if destination_index == source_index:
            continue

        destination_node, destination_snap_m = snapped_nodes[int(destination_index)]
        if destination_node not in paths:
            continue

        path = paths[destination_node]
        distance_km, duration_h, ascent_m, descent_m = path_stats(graph, path)
        if not min_hours <= duration_h <= max_hours:
            continue

        route_geometry = path_wgs84(graph, path)
        rows.append(
            {
                "source_index": source_index,
                "source_hut": source_hut.get("name"),
                "destination_index": int(destination_index),
                "destination_hut": destination_hut.get("name"),
                "duration_h": round(duration_h, 3),
                "distance_km": round(distance_km, 3),
                "ascent_m": round(ascent_m, 1),
                "descent_m": round(descent_m, 1),
                "source_snap_m": round(source_snap_m, 1),
                "destination_snap_m": round(destination_snap_m, 1),
                "geometry_wkt": route_geometry.wkt,
                }
            )
    print(f"Kept {len(rows)} routes for {source_hut.get('name')}.")
    return rows


def build_route_table(
    graph: nx.DiGraph,
    huts: gpd.GeoDataFrame,
    source_indices: list[int],
    min_hours: float,
    max_hours: float,
    neighbor_radius_km: float,
) -> pd.DataFrame:
    print("Preparing hut snap points.")
    snapped_nodes = nearest_graph_nodes(graph, hut_points_metric(huts))
    rows: list[dict[str, Any]] = []

    for count, source_index in enumerate(source_indices, start=1):
        print(f"Routing {count}/{len(source_indices)}: {huts.loc[source_index].get('name')}")
        rows.extend(
            build_routes_for_source(
                graph,
                huts,
                source_index,
                max_hours,
                min_hours,
                neighbor_radius_km,
                snapped_nodes,
            )
        )

    if not rows:
        print("No routes matched the requested duration window.")
        return pd.DataFrame(columns=ROUTE_COLUMNS)

    print(f"Built route table with {len(rows)} matching routes.")
    return pd.DataFrame(rows, columns=ROUTE_COLUMNS).sort_values(
        ["source_hut", "duration_h", "destination_hut"],
        ignore_index=True,
    )


def initialize_route_cache(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS route_cache_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS route_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                huts_path TEXT NOT NULL,
                graph_path TEXT NOT NULL,
                included_hut_count INTEGER NOT NULL,
                source_hut_count INTEGER NOT NULL,
                neighbor_radius_km REAL NOT NULL,
                min_hours REAL NOT NULL,
                max_hours REAL NOT NULL,
                walking_speed_kmh REAL NOT NULL,
                ascent_m_per_hour REAL NOT NULL,
                descent_m_per_hour REAL NOT NULL,
                status TEXT NOT NULL,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS routes (
                run_id INTEGER NOT NULL,
                source_index INTEGER NOT NULL,
                source_hut TEXT,
                destination_index INTEGER NOT NULL,
                destination_hut TEXT,
                duration_h REAL NOT NULL,
                distance_km REAL NOT NULL,
                ascent_m REAL NOT NULL,
                descent_m REAL NOT NULL,
                source_snap_m REAL NOT NULL,
                destination_snap_m REAL NOT NULL,
                geometry_wkt TEXT NOT NULL,
                PRIMARY KEY (run_id, source_index, destination_index),
                FOREIGN KEY (run_id) REFERENCES route_runs(run_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_routes_source
                ON routes(run_id, source_index);
            CREATE INDEX IF NOT EXISTS idx_routes_destination
                ON routes(run_id, destination_index);
            """
        )
        connection.execute(
            """
            INSERT INTO route_cache_metadata(key, value)
            VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (ROUTE_CACHE_SCHEMA_VERSION,),
        )


def create_route_cache_run(args: argparse.Namespace, huts: gpd.GeoDataFrame) -> int:
    initialize_route_cache(args.route_cache)
    source_count = len(huts)
    with sqlite3.connect(args.route_cache) as connection:
        cursor = connection.execute(
            """
            INSERT INTO route_runs (
                created_at,
                huts_path,
                graph_path,
                included_hut_count,
                source_hut_count,
                neighbor_radius_km,
                min_hours,
                max_hours,
                walking_speed_kmh,
                ascent_m_per_hour,
                descent_m_per_hour,
                status,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dt.datetime.now(dt.UTC).isoformat(),
                str(args.huts),
                str(args.graph_path),
                len(huts),
                source_count,
                args.neighbor_radius_km,
                args.min_hours,
                args.max_hours,
                args.walking_speed_kmh,
                args.ascent_m_per_hour,
                args.descent_m_per_hour,
                "prepared",
                "Routes have not been calculated for this run yet.",
            ),
        )
        return int(cursor.lastrowid)


def write_routes_to_route_cache(
    path: Path,
    run_id: int,
    routes: pd.DataFrame,
) -> None:
    if routes.empty:
        return

    rows = []
    for row in routes[ROUTE_COLUMNS].itertuples(index=False):
        rows.append((run_id, *row))

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executemany(
            """
            INSERT OR REPLACE INTO routes (
                run_id,
                source_index,
                source_hut,
                destination_index,
                destination_hut,
                duration_h,
                distance_km,
                ascent_m,
                descent_m,
                source_snap_m,
                destination_snap_m,
                geometry_wkt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.execute(
            "UPDATE route_runs SET status = ? WHERE run_id = ?",
            ("routes_written", run_id),
        )


def read_cached_routes(
    path: Path,
    run_id: int,
    source_index: int | None = None,
) -> pd.DataFrame:
    query = "SELECT {} FROM routes WHERE run_id = ?".format(", ".join(ROUTE_COLUMNS))
    params: list[Any] = [run_id]
    if source_index is not None:
        query += " AND source_index = ?"
        params.append(source_index)
    query += " ORDER BY source_hut, duration_h, destination_hut"

    with sqlite3.connect(path) as connection:
        return pd.read_sql_query(query, connection, params=params)


def can_visit_hut(
    destination_index: int,
    visited_indices: list[int],
    day: int,
    total_days: int,
    min_nights_between_same_hut: int,
    start_index: int,
) -> bool:
    if destination_index not in visited_indices:
        return True

    is_final_return_to_start = (
        destination_index == start_index
        and day == total_days
        and total_days >= 4
    )
    if is_final_return_to_start:
        return True

    last_visit_position = max(
        position
        for position, visited_index in enumerate(visited_indices)
        if visited_index == destination_index
    )
    nights_between_stays = len(visited_indices) - last_visit_position - 1
    return nights_between_stays >= min_nights_between_same_hut


def build_multiday_itineraries(
    graph: nx.DiGraph,
    huts: gpd.GeoDataFrame,
    start_index: int,
    days: int,
    min_hours: float,
    max_hours: float,
    neighbor_radius_km: float,
    min_nights_between_same_hut: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if days < 1:
        raise ValueError("--days must be at least 1.")
    if min_nights_between_same_hut < 0:
        raise ValueError("--min-nights-between-same-hut must be at least 0.")

    print("Preparing hut snap points.")
    snapped_nodes = nearest_graph_nodes(graph, hut_points_metric(huts))
    leg_cache: dict[int, list[dict[str, Any]]] = {}

    def legs_from(source_index: int) -> list[dict[str, Any]]:
        if source_index not in leg_cache:
            print(f"Expanding day-leg options from: {huts.loc[source_index].get('name')}")
            leg_cache[source_index] = build_routes_for_source(
                graph,
                huts,
                source_index,
                max_hours,
                min_hours,
                neighbor_radius_km,
                snapped_nodes,
            )
        else:
            print(f"Reusing cached day-leg options from: {huts.loc[source_index].get('name')}")
        return leg_cache[source_index]

    partials: list[tuple[int, list[int], list[dict[str, Any]]]] = [
        (start_index, [start_index], [])
    ]

    for day in range(1, days + 1):
        print(f"Building itinerary day {day}/{days} from {len(partials)} partial itineraries.")
        next_partials: list[tuple[int, list[int], list[dict[str, Any]]]] = []
        for current_index, visited_indices, legs in partials:
            for leg in legs_from(current_index):
                destination_index = int(leg["destination_index"])
                if not can_visit_hut(
                    destination_index,
                    visited_indices,
                    day,
                    days,
                    min_nights_between_same_hut,
                    start_index,
                ):
                    continue

                next_partials.append(
                    (
                        destination_index,
                        visited_indices + [destination_index],
                        legs + [leg],
                    )
                )
        partials = next_partials
        print(f"Kept {len(partials)} partial itineraries after day {day}.")
        if not partials:
            break

    leg_rows = [
        leg
        for source_legs in leg_cache.values()
        for leg in source_legs
    ]
    legs_df = pd.DataFrame(leg_rows, columns=ROUTE_COLUMNS).drop_duplicates(
        ["source_index", "destination_index", "duration_h", "distance_km"],
        ignore_index=True,
    )

    itinerary_rows: list[dict[str, Any]] = []
    for itinerary_id, (_, hut_indices, legs) in enumerate(partials, start=1):
        hut_names = [str(huts.loc[index].get("name")) for index in hut_indices]
        itinerary_rows.append(
            {
                "itinerary_id": itinerary_id,
                "days": len(legs),
                "hut_indices": " -> ".join(str(index) for index in hut_indices),
                "hut_names": " -> ".join(hut_names),
                "total_duration_h": round(sum(float(leg["duration_h"]) for leg in legs), 3),
                "total_distance_km": round(sum(float(leg["distance_km"]) for leg in legs), 3),
                "total_ascent_m": round(sum(float(leg["ascent_m"]) for leg in legs), 1),
                "total_descent_m": round(sum(float(leg["descent_m"]) for leg in legs), 1),
                "leg_durations_h": " | ".join(f"{float(leg['duration_h']):.3f}" for leg in legs),
                "leg_distances_km": " | ".join(f"{float(leg['distance_km']):.3f}" for leg in legs),
                "leg_geometry_wkts": " || ".join(str(leg["geometry_wkt"]) for leg in legs),
            }
        )

    if not itinerary_rows:
        print("No complete multi-day itineraries matched the requested constraints.")
        return legs_df, pd.DataFrame(columns=ITINERARY_COLUMNS)

    itineraries_df = pd.DataFrame(itinerary_rows, columns=ITINERARY_COLUMNS).sort_values(
        ["total_duration_h", "total_distance_km"],
        ignore_index=True,
    )
    print(f"Built {len(itineraries_df)} complete {days}-day itineraries.")
    return legs_df, itineraries_df


def create_map(routes: pd.DataFrame, huts: gpd.GeoDataFrame, source_index: int, output: Path) -> None:
    print(f"Creating Folium map with {len(routes)} routes: {output}")
    source = huts.loc[source_index]
    source_point = marker_points(huts.loc[[source_index]]).iloc[0]
    route_map = folium.Map(location=[source_point.y, source_point.x], zoom_start=10)

    folium.Marker(
        [source_point.y, source_point.x],
        tooltip=str(source.get("name")),
        popup=folium.Popup(f"<b>{source.get('name')}</b>", max_width=260),
        icon=folium.Icon(color="red", icon="home"),
    ).add_to(route_map)

    bounds = [[source_point.y, source_point.x]]
    destination_indices = routes["destination_index"].astype(int).tolist()
    destination_points = marker_points(huts.loc[destination_indices])
    colors = ["#1f77b4", "#2ca02c", "#d62728", "#9467bd", "#ff7f0e", "#17becf"]

    for count, row in routes.iterrows():
        line = wkt.loads(row["geometry_wkt"])
        lat_lon = [(lat, lon) for lon, lat in line.coords]
        bounds.extend(lat_lon)
        color = colors[count % len(colors)]
        popup = (
            f"<b>{row['destination_hut']}</b><br>"
            f"Duration: {row['duration_h']:.1f} h<br>"
            f"Distance: {row['distance_km']:.1f} km<br>"
            f"Ascent: {row['ascent_m']:.0f} m<br>"
            f"Descent: {row['descent_m']:.0f} m"
        )

        folium.PolyLine(
            lat_lon,
            color=color,
            weight=4,
            opacity=0.85,
            tooltip=f"{row['destination_hut']}: {row['duration_h']:.1f} h",
            popup=folium.Popup(popup, max_width=320),
        ).add_to(route_map)

        point = destination_points.loc[int(row["destination_index"])]
        folium.Marker(
            [point.y, point.x],
            tooltip=str(row["destination_hut"]),
            popup=folium.Popup(popup, max_width=320),
            icon=folium.Icon(color="blue", icon="flag"),
        ).add_to(route_map)

    if len(bounds) > 1:
        route_map.fit_bounds(bounds, padding=(30, 30))
    output.parent.mkdir(parents=True, exist_ok=True)
    route_map.save(output)
    print("Map saved.")


def add_itinerary_dropdown(
    route_map: folium.Map,
    layer_options: list[dict[str, str]],
) -> None:
    if not layer_options:
        return

    dropdown = MacroElement()
    dropdown.layer_options = json.dumps(layer_options)
    dropdown._template = Template(
        """
        {% macro html(this, kwargs) %}
        <div id="itinerary-selector"
             style="
                position: fixed;
                top: 12px;
                right: 12px;
                z-index: 9999;
                background: white;
                padding: 10px 12px;
                border: 1px solid #999;
                border-radius: 4px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.25);
                font-family: Arial, sans-serif;
                font-size: 13px;
                max-width: 360px;
             ">
            <label for="itinerary-select" style="display:block; font-weight:700; margin-bottom:6px;">
                Itinerary
            </label>
            <select id="itinerary-select" style="width:100%; font-size:13px; padding:4px;">
            </select>
            <div id="itinerary-summary" style="margin-top:8px; line-height:1.35;"></div>
        </div>
        {% endmacro %}

        {% macro script(this, kwargs) %}
        (function() {
            var map = {{ this._parent.get_name() }};
            var options = {{ this.layer_options | safe }};
            var select = document.getElementById("itinerary-select");
            var summary = document.getElementById("itinerary-summary");
            var layers = {};

            options.forEach(function(option) {
                layers[option.id] = window[option.layerName];
                var item = document.createElement("option");
                item.value = option.id;
                item.text = option.label;
                select.appendChild(item);
            });

            function setVisible(selectedId) {
                options.forEach(function(option) {
                    var layer = layers[option.id];
                    if (!layer) {
                        return;
                    }
                    if (option.id === selectedId) {
                        if (!map.hasLayer(layer)) {
                            map.addLayer(layer);
                        }
                        summary.innerHTML = option.summary;
                    } else if (map.hasLayer(layer)) {
                        map.removeLayer(layer);
                    }
                });
            }

            select.addEventListener("change", function(event) {
                setVisible(event.target.value);
            });

            if (options.length > 0) {
                select.value = options[0].id;
                setVisible(options[0].id);
            }
        })();
        {% endmacro %}
        """
    )
    route_map.add_child(dropdown)


def create_itinerary_map(
    itineraries: pd.DataFrame,
    huts: gpd.GeoDataFrame,
    source_index: int,
    output: Path,
    max_itineraries: int,
) -> None:
    output = Path(output)
    print(
        f"Creating Folium map with up to {max_itineraries} itineraries "
        f"from {len(itineraries)} matches: {output}"
    )
    source = huts.loc[source_index]
    source_point = marker_points(huts.loc[[source_index]]).iloc[0]
    route_map = folium.Map(location=[source_point.y, source_point.x], zoom_start=10)

    day_colors = ["#1f77b4", "#2ca02c", "#d62728", "#9467bd", "#ff7f0e", "#17becf"]
    bounds = [[source_point.y, source_point.x]]
    mapped = itineraries.head(max_itineraries)
    layer_options: list[dict[str, str]] = []

    for _, itinerary in mapped.iterrows():
        hut_indices = [int(value.strip()) for value in itinerary["hut_indices"].split("->")]
        hut_names = [str(huts.loc[index].get("name")) for index in hut_indices]
        leg_geometries = str(itinerary["leg_geometry_wkts"]).split(" || ")
        leg_durations = str(itinerary["leg_durations_h"]).split(" | ")
        leg_distances = str(itinerary["leg_distances_km"]).split(" | ")

        itinerary_id = int(itinerary["itinerary_id"])
        label = (
            f"{itinerary_id}: {hut_names[0]} -> {hut_names[-1]} "
            f"({float(itinerary['total_duration_h']):.1f} h)"
        )
        summary = (
            f"<b>Itinerary {int(itinerary['itinerary_id'])}</b><br>"
            f"{' -> '.join(hut_names)}<br>"
            f"Total duration: {float(itinerary['total_duration_h']):.1f} h<br>"
            f"Total distance: {float(itinerary['total_distance_km']):.1f} km"
        )
        group = folium.FeatureGroup(name=label, show=True)

        for day_index, geometry_wkt in enumerate(leg_geometries, start=1):
            line = wkt.loads(geometry_wkt)
            lat_lon = [(lat, lon) for lon, lat in line.coords]
            bounds.extend(lat_lon)
            color = day_colors[(day_index - 1) % len(day_colors)]
            duration = leg_durations[day_index - 1] if day_index <= len(leg_durations) else "?"
            distance = leg_distances[day_index - 1] if day_index <= len(leg_distances) else "?"
            leg_popup = (
                f"<b>Itinerary {itinerary_id}, day {day_index}</b><br>"
                f"{hut_names[day_index - 1]} -> {hut_names[day_index]}<br>"
                f"Duration: {float(duration):.1f} h<br>"
                f"Distance: {float(distance):.1f} km<br><br>"
                f"{summary}"
            )
            folium.PolyLine(
                lat_lon,
                color=color,
                weight=5,
                opacity=0.9,
                tooltip=f"Itinerary {int(itinerary['itinerary_id'])}, day {day_index}: {duration} h",
                popup=folium.Popup(leg_popup, max_width=440),
            ).add_to(group)

        points = marker_points(huts.loc[hut_indices])
        for stop_number, index in enumerate(hut_indices):
            point = points.iloc[stop_number]
            is_source = int(index) == source_index
            marker_popup = (
                f"<b>{huts.loc[int(index)].get('name')}</b><br>"
                f"Stop {stop_number} of {len(hut_indices) - 1}<br><br>"
                f"{summary}"
            )
            folium.Marker(
                [point.y, point.x],
                tooltip=str(huts.loc[int(index)].get("name")),
                popup=folium.Popup(marker_popup, max_width=440),
                icon=folium.Icon(color="red" if is_source else "blue", icon="home" if is_source else "flag"),
            ).add_to(group)
            bounds.append([point.y, point.x])

        group.add_to(route_map)
        layer_options.append(
            {
                "id": str(itinerary_id),
                "layerName": group.get_name(),
                "label": label,
                "summary": summary,
            }
        )

    if mapped.empty:
        folium.Marker(
            [source_point.y, source_point.x],
            tooltip=str(source.get("name")),
            popup=folium.Popup(f"<b>{source.get('name')}</b>", max_width=260),
            icon=folium.Icon(color="red", icon="home"),
        ).add_to(route_map)

    add_itinerary_dropdown(route_map, layer_options)

    if len(bounds) > 1:
        route_map.fit_bounds(bounds, padding=(30, 30))
    output.parent.mkdir(parents=True, exist_ok=True)
    route_map.save(output)
    print("Map saved.")


def main() -> None:
    args = parse_args()
    print("Starting hiking route generation.")
    print(f"Loading huts from: {args.huts}")
    huts = load_huts(args.huts)
    print(f"Loaded {len(huts)} huts.")
    if args.write_inclusion_template:
        write_hut_inclusion_template(huts, args.write_inclusion_template)
        print(f"Hut inclusion template written to: {args.write_inclusion_template}")
        return

    before_count = len(huts)
    if args.inclusion_csv:
        huts = filter_huts_by_inclusion_csv(huts, args.inclusion_csv)
        print(f"Included {len(huts)} of {before_count} huts from: {args.inclusion_csv}")
    else:
        huts = filter_huts_by_inclusion_column(huts)
        print(f"Included {len(huts)} of {before_count} huts from: {args.huts}")

    if args.init_route_cache:
        run_id = create_route_cache_run(args, huts)
        print(f"Initialized route cache: {args.route_cache}")
        print(f"Prepared route run id: {run_id}")
        print("No routes were calculated.")
        return

    graph = load_or_build_graph(args)

    if args.all_huts:
        source_indices = huts.index.astype(int).tolist()
        map_source_index = find_input_hut(huts, args.hut).name
        print(f"Building single-day route table for all {len(source_indices)} huts.")
        routes = build_route_table(
            graph,
            huts,
            source_indices,
            min_hours=args.min_hours,
            max_hours=args.max_hours,
            neighbor_radius_km=args.neighbor_radius_km,
        )

        args.routes_output.parent.mkdir(parents=True, exist_ok=True)
        print(f"Writing route table to: {args.routes_output}")
        routes.to_csv(args.routes_output, index=False)
        print("Route table saved.")

        if not args.skip_map:
            map_routes = routes[routes["source_index"] == map_source_index]
            create_map(map_routes, huts, map_source_index, args.map_output)

        print(f"Routes written to: {args.routes_output}")
        if not args.skip_map:
            print(f"Map written to: {args.map_output}")
        print(f"Routes kept: {len(routes)}")
        return

    source_hut = find_input_hut(huts, args.hut)
    source_index = int(source_hut.name)
    print(
        f"Building {args.days}-day itineraries for input hut: {source_hut.get('name')} "
        f"with daily neighbor radius {args.neighbor_radius_km:g} km."
    )
    print(
        "Repeat hut rule: "
        f"at least {args.min_nights_between_same_hut} overnight stays between repeats; "
        "final return to start allowed for itineraries with at least 4 overnight stays."
    )

    routes, itineraries = build_multiday_itineraries(
        graph,
        huts,
        source_index,
        days=args.days,
        min_hours=args.min_hours,
        max_hours=args.max_hours,
        neighbor_radius_km=args.neighbor_radius_km,
        min_nights_between_same_hut=args.min_nights_between_same_hut,
    )

    args.routes_output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Writing discovered daily route legs to: {args.routes_output}")
    routes.to_csv(args.routes_output, index=False)
    print("Route table saved.")

    args.itineraries_output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Writing complete itineraries to: {args.itineraries_output}")
    itineraries.to_csv(args.itineraries_output, index=False)
    print("Itinerary table saved.")

    if not args.skip_map:
        create_itinerary_map(
            itineraries,
            huts,
            source_index,
            args.map_output,
            max_itineraries=args.max_map_itineraries,
        )

    print(f"Routes written to: {args.routes_output}")
    print(f"Itineraries written to: {args.itineraries_output}")
    if not args.skip_map:
        print(f"Map written to: {args.map_output}")
    print(f"Daily route legs discovered: {len(routes)}")
    print(f"Complete itineraries kept: {len(itineraries)}")


if __name__ == "__main__":
    main()
