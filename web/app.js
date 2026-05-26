const form = document.querySelector("#search-form");
const startHutInput = document.querySelector("#start-hut");
const hutOptions = document.querySelector("#hut-options");
const results = document.querySelector("#results");
const resultsTitle = document.querySelector("#results-title");
const statusText = document.querySelector("#status-text");
const routeMapElement = document.querySelector("#map");
const mapSourceInputs = document.querySelectorAll("input[name='map_source']");

const numberFormat = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
});
const switzerlandBounds = [
  [45.75, 5.75],
  [47.9, 10.65],
];
const routeColors = ["#7b2cbf", "#00876c", "#3f3f46", "#8b5cf6", "#2f6f4e"];

let map;
let hutLayer;
let routeLayer;
let baseLayers;
let hikingPathLayer;
let activeBaseLayer;
const hutMarkersByName = new Map();
let selectedCard;

function formatNumber(value) {
  return numberFormat.format(value);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character];
  });
}

function hutDetailsHtml(marker) {
  const lines = [`<strong>${escapeHtml(marker.hut)}</strong>`];
  if (Number.isFinite(marker.altitude_m)) {
    lines.push(`Altitude: ${formatNumber(marker.altitude_m)} m`);
  }
  return lines.join("<br>");
}

function setStatus(message) {
  statusText.textContent = message;
}

function clearResults() {
  results.replaceChildren();
}

function renderEmpty(message) {
  clearResults();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  results.appendChild(empty);
}

function setMapSource(source) {
  const nextBaseLayer = baseLayers[source] ?? baseLayers.swisstopo;
  if (activeBaseLayer) {
    map.removeLayer(activeBaseLayer);
  }
  activeBaseLayer = nextBaseLayer;
  activeBaseLayer.addTo(map);

  if (source === "swisstopo") {
    hikingPathLayer.addTo(map);
  } else if (map.hasLayer(hikingPathLayer)) {
    map.removeLayer(hikingPathLayer);
  }
}

function initMap() {
  map = L.map(routeMapElement, {
    zoomControl: true,
    scrollWheelZoom: true,
  });

  map.createPane("hutPane");
  map.getPane("hutPane").style.zIndex = 650;
  map.createPane("routePane");
  map.getPane("routePane").style.zIndex = 660;
  map.createPane("selectedHutPane");
  map.getPane("selectedHutPane").style.zIndex = 720;

  baseLayers = {
    swisstopo: L.tileLayer(
      "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg",
      {
        attribution: "&copy; swisstopo",
        maxZoom: 19,
      },
    ),
    osm: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 19,
    }),
  };

  hikingPathLayer = L.tileLayer(
    "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swisstlm3d-wanderwege/default/current/3857/{z}/{x}/{y}.png",
    {
      attribution: "&copy; swisstopo",
      maxZoom: 19,
      opacity: 0.9,
    },
  );
  setMapSource("swisstopo");

  hutLayer = L.layerGroup().addTo(map);
  routeLayer = L.layerGroup().addTo(map);
  map.fitBounds(switzerlandBounds);
  window.addEventListener("resize", () => map.invalidateSize());
}

async function loadHutMarkers() {
  const response = await fetch("/api/hut-markers");
  if (!response.ok) {
    return;
  }
  const markers = await response.json();
  hutLayer.clearLayers();
  hutMarkersByName.clear();
  const latLngs = [];
  markers.forEach((marker) => {
    hutMarkersByName.set(marker.hut, marker);
    const latLng = [marker.latitude, marker.longitude];
    latLngs.push(latLng);
    L.circleMarker(latLng, {
      pane: "hutPane",
      interactive: true,
      radius: 5,
      color: "#174c3a",
      weight: 1,
      fillColor: "#236b52",
      fillOpacity: 0.75,
    })
      .bindTooltip(hutDetailsHtml(marker), { sticky: true })
      .bindPopup(hutDetailsHtml(marker))
      .addTo(hutLayer);
  });
  if (latLngs.length) {
    map.fitBounds(latLngs, { padding: [24, 24] });
  }
}

