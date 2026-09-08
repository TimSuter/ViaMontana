"""Export selected PT candidates for the app: python -m research.export_pt_access_routes."""
import csv
import json
import pickle
import sqlite3
from pathlib import Path

import networkx as nx
from pyproj import Transformer
from scipy.spatial import cKDTree

from find_hiking_routes import DEFAULT_GRAPH, METRIC_CRS, WGS84_CRS, path_wgs84, path_stats

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "hut_access.sqlite"


def main():
    with (ROOT / "research/hut_pt_access_candidates.csv").open(encoding="utf-8-sig", newline="") as source:
        candidates = list(csv.DictReader(source))
    print("Loading hiking graph…", flush=True)
    with (ROOT / DEFAULT_GRAPH).open("rb") as source:
        graph = pickle.load(source)
    nodes = list(graph)
    tree = cKDTree([(graph.nodes[node]["x"], graph.nodes[node]["y"]) for node in nodes])
    transformer = Transformer.from_crs(WGS84_CRS, METRIC_CRS, always_xy=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(OUTPUT) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS access_routes (hut TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("DELETE FROM access_routes")
        for index, row in enumerate(candidates, 1):
            row["geometry_wkt"] = None
            if row["candidate_status"] == "candidate_computed":
                coordinates = [transformer.transform(float(row[f"{prefix}_longitude"]), float(row[f"{prefix}_latitude"])) for prefix in ("pt_stop", "hut")]
                _, positions = tree.query(coordinates)
                try:
                    _, path = nx.bidirectional_dijkstra(graph, nodes[int(positions[0])], nodes[int(positions[1])], weight="seconds")
                    if len(path) > 1:
                        row["geometry_wkt"] = path_wgs84(graph, path).wkt
                        stats = path_stats(graph, list(reversed(path)))
                        row["exit_duration_h"] = round(stats[1], 3)
                    else:
                        row["candidate_status"] = "no_mapped_walking_segment"
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    row["candidate_status"] = "no_connected_route"
            for field in ("hut_latitude", "hut_longitude", "pt_stop_latitude", "pt_stop_longitude", "duration_h", "distance_km", "ascent_m", "descent_m", "hut_snap_m", "pt_stop_snap_m"):
                row[field] = float(row[field]) if row.get(field) else None
            connection.execute("INSERT OR REPLACE INTO access_routes VALUES (?, ?)", (row["hut_name"], json.dumps(row)))
            print(f"{index}/{len(candidates)} {row['hut_name']}", flush=True)
    print(f"Saved {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
