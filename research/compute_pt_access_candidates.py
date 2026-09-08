import csv
import argparse
import math
import pickle
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import Point

from find_hiking_routes import (
    DEFAULT_ASCENT_M_PER_HOUR,
    DEFAULT_DESCENT_M_PER_HOUR,
    DEFAULT_GRAPH,
    DEFAULT_WALKING_SPEED_KMH,
    METRIC_CRS,
    WGS84_CRS,
    hiking_category_label,
    path_stats,
)


ROOT = Path(__file__).resolve().parents[1]
HUTS_CSV = ROOT / "hut_inclusion.csv"
STOPS_TXT = ROOT / "research" / "gtfs_stops_20260905.txt"
OUTPUT = ROOT / "research" / "hut_pt_access_candidates.csv"

MAX_HOURS = 14.0
MAX_SNAP_M = 600.0
MAX_CANDIDATE_STOPS = 40
MAX_EUCLIDEAN_KM = 35.0


def load_huts() -> gpd.GeoDataFrame:
    rows = pd.read_csv(HUTS_CSV, encoding="utf-8-sig")
    rows = rows[rows["include_in_evaluation"] == 1].copy()
    return gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy(rows["longitude"], rows["latitude"]),
        crs=WGS84_CRS,
    )


def load_stops(huts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    stops = pd.read_csv(STOPS_TXT, dtype=str)
    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    stops = stops.dropna(subset=["stop_lat", "stop_lon", "stop_name"])
    bounds = huts.total_bounds
    min_lon, min_lat, max_lon, max_lat = bounds
    stops = stops[
        stops["stop_lon"].between(min_lon - 0.35, max_lon + 0.35)
        & stops["stop_lat"].between(min_lat - 0.25, max_lat + 0.25)
    ].copy()
    stops = stops[
        stops["location_type"].fillna("").isin(["", "0"])
    ].copy()
    stops["dedupe_key"] = (
        stops["stop_name"].str.lower().str.strip()
        + "|"
        + stops["stop_lat"].round(5).astype(str)
        + "|"
        + stops["stop_lon"].round(5).astype(str)
    )
    stops = stops.drop_duplicates("dedupe_key")
    return gpd.GeoDataFrame(
        stops,
        geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]),
        crs=WGS84_CRS,
    )


