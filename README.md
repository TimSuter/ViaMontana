ViaMontana
=====

ViaMontana is a local-first multiday hiking planner between Swiss mountain huts.

The first version uses precomputed hut-to-hut route edges. During offline
preprocessing, ViaMontana loads hut data, loads or builds a hiking trail graph,
precomputes feasible daily hiking legs between nearby huts, and saves those
route legs to disk.

The live app does not calculate OSM routes during user requests. It searches a
precomputed graph of feasible hut-to-hut hiking legs.

Users can plan itineraries by specifying:

- number of days
- maximum hiking duration per day
- maximum ascent per day
- maximum descent per day
- maximum hiking category
- optional start hut
- optional end hut

In the web hut-to-hut planner, **Days includes arrival and departure**.
Day 1 is the walk from public transport to the starting hut, and the final
day is the walk from the last hut back to public transport. The minimum is
2 days (one overnight stay); 3 days means two overnight stays and one
hut-to-hut leg. Time and elevation-change limits apply only to hut-to-hut legs; arrival
and departure walks are exempt. Trips without suitable mapped access at both ends are
excluded. Totals include both access hikes using the correct direction of
travel; unverified connections to the trail remain excluded.

## Architecture

### Offline preprocessing

- Load huts from the existing `.pkl` file.
- Load or build a hiking trail graph.
- Precompute feasible hut-to-hut routes between nearby huts.
- Save route legs to disk in a simple, reproducible format.

## Swiss Hiking Routes

`find_hiking_routes.py` builds hut-to-hut routes on the official swisstopo
`swissTLM3D Wanderwege` network using NetworkX. The first run downloads the
swisstopo GeoPackage and builds a cached graph in `data/swisstopo_wanderwege/`;
later runs reuse that graph.

Default 3-day itinerary map from Vermigel Hütte:

```powershell
.\.venv\Scripts\python.exe find_hiking_routes.py
```

This uses a 20 km daily neighbor search radius by default. Each route leg is
treated as one hiking day.

Build a CSV route table for all huts:

```powershell
.\.venv\Scripts\python.exe find_hiking_routes.py --all-huts --skip-map
```

Useful itinerary inputs:

```powershell
.\.venv\Scripts\python.exe find_hiking_routes.py --days 3 --min-hours 5 --max-hours 8 --neighbor-radius-km 20
```

Route legs include the hardest swisstopo hiking category found on the route:
`Wanderweg`, `Bergwanderweg`, `Alpinwanderweg`, or `unknown`.

## Route Database

### Build a trip

Use **Build a trip** to select a starting hut and walking-time/elevation-change
limits for each hut-to-hut leg. All matching paths to unvisited huts appear on
the map and in the list. Select a destination, then choose **Continue from this
hut** or **Finish via public transport**. Chosen legs remain visible, with a
trip summary and an undo option. A completed trip can be reopened.

Changing parameters refreshes suggestions automatically. Time and elevation
changes preserve chosen legs; changing the starting hut starts a new trip.
When fewer than three unvisited huts match, up to three additional mapped
routes are shown as labeled, dashed suggestions outside the limits. Alternatives are
ranked by their combined time and elevation deviation, normalized by the
selected ranges (at least 1 hour and 100 meters). If fewer routes exist,
all available choices are shown with a note. Each option has its own color,
matched in the map and list; chosen hikes and access walks use dark green.

The final exit follows the selected access path in reverse, with walking time
computed in the hut-to-stop direction. It is separate from the hut-to-hut
filters. Unverified connections to the trail remain dashed and are excluded
from totals. Regenerate the access export after updating to prepare exit times.

The starting hut's access walk is shown on the map and included in the trip
summary and totals. Arrival and exit walks are exempt from the hut-to-hut
filters. If no starting access walk is available, the builder displays a
message and still allows hut-to-hut planning.

### Public transport access data

Prepare the local access database after generating the research candidates:

```powershell
.\.venv\Scripts\python.exe -m research.compute_pt_access_candidates
.\.venv\Scripts\python.exe -m research.export_pt_access_routes
```

The exporter uses `research/hut_pt_access_candidates.csv` and the cached
hiking graph to write `data/hut_access.sqlite`. Rerun it when candidates or
the graph change. The app reads this database without loading the hiking
graph. Generated CSV and SQLite files remain local and are ignored by Git.
Huts without a computed path show a no-route message.

`fill_hiking_route_database.py` generates the app-facing SQLite route database.
It uses `find_hiking_routes.py` as the routing backend, but the final app should
query only the SQLite database and should not load the full NetworkX trail graph.

The intended workflow is:

1. Offline batch generation loads the included huts.
2. It loads or builds the cached swisstopo trail graph.
3. It calculates single-day hut-to-hut routes for every included start hut.
4. It writes the precomputed route legs to SQLite.
5. The app reads the SQLite `routes` table to find accessible huts and route
   geometries.

Default generation command:

```powershell
.\.venv\Scripts\python.exe fill_hiking_route_database.py
```

By default this writes to:

```text
data/hiking_routes.sqlite
```

Default route constraints:

- minimum duration: 2 hours
- maximum duration: 14 hours
- nearest-neighbor hut radius: 30 km
- included huts only: `include_in_evaluation == 1`

To use a separate inclusion review CSV:

```powershell
.\.venv\Scripts\python.exe fill_hiking_route_database.py --inclusion-csv .\hut_inclusion.csv
```

