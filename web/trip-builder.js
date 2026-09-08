export function createTripBuilder({ map, routeLayer, hutLayer, results, resultsTitle, setStatus, parseLineString, formatNumber, escapeHtml }) {
  const form = document.querySelector('#builder-form');
  let active = false, revision = 0, start = '', legs = [], choices = [], selected = null, exit = null, filters = {};
  const current = () => legs.at(-1)?.destination_hut ?? start;
  const visited = () => new Set([start, ...legs.map(leg => leg.destination_hut)]);
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
    const draw = (leg, color, weight = 5, onClick = null) => {
      const points = parseLineString(leg.geometry_wkt); bounds.push(...points);
      const line = L.polyline(points, { pane: 'routePane', color, weight, opacity: 0.85 }).addTo(routeLayer);
      if (onClick) line.on('click', onClick);
      return points;
    };
    const marker = (point, label, color, action) => {
      const dot = L.circleMarker(point, { pane: 'selectedHutPane', radius: 8, color, fillOpacity: 1 })
        .bindTooltip(escapeHtml(label)).addTo(routeLayer);
      if (action) dot.on('click', action);
    };
    resultsTitle.textContent = exit ? 'Trip complete' : `Next hut from ${current()}`;
    const chain = [start, ...legs.map(leg => leg.destination_hut), ...(exit ? [exit.pt_stop_name] : [])];
    const summary = block(exit ? 'Your completed trip' : 'Your trip', chain.join(' → '));
    const included = [...legs, ...(exit ? [exit] : [])];
    if (included.length) {
      const totals = document.createElement('p');
      totals.textContent = `Total: ${formatNumber(included.reduce((sum, leg) => sum + leg.duration_h, 0))} h · ${formatNumber(included.reduce((sum, leg) => sum + leg.distance_km, 0))} km · ↑ ${formatNumber(included.reduce((sum, leg) => sum + leg.ascent_m, 0))} m · ↓ ${formatNumber(included.reduce((sum, leg) => sum + leg.descent_m, 0))} m`;
      summary.append(totals);
    }
    legs.forEach((leg, index) => {
      const details = document.createElement('p'); details.textContent = `Leg ${index + 1}: ${leg.start_hut} → ${leg.destination_hut}. ${stats(leg)}`; summary.append(details);
      const points = draw(leg, '#174c3a'); marker(points[0], leg.start_hut, '#174c3a'); marker(points.at(-1), leg.destination_hut, '#174c3a');
    });
    if (legs.length || exit) summary.append(button(exit ? 'Reopen trip' : 'Undo last leg', () => {
      revision++; if (exit) exit = null; else legs.pop(); selected = null; loadChoices();
    }));
    results.append(summary);
    if (exit) {
      const points = draw(exit, '#7b2cbf');
      const stop = [exit.pt_stop_latitude, exit.pt_stop_longitude], hut = [exit.hut_latitude, exit.hut_longitude];
      bounds.push(stop, hut);
      [[hut, points[0]], [points.at(-1), stop]].forEach(gap => L.polyline(gap, { pane: 'routePane', color: '#777', dashArray: '4 6', weight: 2 }).addTo(routeLayer));
      marker(stop, exit.pt_stop_name, '#7b2cbf');
      results.append(block(`Exit: ${exit.hut_name} → ${exit.pt_stop_name}`, `${stats(exit)}. Computed access candidate. Dashed connections to the trail are unverified and excluded from totals. The exit is separate from the hut-to-hut filters.`));
      setStatus('Trip completed with a walk to public transport.');
    } else if (selected) {
      const points = draw(selected, '#e50909', 6); marker(points.at(-1), selected.destination_hut, '#e50909');
      const decision = block(selected.destination_hut, stats(selected));
      decision.append(button('Continue from this hut', () => { legs.push(selected); selected = null; loadChoices(); }));
      decision.append(button('Finish via public transport', finish));
      decision.append(button('Choose another hut', () => { revision++; selected = null; render(); }));
      results.append(decision);
      setStatus('Continue to another hut, or finish with this hut’s public transport access route.');
    } else {
      choices.forEach(leg => {
        const select = () => { revision++; selected = leg; render(); };
        const points = draw(leg, '#7b2cbf', 3, select);
        marker(points[0], current(), '#174c3a'); marker(points.at(-1), leg.destination_hut, '#7b2cbf', select);
        const card = block(leg.destination_hut, stats(leg)); card.append(button('Select hut', select)); results.append(card);
      });
      if (!choices.length) results.append(block('No next huts', 'No unvisited huts match these limits. Undo the last leg or start a new trip with different limits.'));
      if (legs.length) results.append(button('Finish from current hut via public transport', finish));
      setStatus(`${choices.length} available next huts. Select a route or destination marker on the map, or a hut in the list.`);
    }
    if (bounds.length) map.fitBounds(bounds, { padding: [32, 32] });
  }
  async function loadChoices() {
    const version = ++revision; choices = []; render(); setStatus('Loading available paths…');
    try {
      const data = await request(`/api/next-huts?${new URLSearchParams({ ...filters, start_hut: current() })}`);
      if (!active || version !== revision) return;
      choices = data.filter(leg => !visited().has(leg.destination_hut)); render();
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
  form.addEventListener('submit', event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    if (+values.min_duration_h > +values.max_duration_h || +values.min_elevation_change_m > +values.max_elevation_change_m) { setStatus('Minimum limits must not exceed maximum limits.'); return; }
    start = values.start_hut.trim(); if (!start) return;
    delete values.start_hut; filters = values; legs = []; selected = exit = null; loadChoices();
  });
  return {
    activate() { active = true; form.hidden = false; if (start && !selected && !exit) loadChoices(); else render(); },
    deactivate() { active = false; revision++; form.hidden = true; hutLayer.addTo(map); },
    selectStart(hut) { if (!start) { document.querySelector('#builder-hut').value = hut; form.requestSubmit(); } },
  };
}