function parseLineString(wkt) {
  const body = wkt.slice(wkt.indexOf("(") + 1, wkt.lastIndexOf(")"));
  return body.split(",").map((pair) => {
    const [lon, lat] = pair.trim().split(/\s+/).map(Number);
    return [lat, lon];
  });
}

async function fetchLegGeometry(leg) {
  const params = new URLSearchParams({
    start_hut: leg.start_hut,
    destination_hut: leg.destination_hut,
  });
  const response = await fetch(`/api/route?${params}`);
  if (!response.ok) {
    throw new Error("Route geometry could not be loaded.");
  }
  return response.json();
}

async function showItineraryOnMap(itinerary, card) {
  map.invalidateSize();
  routeLayer.clearLayers();
  if (selectedCard) {
    selectedCard.classList.remove("is-selected");
  }
  selectedCard = card;
  selectedCard.classList.add("is-selected");

  const bounds = [];
  const legs = await Promise.all(itinerary.legs.map(fetchLegGeometry));
  legs.forEach((leg, index) => {
    const latLngs = parseLineString(leg.geometry_wkt);
    const routeColor = routeColors[index % routeColors.length];
    bounds.push(...latLngs);
    L.polyline(latLngs, {
      pane: "routePane",
      color: routeColor,
      weight: 5,
      opacity: 0.9,
      dashArray: "10 8",
      lineCap: "butt",
    })
      .bindPopup(
        [
          `<b>Day ${index + 1}</b>`,
          `${leg.start_hut} -> ${leg.destination_hut}`,
          `${formatNumber(leg.duration_h)} h`,
          `${formatNumber(leg.distance_km)} km`,
          leg.max_hiking_category,
        ].join("<br>"),
      )
      .addTo(routeLayer);
  });

  itinerary.huts.forEach((hut, index) => {
    const marker = hutMarkersByName.get(hut) ?? { hut };
    const leg = legs[index === 0 ? 0 : index - 1];
    const latLngs = parseLineString(leg.geometry_wkt);
    const latLng = index === 0 ? latLngs[0] : latLngs.at(-1);
    L.circleMarker(latLng, {
      pane: "selectedHutPane",
      interactive: true,
      radius: 7,
      color: "#ffffff",
      weight: 2,
      fillColor: index === 0 ? "#b4492d" : "#236b52",
      fillOpacity: 1,
    })
      .bindTooltip(hutDetailsHtml(marker), { sticky: true })
      .bindPopup(hutDetailsHtml(marker))
      .addTo(routeLayer);
  });

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [32, 32] });
  }
}

async function loadHutOptions(search = "") {
  const params = new URLSearchParams({ limit: "80" });
  if (search.trim()) {
    params.set("search", search.trim());
  }
  const response = await fetch(`/api/huts?${params}`);
  if (!response.ok) {
    return;
  }
  const data = await response.json();
  hutOptions.replaceChildren(
    ...data.huts.map((hut) => {
      const option = document.createElement("option");
      option.value = hut;
      return option;
    }),
  );
}

function metric(label, value) {
  const item = document.createElement("span");
  item.className = "metric";
  item.textContent = `${label}: ${value}`;
  return item;
}

