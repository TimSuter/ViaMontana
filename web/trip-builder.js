export function createTripBuilder({ map, routeLayer, hutLayer, results, resultsTitle, setStatus, parseLineString, formatNumber, escapeHtml }) {
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
  let active = false, revision = 0, start = '', legs = [], choices = [], selected = null, exit = null, arrival = null, arrivalError = null, filters = {};
  const current = () => legs.at(-1)?.destination_hut ?? start;
  const visited = () => new Set([start, ...legs.map(leg => leg.destination_hut)]);
  let refreshTimer, loading = false;
  const stats = leg => `${formatNumber(leg.duration_h)} h · ${formatNumber(leg.distance_km)} km · ↑ ${formatNumber(leg.ascent_m)} m · ↓ ${formatNumber(leg.descent_m)} m · ${leg.max_hiking_category}`;
  function button(text, action) {
    const element = document.createElement('button');
    element.type = 'button'; element.textContent = text;
    element.addEventListener('click', action);
    return element;
  }
  function block(title, text, color = null) {
    const element = document.createElement('section'); element.className = 'access-details';
    if (color) { element.classList.add('colored-route'); element.style.setProperty('--route-color', color); }
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
      if (onClick) line.on('click', onClick);
      return points;
    };
    const marker = (point, label, color, action) => {
      const dot = L.circleMarker(point, { pane: 'selectedHutPane', radius: 8, color, fillOpacity: 1 })
        .bindTooltip(escapeHtml(label)).addTo(routeLayer);
      if (action) dot.on('click', action);
    };
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
      const details = document.createElement('p');
      details.textContent = `Arrival: ${arrival.start_hut} -> ${start}. ${stats(arrival)}. Computed access candidate. Dashed connections to the trail are unverified and excluded from totals.`;
      summary.append(details);
    } else if (arrivalError) {
      const details = document.createElement('p');
      details.textContent = `Starting access route unavailable: ${arrivalError} Totals exclude the walk to the first hut.`;
      summary.append(details, button('Retry starting access route', () => { arrivalError = null; loadChoices(); }));
    }
    if (included.length) {
      const totals = document.createElement('p');
      totals.textContent = `Total: ${formatNumber(included.reduce((sum, leg) => sum + leg.duration_h, 0))} h · ${formatNumber(included.reduce((sum, leg) => sum + leg.distance_km, 0))} km · ↑ ${formatNumber(included.reduce((sum, leg) => sum + leg.ascent_m, 0))} m · ↓ ${formatNumber(included.reduce((sum, leg) => sum + leg.descent_m, 0))} m`;
      summary.append(totals);
    }
    legs.forEach((leg, index) => {
      const details = document.createElement('p'); details.textContent = `Leg ${index + 1}: ${leg.start_hut} → ${leg.destination_hut}. ${stats(leg)}`; summary.append(details);
      const points = draw(leg, tripColor); marker(points[0], leg.start_hut, tripColor); marker(points.at(-1), leg.destination_hut, tripColor);
    });
    if (legs.length || exit) summary.append(button(exit ? 'Reopen trip' : 'Undo last leg', () => {
      revision++; if (exit) exit = null; else legs.pop(); selected = null; loadChoices();
    }));
    results.append(summary);
    if (exit) {
      const points = draw(exit, tripColor);
      const stop = [exit.pt_stop_latitude, exit.pt_stop_longitude], hut = [exit.hut_latitude, exit.hut_longitude];
      bounds.push(stop, hut);
      [[hut, points[0]], [points.at(-1), stop]].forEach(gap => L.polyline(gap, { pane: 'routePane', color: '#777', dashArray: '4 6', weight: 2 }).addTo(routeLayer));
      marker(stop, exit.pt_stop_name, tripColor);
      results.append(block(`Exit: ${exit.hut_name} → ${exit.pt_stop_name}`, `${stats(exit)}. Computed access candidate. Dashed connections to the trail are unverified and excluded from totals. The exit is separate from the hut-to-hut filters.`));
      setStatus('Trip completed with a walk to public transport.');
    } else if (selected) {
      const color = optionColor(selected);
      const points = draw(selected, color, 6, null, selected.is_suggestion); marker(points.at(-1), selected.destination_hut, color);
      const decision = block(selected.destination_hut, `${stats(selected)}${selected.is_suggestion ? ' - Suggestion: outside your current limits.' : ''}`, color);
      decision.append(button('Continue from this hut', () => { legs.push(selected); selected = null; loadChoices(); }));
      decision.append(button('Finish via public transport', finish));
      decision.append(button('Choose another hut', () => { revision++; selected = null; render(); }));
      results.append(decision);
      setStatus('Continue to another hut, or finish with this hut’s public transport access route.');
    } else if (loading) {
      results.append(block('Loading next huts', 'Finding matching routes and suggested alternatives.'));
    } else {
      const suggestionCount = choices.filter(leg => leg.is_suggestion).length;
      if (suggestionCount) results.append(block('Suggested alternatives', 'Fewer than three unvisited huts match your limits. Dashed routes labeled as suggestions are the closest available alternatives outside those limits.'));
      if (choices.length < 3) results.append(block('Limited available routes', 'Fewer than three unvisited huts have mapped routes from this hut.'));
      choices.forEach(leg => {
        const select = () => { revision++; selected = leg; render(); };
        const color = optionColor(leg);
        const points = draw(leg, color, 3, select, leg.is_suggestion);
        marker(points[0], current(), tripColor); marker(points.at(-1), `${leg.destination_hut}${leg.is_suggestion ? ' (suggestion)' : ''}`, color, select);
        const card = block(leg.destination_hut, `${stats(leg)}${leg.is_suggestion ? ' - Suggestion: outside your current time or elevation limits.' : ''}`, color); card.append(button('Select hut', select)); results.append(card);
      });
      if (!choices.length) results.append(block('No next huts', 'No unvisited huts have mapped routes from this hut. Undo the last leg or start a new trip with different limits.'));
      if (legs.length) results.append(button('Finish from current hut via public transport', finish));
      setStatus(`${choices.length} available next huts (${suggestionCount} suggestions outside your limits). Select a route or destination marker on the map, or a hut in the list.`);
    }
    if (bounds.length) map.fitBounds(bounds, { padding: [32, 32] });
  }
  async function loadChoices() {
    const version = ++revision; choices = []; loading = true; render(); setStatus('Loading available paths…');
    try {
      const params = new URLSearchParams({ ...filters, start_hut: current(), include_suggestions: 'true' });
      visited().forEach(hut => params.append('excluded_huts', hut));
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
      choices = nextResult.value.filter(leg => !visited().has(leg.destination_hut)); render();
    } catch (error) {
      if (active && version === revision) { setStatus(error.message); results.append(button('Retry loading paths', loadChoices)); }
    }
  }
  async function finish() {
    const version = ++revision; const pending = selected;
    const hut = pending?.destination_hut ?? current(); setStatus('Loading the walk to public transport…');
    try {
      const data = await request(`/api/exit-route?${new URLSearchParams({ hut })}`);
      if (!active || version !== revision) return;
      if (pending) legs.push(pending);
      selected = null; exit = data; render();
    } catch (error) { if (active && version === revision) setStatus(error.message); }
  }
  function updateParameters(newTrip = false) {
    window.clearTimeout(refreshTimer);
    if (!active || !form.checkValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    if (+values.min_duration_h > +values.max_duration_h || +values.min_elevation_change_m > +values.max_elevation_change_m) { setStatus('Minimum limits must not exceed maximum limits.'); return; }
    const nextStart = values.start_hut.trim(); if (!nextStart) return;
    if (newTrip || nextStart !== start) {
      optionColors.clear();
      start = nextStart; legs = []; exit = arrival = arrivalError = null;
    }
    delete values.start_hut; filters = values; selected = null;
    exit = null;
    loadChoices();
  }
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
    activate() { active = true; form.hidden = false; if (start && !selected && !exit) loadChoices(); else render(); },
    deactivate() { window.clearTimeout(refreshTimer); active = false; revision++; form.hidden = true; hutLayer.addTo(map); },
    selectStart(hut) { if (!start) { document.querySelector('#builder-hut').value = hut; form.requestSubmit(); } },
  };
}