def nearest_graph_nodes(graph: nx.DiGraph, points: gpd.GeoSeries) -> tuple[list, list[float]]:
    node_ids = list(graph.nodes)
    node_xy = [(graph.nodes[node]["x"], graph.nodes[node]["y"]) for node in node_ids]
    tree = cKDTree(node_xy)
    distances, positions = tree.query([(point.x, point.y) for point in points])
    return [node_ids[int(pos)] for pos in positions], [float(distance) for distance in distances]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0, help="Zero-based offset in included hut order.")
    parser.add_argument("--append", action="store_true", help="Append rows instead of replacing output.")
    args = parser.parse_args()

    print(f"Loading graph: {DEFAULT_GRAPH}", flush=True)
    with (ROOT / DEFAULT_GRAPH).open("rb") as f:
        graph = pickle.load(f)
    print(
        f"Loaded graph with {graph.number_of_nodes()} nodes and {graph.number_of_edges()} edges.",
        flush=True,
    )

    huts = load_huts()
    stops = load_stops(huts)
    huts_m = huts.to_crs(METRIC_CRS)
    stops_m = stops.to_crs(METRIC_CRS)
    print(f"Loaded {len(huts)} included huts and {len(stops)} filtered PT stop points.", flush=True)

    stop_nodes, stop_snap_m = nearest_graph_nodes(graph, stops_m.geometry)
    stops = stops.reset_index(drop=True)
    stops_m = stops_m.reset_index(drop=True)
    stops["graph_node"] = stop_nodes
    stops["snap_m"] = stop_snap_m
    stops = stops[stops["snap_m"] <= MAX_SNAP_M].copy()
    stops_m = stops_m.loc[stops.index].copy()
    stops = stops.reset_index(drop=True)
    stops_m = stops_m.reset_index(drop=True)
    print(f"Kept {len(stops)} PT stop points within {MAX_SNAP_M:g} m of the hiking graph.", flush=True)

    hut_nodes, hut_snap_m = nearest_graph_nodes(graph, huts_m.geometry)
    huts = huts.reset_index(drop=True)
    huts_m = huts_m.reset_index(drop=True)
    stop_xy = [(point.x, point.y) for point in stops_m.geometry]
    stop_tree = cKDTree(stop_xy)
    fields = [
        "hut_index",
        "hut_name",
        "hut_latitude",
        "hut_longitude",
        "candidate_status",
        "pt_stop_name",
        "pt_stop_id",
        "pt_stop_latitude",
        "pt_stop_longitude",
        "duration_h",
        "distance_km",
        "ascent_m",
        "descent_m",
        "max_hiking_category",
        "difficulty_status",
        "hut_snap_m",
        "pt_stop_snap_m",
        "notes",
    ]
    mode = "a" if args.append and OUTPUT.exists() else "w"
    with OUTPUT.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if mode == "w":
            writer.writeheader()

        for i, hut in huts.iloc[args.start :].iterrows():
            hut_name = hut["name"]
            hut_node = hut_nodes[i]
            hx, hy = huts_m.geometry.iloc[i].x, huts_m.geometry.iloc[i].y
            candidate_positions = stop_tree.query_ball_point(
                (hx, hy),
                r=MAX_EUCLIDEAN_KM * 1000,
            )
            candidate_positions = sorted(
                candidate_positions,
                key=lambda pos: stops_m.geometry.iloc[pos].distance(huts_m.geometry.iloc[i]),
            )[:MAX_CANDIDATE_STOPS]
            if not candidate_positions:
                writer.writerow(
                    {
                        "hut_index": hut["hut_index"],
                        "hut_name": hut_name,
                        "hut_latitude": hut["latitude"],
                        "hut_longitude": hut["longitude"],
                        "candidate_status": "no_nearby_pt_stop",
                        "hut_snap_m": round(hut_snap_m[i], 1),
                        "notes": f"No PT stop within {MAX_EUCLIDEAN_KM:g} km straight-line radius.",
                    }
                )
                continue

            best = None
            for stop_pos in candidate_positions:
                stop = stops.iloc[stop_pos]
                stop_node = stop["graph_node"]
                try:
                    duration_seconds, path_stop_to_hut = nx.bidirectional_dijkstra(
                        graph,
                        stop_node,
                        hut_node,
                        weight="seconds",
                    )
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                if duration_seconds > MAX_HOURS * 3600:
                    continue
                if best is None or duration_seconds < best[0]:
                    best = (duration_seconds, stop, path_stop_to_hut)

            if best is None:
                writer.writerow(
                    {
                        "hut_index": hut["hut_index"],
                        "hut_name": hut_name,
                        "hut_latitude": hut["latitude"],
                        "hut_longitude": hut["longitude"],
                        "candidate_status": "no_route_within_cutoff",
                        "hut_snap_m": round(hut_snap_m[i], 1),
                        "notes": (
                            f"No route within {MAX_HOURS:g} h among the nearest "
                            f"{len(candidate_positions)} PT stop candidates."
                        ),
                    }
                )
                f.flush()
                continue

            _, stop, path_stop_to_hut = best
            distance_km, duration_h, ascent_m, descent_m, max_rank, difficulty_status = path_stats(
                graph,
                path_stop_to_hut,
            )
            writer.writerow(
                {
                    "hut_index": hut["hut_index"],
                    "hut_name": hut_name,
                    "hut_latitude": hut["latitude"],
                    "hut_longitude": hut["longitude"],
                    "candidate_status": "candidate_computed",
                    "pt_stop_name": stop["stop_name"],
                    "pt_stop_id": stop["stop_id"],
                    "pt_stop_latitude": stop["stop_lat"],
                    "pt_stop_longitude": stop["stop_lon"],
                    "duration_h": round(duration_h, 3),
                    "distance_km": round(distance_km, 3),
                    "ascent_m": round(ascent_m, 1),
                    "descent_m": round(descent_m, 1),
                    "max_hiking_category": hiking_category_label(max_rank),
                    "difficulty_status": difficulty_status,
                    "hut_snap_m": round(hut_snap_m[i], 1),
                    "pt_stop_snap_m": round(float(stop["snap_m"]), 1),
                    "notes": (
                        "Shortest candidate from official GTFS 2026-09-05 stop list "
                        "over cached swisstopo swissTLM3D Wanderwege graph. "
                        "Needs manual/source confirmation before database import."
                    ),
                }
            )
            print(f"{i + 1}/{len(huts)} {hut_name}: {stop['stop_name']} {duration_h:.2f}h", flush=True)


if __name__ == "__main__":
    main()
