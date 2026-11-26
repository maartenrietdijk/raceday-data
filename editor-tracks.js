const RaceDayTracks = (() => {
  let draft, activeId, query = '', loading, loadingKey, publishing = false;
  const fields = {
    length: 'Lengte', turns: 'Aantal bochten', direction: 'Rijrichting',
    openedYear: 'Eerste race', circuitType: 'Soort circuit', elevationChange: 'Hoogteverschil',
    lapRecord: 'Ronderecord', lapRecordHolder: 'Recordhouder', timeZoneIdentifier: 'Tijdzone',
    firstFormula1GrandPrix: 'Eerste F1-Grand Prix (jaar)',
    assetName: 'Naam app-asset (optioneel)', darkAssetName: 'Naam donker app-asset (optioneel)'
  };
  const optional = new Set(['elevationChange', 'firstFormula1GrandPrix', 'assetName', 'darkAssetName']);
  const isOval = type => typeof type === 'string' && type.trim().toLowerCase() === 'oval';
  const storageKey = () => 'raceday_tracks_v1:' + [state.github.repo, state.github.branch, state.github.path].join('/');
  const encoded = text => btoa(Array.from(new TextEncoder().encode(text), byte => String.fromCharCode(byte)).join(''));
  const decoded = base64 => new TextDecoder().decode(Uint8Array.from(atob(base64), byte => byte.charCodeAt(0)));

  function connection() {
    const {token, repo, branch, path} = state.github;
    const file = [...(path || '').split('/').filter(Boolean), 'tracks.json'].map(encodeURIComponent).join('/');
    return token && repo ? {
      url: 'https://api.github.com/repos/' + repo + '/contents/' + file,
      branch: branch || 'main',
      headers: { 'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json' }
    } : null;
  }

  function checkGeometry(g) {
    if (!g || ![g.width, g.height].every(n => Number.isFinite(n) && n >= 1 && n <= 10000) ||
        !Array.isArray(g.points) || g.points.length < 4 || g.points.length > 2000 ||
        !Array.isArray(g.turnLabels) || g.turnLabels.length > 100 || !Array.isArray(g.sectorEnds)) return false;
    const validEnds = (ends, count) => Array.isArray(ends) &&
      (count == null ? ends.length >= 1 && ends.length <= 11 : ends.length === count) &&
      ends.every((end, i) => Number.isInteger(end) && end > (ends[i - 1] ?? 0) && end < g.points.length - 1);
    const validNumbers = g.turnNumbers == null || (Array.isArray(g.turnNumbers) &&
      g.turnNumbers.length === g.turnLabels.length && new Set(g.turnNumbers).size === g.turnNumbers.length &&
      g.turnNumbers.every(number => typeof number === 'string' && /^[1-9][0-9]?[A-Z]?$/.test(number)));
    const key = point => JSON.stringify(point);
    return validEnds(g.sectorEnds) && (g.timingSectorEnds == null || validEnds(g.timingSectorEnds)) && validNumbers &&
      key(g.points[0]) === key(g.points.at(-1)) && key(g.points[0]) !== key(g.points[1]) &&
      [...g.points, ...g.turnLabels].every(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) &&
        p[0] >= 0 && p[0] <= g.width && p[1] >= 0 && p[1] <= g.height);
  }

  function validate(data, {partial = false} = {}) {
    if (data?.schemaVersion !== 1 || !Array.isArray(data.tracks) || !data.tracks.length) throw new Error('Kies een geldige tracks.json.');
    const ids = new Set();
    const names = new Set();
    const aliases = new Set();
    for (const t of data.tracks) {
      const name = t?.circuitName?.trim();
      if (!name || name !== t.circuitName || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(t.id) || ids.has(t.id) || names.has(name)) {
        throw new Error('Elke baan heeft een eigen naam en unieke ID nodig.');
      }
      ids.add(t.id);
      names.add(name);
      if (!Number.isFinite(t.orientationAngle) || Math.abs(t.orientationAngle) > 180) throw new Error(name + ' · Vul een hoek tussen −180 en 180 in.');
      if ((t.latitude == null) !== (t.longitude == null) ||
          (t.latitude != null && (!Number.isFinite(t.latitude) || !Number.isFinite(t.longitude) || Math.abs(t.latitude) > 90 || Math.abs(t.longitude) > 180))) {
        throw new Error(name + ' · Controleer de coördinaten.');
      }
      if (t.aliases != null && (!Array.isArray(t.aliases) || t.aliases.some(alias => typeof alias !== 'string' || !alias.trim()))) throw new Error(name + ' · Controleer de aliassen.');
      for (const alias of t.aliases ?? []) {
        const folded = alias.toLowerCase();
        if (aliases.has(folded)) throw new Error('Alias meer dan één keer gebruikt: ' + alias);
        aliases.add(folded);
      }
      for (const p of [t.profile, t.motoGPProfile, t.nascarEuroProfile].filter(p => p != null)) {
        for (const key of Object.keys(fields)) {
          if (key === 'turns') continue;
          if ((p[key] != null || !optional.has(key)) && (typeof p[key] !== 'string' || !p[key].trim())) throw new Error(name + ' · Controleer ' + fields[key] + '.');
        }
        if (p.firstFormula1GrandPrix != null && !/^[0-9]{4}$/.test(p.firstFormula1GrandPrix)) {
          throw new Error(name + ' · Vul het jaar van de eerste F1-Grand Prix in.');
        }
        if (!Number.isInteger(p.turns) || p.turns < 1 || p.turns > 200) throw new Error(name + ' · Vul het aantal bochten in.');
        if (p.bankingDegrees != null && (!Number.isFinite(p.bankingDegrees) || p.bankingDegrees < 0 || p.bankingDegrees > 90)) {
          throw new Error(name + ' · Vul een banking tussen 0 en 90 graden in.');
        }
        try { new Intl.DateTimeFormat('nl', { timeZone: p.timeZoneIdentifier }); }
        catch { throw new Error(name + ' · Gebruik een geldige IANA-tijdzone.'); }
        for (const rating of [p.tyreStress, p.overtakingDifficulty]) {
          if (!rating || typeof rating.label !== 'string' || !rating.label.trim() || !Number.isFinite(rating.value) || rating.value < 0 || rating.value > 1) {
            throw new Error(name + ' · Vul het label en de score voor beide meldingen in.');
          }
        }
        if (p.geometry && (!checkGeometry(p.geometry) || (p.geometry.turnLabels.length && p.geometry.turnLabels.length !== p.turns))) {
          throw new Error(name + ' · Controleer de vectorpunten, sectorgrenzen en bochtnummers.');
        }
      }
    }
    for (const name of names) if (aliases.has(name.toLowerCase())) throw new Error('Alias komt overeen met een aparte baan: ' + name);
    const byId = new Map(data.tracks.map(track => [track.id, track]));
    for (const track of data.tracks) {
      if (track.layoutGroup != null) {
        const parent = byId.get(track.layoutGroup);
        if (typeof track.layoutGroup !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(track.layoutGroup) ||
            parent?.layoutGroup != null || (!partial && !parent)) {
          throw new Error(track.circuitName + ' · Controleer de hoofdbaan.');
        }
      }
      if (track.formulaELayoutID != null) {
        const layout = byId.get(track.formulaELayoutID);
        if (typeof track.formulaELayoutID !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(track.formulaELayoutID) ||
            (layout ? layout.layoutGroup !== track.id : !partial)) {
          throw new Error(track.circuitName + ' · Controleer de Formula E-layout.');
        }
      }
      if (track.motoGPLayoutID != null) {
        const layout = byId.get(track.motoGPLayoutID);
        if (typeof track.motoGPLayoutID !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(track.motoGPLayoutID) ||
            (layout ? layout.layoutGroup !== track.id : !partial)) {
          throw new Error(track.circuitName + ' · Controleer de MotoGP-layout.');
        }
      }
    }
    return data;
  }

  function store() {
    try { localStorage.setItem(storageKey(), JSON.stringify(draft)); }
    catch { showStatus('Lokaal opslaan is mislukt. Exporteer tracks.json om je wijzigingen te bewaren.', 'error'); }
  }

  async function requestPublished() {
    const github = connection();
    if (!github) return null;
    const response = await fetch(github.url + '?ref=' + encodeURIComponent(github.branch), {headers: github.headers});
    if (response.status === 404) return null;
    if (!response.ok) throw new Error('GitHub antwoordde met status ' + response.status + '.');
    const file = await response.json();
    return { data: validate(JSON.parse(decoded(file.content))), sha: file.sha };
  }

  async function load(useDraft = true) {
    const key = storageKey();
    if (useDraft) {
      try {
        const saved = JSON.parse(localStorage.getItem(key));
        if (saved) return {...saved, data: validate(saved.data), key};
      } catch {}
    }
    const remote = await requestPublished();
    if (remote) return {...remote, key};
    const response = await fetch('tracks.json');
    if (!response.ok) throw new Error('Kon tracks.json niet laden.');
    return {data: validate(await response.json()), sha: null, key};
  }

  function open() {
    state.activeSeries = null;
    state.activeView = 'tracks';
    renderSidebar();
    renderMain();
    if (window.innerWidth <= 760 && document.querySelector('.sidebar').classList.contains('open')) toggleSidebar();
  }

  const preview = (geometry, orientation) => {
    if (!checkGeometry(geometry)) return '';
    const angle = orientation * Math.PI / 180;
    const transform = ([x, y]) => [x * Math.cos(angle) - y * Math.sin(angle), x * Math.sin(angle) + y * Math.cos(angle)];
    const points = geometry.points.map(transform);
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    const minX = Math.min(...xs), minY = Math.min(...ys);
    const bounds = [minX - 16, minY - 16, Math.max(...xs) - minX + 32, Math.max(...ys) - minY + 32];
    return `<svg class="track-preview" viewBox="${bounds.join(' ')}" role="img" aria-label="Baanvorm in gekozen oriëntatie">
      <polyline points="${points.map(p => p.join(',')).join(' ')}" fill="none" stroke="currentColor" stroke-width="7" stroke-linejoin="round"/>
    </svg>`;
  };

  const input = (name, label, value, type = 'text', extra = '') =>
    `<label class="field"><span>${esc(label)}</span><input name="${name}" value="${esc(value)}" type="${type}" ${extra}></label>`;

  function showTrack(id) {
    activeId = id;
    renderTracks();
    renderForm();
  }

  function renderForm() {
    const container = document.getElementById('trackLibraryForm');
    if (!container) return;
    const track = draft?.data.tracks.find(t => t.id === activeId);
    if (!track) {
      container.innerHTML = '<p class="muted">Selecteer een baan of voeg er een toe.</p>';
      return;
    }
    const parent = track.layoutGroup ? draft.data.tracks.find(t => t.id === track.layoutGroup) : track;
    const variants = draft.data.tracks.filter(t => t.layoutGroup === parent.id);
    const p = track.profile || {};
    const rating = (key, label) =>
      input(key + 'Label', label, p[key]?.label) + input(key + 'Value', label + ' · score (0–1)', p[key]?.value, 'number', 'min="0" max="1" step=".01"');
    const profileField = ([key, label]) => {
      if (key === 'circuitType') {
        return input(key, label, p[key], 'text', 'list="trackCircuitTypes"') +
          '<datalist id="trackCircuitTypes"><option value="Permanent circuit"><option value="Semi-permanent circuit"><option value="Temporary circuit"><option value="Street circuit"><option value="Oval"></datalist>';
      }
      if (key === 'elevationChange') {
        return `<div id="trackElevationField">${input(key, label, p[key])}</div>
          <div id="trackBankingField">${input('bankingDegrees', 'Maximale banking (°)', p.bankingDegrees, 'number', 'min="0" max="90" step="any"')}</div>`;
      }
      return input(key, label, p[key], key === 'turns' ? 'number' : 'text', key === 'turns' ? 'min="1" max="200"' : '');
    };
    container.innerHTML = `<form class="config-card track-form" id="trackForm">
      <div class="track-detail-header">
      <h2>${esc(parent.circuitName)}</h2>
      ${variants.length ? `<label class="field track-layout-picker"><span>Lay-out</span>
        <select id="trackLayout" name="layout">
          ${[parent, ...variants].map(t => `<option value="${esc(t.id)}"${t.id === track.id ? ' selected' : ''}>${esc(t.circuitName)}</option>`).join('')}
        </select>
      </label>` : ''}
      </div>
      <div id="trackPreview">${preview(p.geometry, track.orientationAngle)}</div>
      <div class="track-fields">
        ${input('circuitName', 'Circuitnaam', track.circuitName, 'text', 'required')}
        ${input('id', 'Track-ID', track.id, 'text', 'pattern="[a-z0-9]+(-[a-z0-9]+)*" required')}
        ${input('latitude', 'Breedtegraad', track.latitude, 'number', 'step="any" min="-90" max="90"')}
        ${input('longitude', 'Lengtegraad', track.longitude, 'number', 'step="any" min="-180" max="180"')}
        ${input('orientationAngle', 'Oriëntatiehoek (°)', track.orientationAngle, 'number', 'min="-180" max="180" step="any" required')}
        ${input('aliases', 'Aliassen (gescheiden door komma’s)', track.aliases?.join(', '))}
      </div>
      <label class="track-check"><input type="checkbox" name="showProfile" ${track.profile ? 'checked' : ''}> Circuitgegevens tonen in de app</label>
      <div id="trackProfileFields" ${track.profile ? '' : 'hidden'}>
        <div class="track-fields">
          ${Object.entries(fields).map(profileField).join('')}
          ${rating('tyreStress', 'Bandendegradatie')}${rating('overtakingDifficulty', 'Inhalen')}
        </div>
        <label class="field track-geometry">Baangeometrie (JSON)
          <textarea name="geometry" rows="6" spellcheck="false" placeholder='{"width": 290, "height": 290, "points": [], "sectorEnds": [], "turnLabels": []}'>${p.geometry ? esc(JSON.stringify(p.geometry, null, 2)) : ''}</textarea>
        </label>
      </div>
      <button class="btn btn-primary" type="submit">Baan opslaan</button>
    </form>`;
    const form = document.getElementById('trackForm');
    const layout = document.getElementById('trackLayout');
    const formSnapshot = () => JSON.stringify([...new FormData(form)].filter(([key]) => key !== 'layout'));
    let savedForm;
    if (layout) layout.onchange = () => {
      if (formSnapshot() !== savedForm && !window.confirm('Je hebt niet-opgeslagen wijzigingen. Wil je van lay-out wisselen en deze wijzigingen weggooien?')) {
        layout.value = track.id;
        return;
      }
      showTrack(layout.value);
      document.getElementById('trackLayout')?.focus();
    };
    const updateOvalFields = () => {
      const oval = isOval(form.elements.circuitType.value);
      document.getElementById('trackElevationField').hidden = oval;
      document.getElementById('trackBankingField').hidden = !oval;
      form.elements.elevationChange.disabled = oval;
      form.elements.bankingDegrees.disabled = !oval;
    };
    form.elements.circuitType.oninput = updateOvalFields;
    updateOvalFields();
    savedForm = formSnapshot();
    form.elements.showProfile.onchange = () => {
      document.getElementById('trackProfileFields').hidden = !form.elements.showProfile.checked;
    };
    form.elements.orientationAngle.oninput = () => {
      document.getElementById('trackPreview').innerHTML = preview(p.geometry, form.elements.orientationAngle.valueAsNumber || 0);
    };
    form.onsubmit = event => {
      event.preventDefault();
      try {
        const values = new FormData(form);
        const field = key => String(values.get(key) ?? '').trim();
        const edited = {id: field('id'), circuitName: field('circuitName'), orientationAngle: Number(field('orientationAngle'))};
        if (track.layoutGroup) edited.layoutGroup = track.layoutGroup;
        if (track.motoGPLayoutID) edited.motoGPLayoutID = track.motoGPLayoutID;
        if (track.formulaELayoutID) edited.formulaELayoutID = track.formulaELayoutID;
        if (track.motoGPProfile) edited.motoGPProfile = track.motoGPProfile;
        if (track.nascarEuroProfile) edited.nascarEuroProfile = track.nascarEuroProfile;
        if (field('aliases')) edited.aliases = field('aliases').split(',').map(x => x.trim()).filter(Boolean);
        for (const key of ['latitude', 'longitude']) if (field(key)) edited[key] = Number(field(key));
        if (values.has('showProfile')) {
          const profile = {};
          for (const key of Object.keys(fields)) {
            if (!optional.has(key) || field(key)) profile[key] = key === 'turns' ? Number(field(key)) : field(key);
          }
          if (isOval(profile.circuitType)) {
            profile.circuitType = 'Oval';
            if (field('bankingDegrees')) profile.bankingDegrees = Number(field('bankingDegrees'));
          }
          for (const key of ['tyreStress', 'overtakingDifficulty']) {
            profile[key] = {label: field(key + 'Label'), value: field(key + 'Value') ? Number(field(key + 'Value')) : NaN};
          }
          if (field('geometry')) profile.geometry = JSON.parse(field('geometry'));
          edited.profile = profile;
        }
        draft.data = validate({...draft.data, tracks: [
          ...draft.data.tracks.filter(t => t.id !== activeId).map(t => {
            if (activeId === edited.id) return t;
            if (t.layoutGroup === activeId) return {...t, layoutGroup: edited.id};
            if (t.formulaELayoutID === activeId) return {...t, formulaELayoutID: edited.id};
            if (t.motoGPLayoutID === activeId) return {...t, motoGPLayoutID: edited.id};
            return t;
          }), edited
        ]});
        activeId = edited.id;
        store();
        render();
        showStatus('Baan lokaal opgeslagen.', 'success');
      } catch (error) { showStatus(error.message, 'error'); }
    };
  }

  function renderTracks() {
    const list = document.getElementById('trackLibraryList');
    const searchKey = name => name.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
    const term = searchKey(query.trim());
    const matches = new Set(draft.data.tracks.filter(t =>
      [t.circuitName, ...(t.aliases ?? [])].some(name => searchKey(name).includes(term))
    ).map(t => t.layoutGroup ?? t.id));
    const selected = draft.data.tracks.find(t => t.id === activeId);
    const selectedGroup = selected?.layoutGroup ?? activeId;
    const tracks = draft.data.tracks.filter(t =>
      !t.layoutGroup && matches.has(t.id)
    ).sort((a, b) => a.circuitName.localeCompare(b.circuitName, 'nl'));
    if (!tracks.length) {
      list.innerHTML = '<p>Geen banen gevonden.</p>';
      return;
    }
    list.innerHTML = tracks.map(t => `<button type="button" class="series-item${t.id === selectedGroup ? ' active" aria-current="true' : ''}" data-track="${esc(t.id)}">${esc(t.circuitName)}</button>`).join('');
    list.onclick = event => {
      const id = event.target.closest('[data-track]')?.dataset.track;
      if (!id) return;
      showTrack(id);
    };
  }

  function render() {
    const main = document.getElementById('mainContent');
    const title = document.getElementById('workspaceTitle');
    title.textContent = 'Tracks';
    document.getElementById('workspaceIcon').innerHTML = document.querySelector('#tracksNav svg').outerHTML;
    if (draft?.key !== storageKey()) draft = null;
    main.innerHTML = `<div class="track-library">
      <div class="track-actions">
        ${draft ? '<button type="button" class="btn btn-primary" id="addTrack">Baan toevoegen</button><button type="button" class="btn btn-ghost" id="exportTracks">Exporteer tracks.json</button>' : ''}
        <label class="btn btn-ghost">Importeer JSON<input type="file" accept=".json,application/json" id="importTracks" class="track-file"></label>
        <button type="button" class="btn btn-ghost" id="reloadTracks">Herlaad bibliotheek</button>
      </div>
      <div id="trackLibraryStatus" role="status"></div>
      <details class="config-card" style="margin-bottom: 16px">
        <summary>Welke informatie heeft Tracks nodig?</summary>
        <p>Per baan zijn een unieke Track-ID, de circuitnaam uit de kalender en de oriëntatiehoek nodig. Gebruik 0° voor de aangeleverde oriëntatie en −90° voor Lusail. Voeg alternatieve kalendernamen toe als aliassen.</p>
        <p>Coördinaten zijn optioneel en worden gebruikt voor het weer. Voor een detailsheet voeg je lengte, bochten, rijrichting, eerste race, circuittype, ronderecord, tijdzone en de twee beoordelingen toe. Voor de kaart kies je vectorgeometrie met sectorgrenzen en bochtposities, of de naam van een meegeleverd app-asset.</p>
        <p>Importeer een geëxporteerde tracks.json, een lijst met banen of één baanobject. Bij een bestaande bibliotheek worden banen op Track-ID bijgewerkt. Een wedstrijdvariant kan met <code>layoutGroup</code> verwijzen naar de Track-ID van de hoofdbaan.</p>
      </details>
      ${draft ? `<div class="track-layout"><aside class="config-card"><label class="field">Zoek baan<input type="search" id="trackSearch" value="${esc(query)}"></label><div id="trackLibraryList"></div></aside><div id="trackLibraryForm"></div></div>` : ''}
    </div>`;
    document.getElementById('importTracks').onchange = async event => {
      const file = event.target.files[0];
      if (!file) return;
      try {
        const payload = JSON.parse(await file.text());
        const fullLibrary = payload?.schemaVersion != null;
        if (!draft && !fullLibrary) throw new Error('Laad eerst de trackbibliotheek of importeer een volledige tracks.json.');
        const incoming = fullLibrary ? validate(payload) :
          validate({schemaVersion: 1, tracks: Array.isArray(payload) ? payload : [payload]}, {partial: true});
        const existing = new Map((draft?.data.tracks ?? []).map(track => [track.id, track]));
        const updates = incoming.tracks.filter(track => existing.has(track.id)).length;
        if (updates && !confirm(`${updates} bestaande banen met dezelfde Track-ID bijwerken?`)) return;
        for (const track of incoming.tracks) existing.set(track.id, {...existing.get(track.id), ...track});
        const data = validate({...incoming, ...draft?.data, tracks: [...existing.values()]});
        loading = null;
        draft = {data, sha: draft?.sha ?? null, key: storageKey()};
        activeId = incoming.tracks[0].id;
        query = '';
        store();
        render();
        showStatus(`${incoming.tracks.length} banen geïmporteerd (${updates} bijgewerkt).`, 'success');
      } catch (error) { showStatus(error.message, 'error'); }
    };
    document.getElementById('reloadTracks').onclick = () => {
      if (draft && !confirm('De bibliotheek opnieuw laden en de lokale wijzigingen vervangen?')) return;
      startLoad(false);
    };
    if (!draft) { if (!loading || loadingKey !== storageKey()) startLoad(); return; }
    if (!draft.data.tracks.some(t => t.id === activeId)) activeId = draft.data.tracks[0]?.id;
    document.getElementById('trackSearch').oninput = event => { query = event.target.value; renderTracks(); };
    document.getElementById('addTrack').onclick = () => {
      let number = 1;
      while (draft.data.tracks.some(t => t.id === 'nieuwe-baan-' + number || t.circuitName === 'Nieuwe baan ' + number)) number++;
      activeId = 'nieuwe-baan-' + number;
      draft.data.tracks.push({id: activeId, circuitName: 'Nieuwe baan ' + number, orientationAngle: 0});
      query = '';
      store();
      render();
      document.querySelector('#trackForm [name="circuitName"]')?.select();
    };
    document.getElementById('exportTracks').onclick = () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(draft.data, null, 2) + '\n'], {type: 'application/json'}));
      const link = Object.assign(document.createElement('a'), {href: url, download: 'tracks.json'});
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    renderTracks();
    renderForm();
  }

  function startLoad(useDraft = true) {
    const status = document.getElementById('trackLibraryStatus');
    if (status) status.textContent = 'Trackbibliotheek laden…';
    const key = storageKey();
    const request = load(useDraft);
    loading = request;
    loadingKey = key;
    request.then(loaded => {
      if (loading !== request || key !== storageKey()) return;
      draft = loaded;
      store();
      if (state.activeView === 'tracks') render();
    }).catch(() => {
      if (loading !== request || key !== storageKey()) return;
      const target = document.getElementById('trackLibraryStatus');
      if (target) target.textContent = 'Importeer de volledige tracks.json of verbind de editor met GitHub om de bibliotheek te laden.';
    }).finally(() => { if (loading === request) loading = null; });
  }

  async function publish() {
    if (publishing) return;
    const github = connection();
    if (!github || !draft || draft.key !== storageKey()) return showStatus('Laad de bibliotheek en koppel GitHub.', 'error');
    const snapshot = draft;
    publishing = true;
    try {
      const data = JSON.stringify(validate(snapshot.data), null, 2) + '\n';
      const response = await fetch(github.url, {method: 'PUT', headers: github.headers, body: JSON.stringify({
        branch: github.branch, message: 'Update track library', content: encoded(data),
        ...(snapshot.sha ? {sha: snapshot.sha} : {})
      })});
      if (response.status === 409 || response.status === 422) throw new Error('De gepubliceerde versie is gewijzigd. Exporteer je concept en herlaad de bibliotheek.');
      if (!response.ok) throw new Error('GitHub antwoordde met status ' + response.status + '.');
      snapshot.sha = (await response.json()).content.sha;
      if (draft === snapshot && snapshot.key === storageKey()) store();
      showStatus('Trackbibliotheek gepubliceerd.', 'success');
    } catch (error) { showStatus(error.message, 'error'); }
    finally { publishing = false; }
  }

  return { open, render, publish };
})();
