'use strict';

const $ = id => document.getElementById(id);
let token = location.hash.slice(1) || sessionStorage.getItem('raptorCore3Token') || '';
if (token) {
  sessionStorage.setItem('raptorCore3Token', token);
  history.replaceState(null, '', location.pathname);
}

let config = null;
let state = null;
let selected = 0;
let dirty = false;
let toastTimer = null;

async function api(path, data) {
  const response = await fetch('/api/' + path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {
      'X-Aurora-Token': token,
      'Content-Type': 'application/json'
    },
    body: data === undefined ? undefined : JSON.stringify(data)
  });
  const json = await response.json();
  if (!response.ok) throw Error(json.error || response.statusText);
  return json;
}

function toast(message) {
  $('toast').textContent = String(message);
  $('toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('show'), 3500);
}

function handle(fn) {
  return async (...args) => {
    try { await fn(...args); }
    catch (error) { toast(error.message || error); }
  };
}

function mark() {
  dirty = true;
  $('save').textContent = 'Enregistrer *';
}

function clean() {
  dirty = false;
  $('save').textContent = 'Enregistrer';
}

function target() {
  return config?.targets?.[selected] || null;
}

function currentDevice() {
  const t = target();
  return state?.devices?.find(d => d.id === t?.device) || null;
}

function setSettings() {
  const s = config.settings;
  $('idle').value = Math.round((s.idle_seconds || 0) / 60);
  $('fps').value = String(s.fps || 12);
  $('startup').checked = !!s.startup;
  $('autosync').checked = !!s.auto_sync;
  $('updates').checked = s.check_updates !== false;
}

function sourceLabel(source) {
  return ({
    icue: 'iCUE',
    wled_preset: 'Preset WLED',
    rainbow: 'Arc-en-ciel',
    solid: 'Couleur fixe'
  })[source] || source;
}

function renderCards() {
  const items = config.targets || [];
  $('targets').innerHTML = items.length ? items.map((t, i) => {
    const live = state?.targets?.[t.ip] || 'À l’arrêt';
    return '<div class="card ' + (i === selected ? 'selected' : '') + '" data-target="' + i + '">' +
      '<div class="card-top"><h3>' + escapeHtml(t.name) + '</h3>' +
      '<input class="enabled" data-index="' + i + '" type="checkbox" ' + (t.enabled ? 'checked' : '') + '></div>' +
      '<p>' + escapeHtml(t.ip) + ' · ' + t.count + ' LED · ' + sourceLabel(t.source) + '</p>' +
      '<div class="state">' + escapeHtml(live) + '</div></div>';
  }).join('') : '<p class="muted">Ajoutez votre premier WLED.</p>';
  $('editor').classList.toggle('hidden', !items.length);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
}

function ensureIdleOption(value) {
  const select = $('target-idle');
  const str = value === null ? 'global' : String(value);
  if (![...select.options].some(o => o.value === str)) {
    select.add(new Option(Math.round(Number(value) / 60) + ' min', str));
  }
  select.value = str;
}

function renderEditor() {
  const t = target();
  if (!t) return;
  $('editor-title').textContent = t.name;
  $('name').value = t.name;
  $('ip').value = t.ip;
  $('count').value = t.count;
  $('brightness').value = t.brightness;
  $('brightness-value').textContent = t.brightness + ' %';
  ensureIdleOption(t.idle_seconds);
  $('on-stop').value = t.on_stop;
  $('stop-preset').value = t.stop_preset;
  $('port').value = t.port;
  $('source').value = t.source;
  $('source-preset').value = t.source_preset;
  $('color').value = '#' + t.color.map(v => Number(v).toString(16).padStart(2, '0')).join('');
  $('mapping').value = t.mapping;
  $('reverse').checked = !!t.reverse;
  $('stop-preset-row').classList.toggle('hidden', t.on_stop !== 'preset');
  $('preset-row').classList.toggle('hidden', t.source !== 'wled_preset');
  $('color-row').classList.toggle('hidden', t.source !== 'solid');
  $('icue-box').classList.toggle('hidden', t.source !== 'icue');
  renderDevices();
  renderSample();
}

function renderDevices() {
  const t = target();
  if (!t) return;
  const devices = state?.devices || [];
  $('device').innerHTML = '<option value="">Choisir un appareil…</option>' +
    devices.map(d => '<option value="' + escapeHtml(d.id) + '">' +
      escapeHtml(d.model || d.id) + ' · ' + (d.positions?.length || 0) + ' LED</option>').join('');
  if (t.device && !devices.some(d => d.id === t.device)) {
    $('device').insertAdjacentHTML('beforeend',
      '<option value="' + escapeHtml(t.device) + '">Appareil enregistré absent</option>');
  }
  $('device').value = t.device || '';
  renderGroups();
}

function renderGroups() {
  const device = currentDevice();
  const groups = new Map();
  for (const p of device?.positions || []) {
    const key = Number(p.group || 0);
    groups.set(key, (groups.get(key) || 0) + 1);
  }
  $('group').innerHTML = [...groups.entries()].map(([group, count]) =>
    '<option value="' + group + '">Groupe ' + group + ' · ' + count + ' LED</option>'
  ).join('') || '<option value="">Aucun groupe</option>';
  $('selection').textContent = (target()?.ids?.length || 0) + ' LED iCUE sélectionnée(s)';
}

function renderSample() {
  const t = target();
  const worker = t && state?.wled_workers?.[t.ip];
  const sample = worker?.frame_sample || [];
  $('frame-sample').innerHTML = sample.map(item => {
    const rgb = item.rgb || [0, 0, 0];
    return '<span><i style="background:rgb(' + rgb.join(',') + ')"></i>LED ' +
      item.led + ' · ' + rgb.join(',') + '</span>';
  }).join('');
  if (t) $('target-state').textContent = state?.targets?.[t.ip] || 'À l’arrêt';
}

function addTarget(found = {}) {
  config.targets.push({
    name: found.name || 'Nouvel éclairage',
    ip: found.ip || '192.168.1.',
    count: found.count || 60,
    port: 21324,
    enabled: true,
    brightness: 100,
    idle_seconds: null,
    on_stop: 'off',
    stop_preset: 1,
    source: 'icue',
    source_preset: 1,
    device: '',
    device_model: '',
    device_serial: '',
    ids: [],
    mapping: 'stretch',
    reverse: false,
    color: [255, 100, 20]
  });
  selected = config.targets.length - 1;
  mark();
  renderCards();
  renderEditor();
}

async function save() {
  config = await api('config', config);
  clean();
  setSettings();
  renderCards();
  renderEditor();
  toast('Configuration enregistrée sans couper la synchronisation.');
}

function bindSettings() {
  $('idle').oninput = () => {
    config.settings.idle_seconds = Math.max(0, Math.round(Number($('idle').value) * 60));
    mark();
  };
  $('fps').onchange = () => { config.settings.fps = Number($('fps').value); mark(); };
  $('startup').onchange = () => { config.settings.startup = $('startup').checked; mark(); };
  $('autosync').onchange = () => { config.settings.auto_sync = $('autosync').checked; mark(); };
  $('updates').onchange = () => { config.settings.check_updates = $('updates').checked; mark(); };
  $('awake').onchange = handle(async () => {
    await api('awake', { enabled: $('awake').checked });
  });
}

function bindEditor() {
  const set = (id, key, convert = v => v) => {
    $(id).onchange = () => {
      const t = target();
      if (!t) return;
      t[key] = convert($(id).value);
      mark();
      renderCards();
      renderEditor();
    };
  };

  set('name', 'name');
  set('ip', 'ip');
  set('count', 'count', Number);
  $('brightness').oninput = () => {
    const t = target();
    if (!t) return;
    t.brightness = Number($('brightness').value);
    $('brightness-value').textContent = t.brightness + ' %';
    mark();
  };
  $('target-idle').onchange = () => {
    const t = target();
    if (!t) return;
    t.idle_seconds = $('target-idle').value === 'global' ? null : Number($('target-idle').value);
    mark();
  };
  $('on-stop').onchange = () => {
    target().on_stop = $('on-stop').value;
    mark();
    renderEditor();
  };
  set('stop-preset', 'stop_preset', Number);
  set('port', 'port', Number);

  $('source').onchange = () => {
    const t = target();
    t.source = $('source').value;
    mark();
    renderCards();
    renderEditor();
  };
  set('source-preset', 'source_preset', Number);
  $('color').oninput = () => {
    const hex = $('color').value.slice(1);
    target().color = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16));
    mark();
  };
  set('mapping', 'mapping');
  $('reverse').onchange = () => {
    target().reverse = $('reverse').checked;
    mark();
  };

  $('device').onchange = () => {
    const t = target();
    const device = state?.devices?.find(d => d.id === $('device').value);
    t.device = $('device').value;
    t.device_model = device?.model || '';
    t.device_serial = device?.serial || '';
    t.ids = [];
    mark();
    renderGroups();
  };

  $('use-group').onclick = () => {
    const t = target();
    const device = currentDevice();
    if (!t || !device) return;
    const group = Number($('group').value);
    t.ids = (device.positions || []).filter(p => Number(p.group || 0) === group).map(p => Number(p.id));
    mark();
    renderGroups();
  };

  $('use-all').onclick = () => {
    const t = target();
    const device = currentDevice();
    if (!t || !device) return;
    t.ids = (device.positions || []).map(p => Number(p.id));
    mark();
    renderGroups();
  };

  $('clear-ids').onclick = () => {
    if (!target()) return;
    target().ids = [];
    mark();
    renderGroups();
  };

  $('refresh-icue').onclick = handle(async () => {
    await api('refresh', {});
    toast('Actualisation iCUE demandée.');
  });

  $('probe').onclick = handle(async () => {
    const t = target();
    const result = await api('probe', { ip: $('ip').value.trim() });
    t.ip = $('ip').value.trim();
    t.name = result.name || t.name;
    t.count = Number(result.count || t.count);
    mark();
    renderCards();
    renderEditor();
    toast('WLED détecté · ' + result.version);
  });

  $('open-wled').onclick = handle(async () => {
    await api('open-wled', { ip: target().ip });
  });

  $('test').onclick = handle(async () => {
    if (dirty) await save();
    await api('test', { ip: target().ip });
    toast('Test couleurs lancé.');
  });

  $('delete').onclick = () => {
    const t = target();
    if (!t || !confirm('Supprimer « ' + t.name + ' » ?')) return;
    config.targets.splice(selected, 1);
    selected = Math.max(0, Math.min(selected, config.targets.length - 1));
    mark();
    renderCards();
    renderEditor();
  };
}

