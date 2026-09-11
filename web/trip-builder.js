export function createTripBuilder({ map, focusMap, resetMapView, getHutLocation, routeLayer, hutLayer, results, resultsTitle, setStatus, parseLineString, formatNumber, escapeHtml }) {
  const tripColor = '#174c3a';
  const optionPalette = ['#0072b2', '#d55e00', '#8e44ad', '#cc397b', '#a07800', '#009eaa', '#6554c0', '#85502b'];
  const optionColors = new Map();
  function optionColor(leg) {
    const key = JSON.stringify([leg.start_hut, leg.destination_hut]);
    if (!optionColors.has(key)) {
      const index = optionColors.size;
      optionColors.set(key, optionPalette[index] ?? `hsl(${(index * 137.508) % 360}, 70%, 40%)`);
    }
    return optionColors.get(key);
  }
  const form = document.querySelector('#builder-form');
  const tripActions = document.querySelector('#trip-actions');
  let active = false, revision = 0, start = '', legs = [], choices = [], exit = null, arrival = null, arrivalError = null, filters = {};
  const current = () => legs.at(-1)?.destination_hut ?? start;
  const previousHut = () => legs.at(-1)?.start_hut;
  let refreshTimer, loading = false, detailsOpen = false;
  const stats = leg => `${formatNumber(leg.duration_h)} h · ${formatNumber(leg.distance_km)} km · ↑ ${formatNumber(leg.ascent_m)} m · ↓ ${formatNumber(leg.descent_m)} m · ${leg.max_hiking_category}`;
  function button(text, action) {
    const element = document.createElement('button');
    element.type = 'button'; element.textContent = text;
    element.addEventListener('click', action);
    return element;
  }
  function block(title, text) {
    const element = document.createElement('section'); element.className = 'access-details';
    const heading = document.createElement('h3'); heading.textContent = title;
    const body = document.createElement('p'); body.textContent = text;
    element.append(heading, body); return element;
  }
  async function request(url) {
    const response = await fetch(url); const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Routes could not be loaded.');
    return data;
  }
  function render() {
    if (!active) return;
    tripActions.replaceChildren();
    tripActions.hidden = !start;
    map.invalidateSize();
    results.replaceChildren(); routeLayer.clearLayers(); map.closePopup();
    if (!start) {
      hutLayer.addTo(map);
      resultsTitle.textContent = 'Build a trip';
      setStatus('Choose a starting hut and limits for each hut-to-hut leg.');
      return;
    }
    map.removeLayer(hutLayer);
    const bounds = [];
    const draw = (leg, color, weight = 5, onClick = null, suggested = false) => {
      const points = parseLineString(leg.geometry_wkt); bounds.push(...points);
      const line = L.polyline(points, { pane: 'routePane', color, weight, opacity: 0.85, dashArray: suggested ? '8 6' : null }).addTo(routeLayer);
      const title = `${leg.start_hut ?? leg.hut_name} \u2192 ${leg.destination_hut ?? leg.pt_stop_name}`;
      const note = suggested ? 'Suggestion: outside your limits.' : '';
      const accessNote = leg === arrival || leg === exit ? 'Computed access walk; dashed connections are unverified and excluded from totals.' : '';
      line.bindTooltip(`<strong>${escapeHtml(title)}</strong><br>${escapeHtml(stats(leg))}${note ? `<br>${note}` : ''}${accessNote ? `<br>${accessNote}` : ''}${onClick ? '<br>Click to add this hike' : ''}`, { sticky: true });
      if (onClick) {
        line.on('click', onClick);
        line.on('mouseover', () => line.setStyle({ weight: weight + 3, opacity: 1 }));
        line.on('mouseout', () => line.setStyle({ weight, opacity: 0.85 }));
      }
      return points;
    };
    const marker = (point, label, color, action) => {
      const dot = L.circleMarker(point, { pane: 'selectedHutPane', radius: 8, color, fillOpacity: 1 })
        .bindTooltip(escapeHtml(label)).addTo(routeLayer);
      if (action) dot.on('click', action);
    };
    const startPoint = getHutLocation(start);
    if (startPoint) {
      bounds.push(startPoint);
      if (!arrival && !legs.length) marker(startPoint, start, tripColor);
    }
    resultsTitle.textContent = exit ? 'Trip complete' : `Next hut from ${current()}`;
    const chain = [...(arrival ? [arrival.start_hut] : []), start, ...legs.map(leg => leg.destination_hut), ...(exit ? [exit.pt_stop_name] : [])];
    const summary = block(exit ? 'Your completed trip' : 'Your trip', chain.join(' → '));
    const included = [...(arrival ? [arrival] : []), ...legs, ...(exit ? [exit] : [])];
    if (arrival) {
      const points = draw(arrival, tripColor);
      bounds.push(arrival.start_point, arrival.end_point);
      [[arrival.start_point, points[0]], [points.at(-1), arrival.end_point]].forEach(gap => {
        L.polyline(gap, { pane: 'routePane', color: '#777', dashArray: '4 6', weight: 2 })
          .bindTooltip('Unverified connection to trail; excluded from totals').addTo(routeLayer);
      });
      marker(arrival.start_point, `Public transport: ${arrival.start_hut}`, tripColor);
      marker(arrival.end_point, start, tripColor);
    } else if (arrivalError) {
      const details = document.createElement('p');
      details.textContent = 'Arrival walk unavailable; totals start at the first hut.';
      summary.append(details);
    }
    if (included.length) {
      const totals = document.createElement('p');
      totals.textContent = `Total: ${formatNumber(included.reduce((sum, leg) => sum + leg.duration_h, 0))} h · ${formatNumber(included.reduce((sum, leg) => sum + leg.distance_km, 0))} km · ↑ ${formatNumber(included.reduce((sum, leg) => sum + leg.ascent_m, 0))} m · ↓ ${formatNumber(included.reduce((sum, leg) => sum + leg.descent_m, 0))} m`;
      summary.append(totals);
      const details = document.createElement('details');
      details.className = 'trip-details';
      details.open = detailsOpen;
      details.addEventListener('toggle', () => {
        if (details.isConnected) detailsOpen = details.open;
      });
      const heading = document.createElement('summary');
      heading.textContent = 'Trip details';
      details.append(heading);
      included.forEach((leg, index) => {
        const item = document.createElement('section');
        const label = document.createElement('h4');
        const kind = leg === arrival ? 'Arrival' : leg === exit ? 'Departure' : `Hike ${index + 1 - (arrival ? 1 : 0)}`;
        label.textContent = `${kind}: ${leg.start_hut ?? leg.hut_name} \u2192 ${leg.destination_hut ?? leg.pt_stop_name}`;
        const metrics = document.createElement('p');
        metrics.textContent = `${formatNumber(leg.duration_h)} h · ${formatNumber(leg.distance_km)} km · Ascent ${formatNumber(leg.ascent_m)} m · Descent ${formatNumber(leg.descent_m)} m · ${leg.max_hiking_category}`;
        item.append(label, metrics);
        if (leg === arrival || leg === exit) {
          const note = document.createElement('p');
          note.textContent = 'Computed access walk. Dashed connections to the trail are unverified and excluded from totals.';
          item.append(note);
        }
        details.append(item);
      });
      summary.append(details);
    }
    legs.forEach(leg => {
      const points = draw(leg, tripColor); marker(points[0], leg.start_hut, tripColor); marker(points.at(-1), leg.destination_hut, tripColor);
    });
    const actions = document.createElement('div');
    actions.className = 'form-actions';
    const undo = button('Undo', () => {
      revision++; if (exit) exit = null; else legs.pop(); loadChoices();
    });
    undo.disabled = !legs.length && !exit;
    actions.append(undo);
    if (!exit) {
      const finishButton = button('Finish via public transport', finish);
      finishButton.disabled = loading;
      actions.append(finishButton);
    }
    tripActions.append(actions);
    results.append(summary);
    if (exit) {
      const points = draw(exit, tripColor);
      const stop = [exit.pt_stop_latitude, exit.pt_stop_longitude], hut = [exit.hut_latitude, exit.hut_longitude];
      bounds.push(stop, hut);
      [[hut, points[0]], [points.at(-1), stop]].forEach(gap => L.polyline(gap, { pane: 'routePane', color: '#777', dashArray: '4 6', weight: 2 }).addTo(routeLayer));
      marker(stop, exit.pt_stop_name, tripColor);
      setStatus('Trip complete. Undo to continue planning.');
    } else if (loading) {
      setStatus('Loading next hikes...');
    } else {
      const suggestionCount = choices.filter(leg => leg.is_suggestion).length;
      choices.forEach(leg => {
        const select = () => {
          if (loading || exit || !choices.includes(leg)) return;
          legs.push(leg); loadChoices();
        };
        const color = optionColor(leg);
        const points = draw(leg, color, 4, select, leg.is_suggestion);
        marker(points[0], current(), tripColor);
        marker(points.at(-1), `${leg.destination_hut} - ${stats(leg)}${leg.is_suggestion ? ' (outside limits)' : ''}`, color, select);
      });
      setStatus(choices.length
        ? `Hover for hike details. Click a route to add it.${suggestionCount ? ' Dashed routes are outside your limits.' : ''}`
        : 'No next hikes available. Undo or finish via public transport.');
    }
    if (bounds.length && !loading) focusMap(bounds, { padding: [32, 32], maxZoom: 13 });
  }
  async function loadChoices() {
    const version = ++revision; choices = []; loading = true; render(); setStatus('Loading available paths…');
    try {
      const params = new URLSearchParams({ ...filters, start_hut: current(), include_suggestions: 'true' });
      const previous = previousHut();
      if (previous) params.append('excluded_huts', previous);
      const needsArrival = !arrival && !arrivalError;
      const [nextResult, arrivalResult] = await Promise.allSettled([
        request(`/api/next-huts?${params}`),
        needsArrival ? request(`/api/access-leg?${new URLSearchParams({ hut: start, direction: 'arrival' })}`) : Promise.resolve(arrival),
      ]);
      if (!active || version !== revision) return;
      if (needsArrival) {
        if (arrivalResult.status === 'fulfilled') arrival = arrivalResult.value;
        else arrivalError = arrivalResult.reason.message;
      }
      loading = false;
      if (nextResult.status === 'rejected') { render(); throw nextResult.reason; }
      choices = nextResult.value.filter(leg => leg.destination_hut !== previous); render();
    } catch (error) {
      if (active && version === revision) { setStatus(error.message); results.append(button('Retry loading paths', loadChoices)); }
    }
  }
  async function finish() {
    if (loading || exit) return;
    loading = true; render();
    const version = ++revision;
    const hut = current(); setStatus('Loading the walk to public transport…');
    try {
      const data = await request(`/api/exit-route?${new URLSearchParams({ hut })}`);
      if (!active || version !== revision) return;
      loading = false; exit = data; render();
    } catch (error) {
      if (active && version === revision) { loading = false; render(); setStatus(error.message); }
    }
  }
  function updateParameters(newTrip = false) {
    window.clearTimeout(refreshTimer);
    if (!active || !form.checkValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    if (+values.min_duration_h > +values.max_duration_h || +values.min_elevation_change_m > +values.max_elevation_change_m) { setStatus('Minimum limits must not exceed maximum limits.'); return; }
    const nextStart = values.start_hut.trim(); if (!nextStart) return;
    const startingNewTrip = newTrip || nextStart !== start;
    if (startingNewTrip) {
      optionColors.clear();
      detailsOpen = false;
      start = nextStart; legs = []; exit = arrival = arrivalError = null;
    }
    delete values.start_hut; filters = values;
    exit = null;
    loadChoices();
    if (startingNewTrip) {
      const point = getHutLocation(start);
      if (point) focusMap([point], { padding: [32, 32], maxZoom: 13 });
    }
  }
  form.addEventListener('reset', () => {
    window.clearTimeout(refreshTimer);
    revision++;
    start = '';
    legs = []; choices = [];
    exit = arrival = arrivalError = null;
    filters = {};
    loading = detailsOpen = false;
    optionColors.clear();
    if (active) {
      render();
      resetMapView();
      document.querySelector('#builder-hut').focus();
    }
  });
  form.addEventListener('input', () => {
    revision++;
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => updateParameters(), 300);
  });
  form.addEventListener('change', () => updateParameters());
  form.addEventListener('submit', event => {
    event.preventDefault(); updateParameters(true);
  });
  return {
    activate() { active = true; form.hidden = false; if (start && !exit) loadChoices(); else render(); },
    deactivate() { window.clearTimeout(refreshTimer); active = false; revision++; form.hidden = true; tripActions.hidden = true; hutLayer.addTo(map); },
    selectStart(hut) { if (!start) { document.querySelector('#builder-hut').value = hut; form.requestSubmit(); } },
  };
}