function renderItinerary(itinerary, index) {
  const card = document.createElement("article");
  card.className = "itinerary-card";
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Show option ${index + 1} on the map`);

  const summary = document.createElement("div");
  summary.className = "itinerary-summary";

  const headingBlock = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = `Option ${index + 1}`;
  const chain = document.createElement("div");
  chain.className = "hut-chain";
  chain.textContent = itinerary.huts.join(" -> ");
  headingBlock.append(title, chain);

  const metrics = document.createElement("div");
  metrics.className = "metrics";
  metrics.append(
    metric("Avg", `${formatNumber(itinerary.average_daily_duration_h)} h/day`),
    metric("Time", `${formatNumber(itinerary.total_duration_h)} h`),
    metric("Distance", `${formatNumber(itinerary.total_distance_km)} km`),
    metric("Ascent", `${formatNumber(itinerary.total_ascent_m)} m`),
    metric("Descent", `${formatNumber(itinerary.total_descent_m)} m`),
    metric("Category", itinerary.max_hiking_category),
  );

  summary.append(headingBlock, metrics);

  const legs = document.createElement("div");
  legs.className = "legs";
  itinerary.legs.forEach((leg, legIndex) => {
    const routeColor = routeColors[legIndex % routeColors.length];
    const row = document.createElement("div");
    row.className = "leg";
    row.style.setProperty("--route-color", routeColor);

    const routeLine = document.createElement("div");
    routeLine.className = "leg-route-line";
    const swatch = document.createElement("span");
    swatch.className = "route-swatch";
    swatch.setAttribute("aria-hidden", "true");
    const route = document.createElement("div");
    route.className = "leg-route";
    route.textContent = `Day ${legIndex + 1}: ${leg.start_hut} -> ${leg.destination_hut}`;
    routeLine.append(swatch, route);

    const stats = document.createElement("div");
    stats.className = "leg-stats";
    stats.textContent = [
      `${formatNumber(leg.duration_h)} h`,
      `${formatNumber(leg.distance_km)} km`,
      `+${formatNumber(leg.ascent_m)} m`,
      `-${formatNumber(leg.descent_m)} m`,
      leg.max_hiking_category,
    ].join(" · ");

    row.append(routeLine, stats);
    legs.appendChild(row);
  });

  async function selectItinerary() {
    try {
      setStatus("Loading selected route geometry.");
      await showItineraryOnMap(itinerary, card);
      setStatus(`Showing option ${index + 1} on the map.`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  card.addEventListener("click", selectItinerary);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectItinerary();
    }
  });

  card.append(summary, legs);
  return card;
}

function formParams() {
  const data = new FormData(form);
  return new URLSearchParams({
    start_hut: data.get("start_hut"),
    days: data.get("days"),
    min_duration_h: data.get("min_duration_h"),
    max_duration_h: data.get("max_duration_h"),
    min_elevation_change_m: data.get("min_elevation_change_m"),
    max_elevation_change_m: data.get("max_elevation_change_m"),
    limit: "50",
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearResults();
  resultsTitle.textContent = "Searching";
  setStatus("Reading precomputed route legs from SQLite.");

  try {
    const response = await fetch(`/api/search?${formParams()}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Search failed.");
    }

    resultsTitle.textContent = `${data.result_count} option${data.result_count === 1 ? "" : "s"}`;
    setStatus(`${data.days} day route search from ${data.start_hut}.`);

    if (!data.itineraries.length) {
      renderEmpty("No route chain matched these constraints.");
      routeLayer.clearLayers();
      loadHutMarkers();
      return;
    }

    const cards = data.itineraries.map(renderItinerary);
    results.replaceChildren(...cards);
    map.invalidateSize();
    await showItineraryOnMap(data.itineraries[0], cards[0]);
    setStatus(`Showing option 1 on the map.`);
  } catch (error) {
    resultsTitle.textContent = "Search failed";
    setStatus(error.message);
    renderEmpty("Check that the route database exists and the hut name is valid.");
  }
});

let hutSearchTimer;
startHutInput.addEventListener("input", () => {
  window.clearTimeout(hutSearchTimer);
  hutSearchTimer = window.setTimeout(() => loadHutOptions(startHutInput.value), 180);
});

mapSourceInputs.forEach((input) => {
  input.addEventListener("change", () => {
    if (input.checked) {
      setMapSource(input.value);
    }
  });
});

loadHutOptions();
initMap();
loadHutMarkers();