To force a graph rebuild before filling the database:

```powershell
.\.venv\Scripts\python.exe fill_hiking_route_database.py --rebuild-graph
```

The script clears and refills the generated `routes` table by default, so stale
route rows from previous constraints do not remain in the database. Use
`--append` only when you explicitly want to keep existing rows.

### Database Schema

The main table is `routes`:

```sql
CREATE TABLE routes (
    start_hut TEXT NOT NULL,
    destination_hut TEXT NOT NULL,
    duration_h REAL NOT NULL,
    distance_km REAL NOT NULL,
    ascent_m REAL NOT NULL,
    descent_m REAL NOT NULL,
    max_hiking_category TEXT NOT NULL,
    difficulty_status TEXT NOT NULL,
    geometry_wkt TEXT NOT NULL,
    PRIMARY KEY (start_hut, destination_hut)
);
```

`max_hiking_category` is the hardest swisstopo category on the route, ordered as:

```text
Wanderweg < Bergwanderweg < Alpinwanderweg
```

`difficulty_status` indicates whether the route category was fully available:

- `mapped`: every route segment had a recognized hiking category
- `partial`: at least one segment had a recognized category, but not all did
- `unknown`: no recognized category was available for the route

`geometry_wkt` stores the precomputed route geometry in WGS84 as WKT. The app can
read this directly for map rendering without recalculating the route.

The script also writes `route_database_metadata`, which records values such as
the fill time, included hut count, graph path, and route constraints.

### Querying Routes

Find all precomputed routes from one hut:

```sql
SELECT
    destination_hut,
    duration_h,
    distance_km,
    ascent_m,
    descent_m,
    max_hiking_category,
    difficulty_status
FROM routes
WHERE start_hut = ?
ORDER BY duration_h;
```

Apply app-level filters directly in SQLite:

```sql
SELECT *
FROM routes
WHERE start_hut = ?
  AND duration_h <= ?
  AND ascent_m <= ?
  AND descent_m <= ?
ORDER BY duration_h;
```

Get the stored route geometry for a selected route:

```sql
SELECT geometry_wkt
FROM routes
WHERE start_hut = ?
  AND destination_hut = ?;
```

The full swisstopo graph is only needed when generating or refreshing the
database. Normal app requests should use these SQLite queries.

## Web Application

The first web application slice is a FastAPI backend with a responsive static
frontend in `web/`. The backend reads `data/hiking_routes.sqlite`, searches the
precomputed single-day route legs, and returns matching one-day or multiday hut
chains. It does not load the full swisstopo graph.

Install the API extras if they are not already available:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[api]"
```

Run the local website:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

If the server was already running, stop the existing process before starting it
again. On Windows, a stale Uvicorn process can leave the port unavailable and
raise an error such as `WinError 10013`.

Find Python processes:

```powershell
Get-Process python
```

Stop the stale process by its process id:

```powershell
Stop-Process -Id <PROCESS_ID>
```

Then start Uvicorn again:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

If port `8000` is still blocked, run the app on another local port:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8080
```

Then open:

```text
http://127.0.0.1:8080
```

The UI accepts:

- starting hut
- number of hiking days
- minimum and maximum hiking time per day
- minimum and maximum elevation change per day

When no route is selected, the map shows all huts available in the SQLite route
database. Search results stay visible as a list, and the first route option is
selected automatically after each search. Selecting a different route card loads
its stored route geometry from SQLite and draws it on the map. The map currently
uses swisstopo's color base map with the swisstopo hiking trail layer as an
overlay.

The web UI includes the ViaMontana logo from `media/logo.png` and uses a
matching red-and-white visual theme. The map source toggle can switch between
swisstopo and OpenStreetMap; the swisstopo hiking trail overlay is shown only
with the swisstopo base map. Planned route legs are drawn as dashed lines in
colors distinct from swisstopo's hiking-path difficulty colors so the underlying
trail categories remain visible. Each route leg in the result list includes a
matching dashed color swatch.

Hut markers are interactive in both the default all-huts view and selected-route
view. Hovering or clicking a hut marker shows the hut name and altitude when the
hut dataset provides an elevation value.

The current elevation-change filter uses `ascent_m + descent_m` for each day.
Results are assembled from the SQLite `routes` table and shown as candidate
itineraries. The API ranks matching itineraries by how closely each day's hiking
time matches the midpoint of the requested time range. For example, a 4 to 8
hour input targets 6 hours per day, and routes with daily legs closest to 6
hours appear first.

For the longer-term cross-platform app direction, the UI should move toward an
Ionic React frontend with Capacitor. Ionic provides mobile-ready web components
for responsive browser use, and Capacitor can later package the same web app for
iOS and Android. A Windows desktop package can be added later with a desktop web
wrapper such as Tauri or Electron while keeping the API and route database model
unchanged.

### Local planner

- Read the precomputed SQLite route database.
- Build a hut graph where nodes are huts and edges are feasible daily hikes.
- Search for multiday itineraries that satisfy user constraints.

### Later

- Wrap the planner in FastAPI.
- Add a web UI and map.
- Deploy on one low-cost server.
- Convert the web UI to a PWA for mobile and desktop installation.

## Development principles

- Keep business logic pure Python.
- Keep data formats simple and reproducible.
- Make the prototype testable without a web server.
- Avoid paid APIs during local development.