async function poll() {
  try {
    const previousRevision = state?.revision;
    state = await api('state');

    if (previousRevision !== undefined && state.revision !== previousRevision && !dirty) {
      config = await api('config');
      selected = Math.max(0, Math.min(selected, config.targets.length - 1));
      setSettings();
      renderEditor();
    }

    $('status').textContent = state.status;
    $('toggle').textContent = (state.running || state.requested) ? '■ Arrêter' : '▶ Démarrer';
    $('idle-now').textContent = state.idle + ' s';
    $('awake').checked = !!state.keep_awake;
    $('engine-restarts').textContent = state.engine_restarts || 0;
    $('icue-restarts').textContent = state.icue?.restarts || 0;
    $('logs').textContent = (state.logs || []).join('\n');
    $('version').textContent = state.app?.version || '0.5.0';

    const update = state.app?.update;
    $('update-box').classList.toggle('hidden', !update?.available);
    if (update?.available) {
      $('update-text').textContent = 'Version ' + update.version + ' disponible.';
    }

    renderCards();
    renderDevices();
    renderSample();
  } catch (error) {
    $('status').textContent = 'Application inaccessible · récupération en cours…';
  }
  setTimeout(poll, 650);
}

function bind() {
  $('save').onclick = handle(save);
  $('toggle').onclick = handle(async () => {
    if (state.running || state.requested) {
      await api('stop', {});
    } else {
      if (dirty) await save();
      await api('start', {});
    }
  });
  $('quit').onclick = handle(async () => {
    if (!confirm('Quitter complètement Raptor Link ?')) return;
    await api('quit', {});
  });
  $('add').onclick = () => addTarget();
  $('discover').onclick = handle(async () => {
    const list = await api('discover', {});
    if (!list.length) return toast('Aucun WLED détecté automatiquement.');
    const text = list.map((d, i) => (i + 1) + ' — ' + d.name + ' · ' + d.ip + ' · ' + d.count + ' LED').join('\n');
    const answer = prompt('WLED détectés :\n\n' + text + '\n\nNuméro à ajouter :', '1');
    const index = Number(answer) - 1;
    if (Number.isInteger(index) && list[index]) addTarget(list[index]);
  });
  $('targets').onclick = event => {
    const enabled = event.target.closest('.enabled');
    if (enabled) {
      config.targets[Number(enabled.dataset.index)].enabled = enabled.checked;
      mark();
      event.stopPropagation();
      return;
    }
    const card = event.target.closest('[data-target]');
    if (card) {
      selected = Number(card.dataset.target);
      renderCards();
      renderEditor();
    }
  };
  $('copy-diag').onclick = handle(async () => {
    const result = await api('diagnostic-report', { minutes: 30 });
    try {
      await navigator.clipboard.writeText(result.text || '');
    } catch {
      const area = document.createElement('textarea');
      area.value = result.text || '';
      document.body.append(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    toast('Diagnostic copié.');
  });
  $('open-update').onclick = handle(async () => {
    await api('open-update', {});
  });

  bindSettings();
  bindEditor();
}

(async () => {
  try {
    config = await api('config');
    state = await api('state');
    bind();
    setSettings();
    renderCards();
    renderEditor();
    await api('window-ready', {});
    poll();
  } catch (error) {
    $('status').textContent = error.message;
    toast(error.message);
  }
})();