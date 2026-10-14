'use strict';
/* Pianificatore Cammini di Santiago — rete di cammini da OpenStreetMap (Overpass),
   routing nel browser, quote Open-Meteo. Sito statico. */

const $ = s => document.querySelector(s);
const EP = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter', 'https://overpass.private.coffee/api/interpreter'];
const STEP_KM = 0.5;
const PALETTE = ['#d6408f', '#17468c', '#2e8b57', '#8e44ad', '#c0392b', '#16a085', '#c9a000', '#5d6d7e'];
const COLOR = new Map();   // un colore diverso per ogni cammino
const colorOf = id => { if (!COLOR.has(id)) COLOR.set(id, `hsl(${Math.floor((COLOR.size * 137.508) % 360)},68%,42%)`); return COLOR.get(id); };
const icon = (id, cls) => L.divIcon({ className: 'pin ' + cls, html: `<svg><use href="#${id}"/></svg>`, iconSize: [26, 26], iconAnchor: [13, 13] });

const S = {
  routes: [], chosen: new Set(),   // elenco OSM e selezione
  tops: [],                        // {id,name,ways} cammini caricati
  pref: {},                        // id -> 'p' (preferisci) | 'x' (evita)
  pts: [],                         // {lat,lon,name,role}
  samples: [], places: [], lodging: [],
  a: 0, b: 0, daily: 25, stages: [], sel: -1, pick: null, token: 0
};
let G = { nodes: [], adj: [], idx: new Map(), grid: new Map() };

/* ---------- utilità ---------- */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const status = t => { $('#status').textContent = t; };
function hav(p, q) {
  const R = 6371, r = Math.PI / 180;
  const dLat = (q.lat - p.lat) * r, dLon = (q.lon - p.lon) * r;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(p.lat * r) * Math.cos(q.lat * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
async function overpass(q, tries = 2) {
  const errs = [];
  for (let t = 0; t < tries; t++) {
    for (const ep of EP) {
      const host = new URL(ep).host, ctl = new AbortController(), to = setTimeout(() => ctl.abort(), 110000);
      try {
        const r = await fetch(ep, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'data=' + encodeURIComponent(q), signal: ctl.signal });
        clearTimeout(to);
        if (r.ok) {
          const j = await r.json();
          if (j.remark && /error|timed out|out of memory/i.test(j.remark) && !(j.elements && j.elements.length)) { errs.push(host + ': ' + j.remark); continue; }
          return j;
        }
        errs.push(host + ': HTTP ' + r.status);
      } catch (e) { clearTimeout(to); errs.push(host + ': ' + (e.name === 'AbortError' ? 'timeout' : e.message)); }
    }
    if (t < tries - 1) { status('Server OSM occupati, riprovo tra pochi secondi…'); await new Promise(r => setTimeout(r, 4000)); }
  }
  throw new Error('i server OSM non rispondono (' + errs.join('; ') + '). Riprova tra un minuto.');
}

/* ---------- mappa ---------- */
const map = L.map('map', { renderer: L.canvas({ tolerance: 8 }) }).setView([43.5, -2.5], 6);
const BASE = {
  'OpenStreetMap': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    { maxZoom: 19, referrerPolicy: 'origin', attribution: '© OpenStreetMap' }),
  'Grigio chiaro': L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    { maxZoom: 19, subdomains: 'abcd', attribution: '© OpenStreetMap © CARTO' }),
  'Topografica': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    { maxZoom: 17, subdomains: 'abc', attribution: '© OpenStreetMap, SRTM · © OpenTopoMap' })
};
let baseNow = BASE['OpenStreetMap'].addTo(map), tileOk = 0, tileErr = 0;
// se le tessere OSM non arrivano, passa allo sfondo di riserva
BASE['OpenStreetMap'].on('tileload', () => { tileOk++; });
BASE['OpenStreetMap'].on('tileerror', () => {
  tileErr++;
  if (!tileOk && tileErr >= 4 && map.hasLayer(BASE['OpenStreetMap'])) {
    map.removeLayer(BASE['OpenStreetMap']);
    baseNow = BASE['Grigio chiaro'].addTo(map);
  }
});
L.control.layers(BASE, null, { position: 'topright', collapsed: true }).addTo(map);
const gOver = L.layerGroup().addTo(map), gNet = L.layerGroup().addTo(map), gRoute = L.layerGroup().addTo(map), gStops = L.layerGroup().addTo(map),
      gLodging = L.layerGroup().addTo(map), gPts = L.layerGroup().addTo(map);

/* ---------- catalogo dei cammini per nome (data/catalogo.json, data/panoramica.json, data/cammini/<slug>.json) ----------
   All'apertura si scaricano solo il catalogo e la panoramica semplificata (pochi MB in tutto);
   i tracciati completi di un cammino si scaricano solo quando lo si sceglie. */
S.cat = [];              // [{slug, nome, fam, paesi, aree, km, n, ids, bbox}]
S.bySlug = new Map();
S.fam = 'santiago';      // famiglia mostrata nell'elenco
S.pano = new Map();      // slug -> linee semplificate [[ [lat,lon], ... ], ...]
S.over = new Map();      // slug -> layer sulla mappa
const fullCache = new Map();
const MIN_KM = 30;       // i percorsi più brevi si vedono solo spuntando "anche i brevi"

const catReady = (async () => {
  try {
    status('Carico l\'elenco dei cammini…');
    const [c, p] = await Promise.all([fetch('data/catalogo.json'), fetch('data/panoramica.json')]);
    if (!c.ok || !p.ok) throw new Error('catalogo non trovato');
    const cj = await c.json(), pj = await p.json();
    S.cat = cj.cammini; S.famiglie = cj.famiglie; S.catDate = cj.generated;
    S.cat.forEach(x => { S.bySlug.set(x.slug, x); colorOf(x.slug); });
    pj.cammini.forEach(x => S.pano.set(x.s, x.l.map(l => l.map(([lon, lat]) => [lat, lon]))));
    renderFamTabs(); renderPaesi(); renderRoutes(); drawOverview();
    status(`${S.cat.length} cammini (dati del ${S.catDate}). Tocca una linea o spunta nell'elenco.`);
    return true;
  } catch (e) { status('Elenco dei cammini non disponibile: ' + e.message); return false; }
})();

function renderFamTabs() {
  const box = $('#famTabs'); box.innerHTML = '';
  for (const f of S.famiglie) {
    const n = S.cat.filter(c => c.fam === f.key && c.km >= MIN_KM).length;
    if (!n) continue;   // famiglia ancora senza dati (es. appena aggiunta)
    const b = document.createElement('button');
    b.textContent = `${f.label} (${n})`; b.className = f.key === S.fam ? 'on' : '';
    b.onclick = () => { S.fam = f.key; listLimit = 80; renderFamTabs(); renderPaesi(); renderRoutes(); drawOverview(); };
    box.appendChild(b);
  }
}
function renderPaesi() {
  const sel = $('#fPaese'), cur = sel.value;
  const paesi = [...new Set(S.cat.filter(c => c.fam === S.fam).flatMap(c => c.paesi))].sort((a, b) => a.localeCompare(b));
  sel.innerHTML = '<option value="">Tutti i paesi</option>' + paesi.map(p => `<option${p === cur ? ' selected' : ''}>${esc(p)}</option>`).join('');
}
const plain = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\bcami(n|nh)o\b/g, 'camino');
function filteredRoutes() {
  const f = plain($('#filter').value.trim()), paese = $('#fPaese').value, brevi = $('#fBrevi').checked;
  return S.cat.filter(c => S.chosen.has(c.slug) || (
    (f ? true : c.fam === S.fam) && (brevi || c.km >= MIN_KM || f) && (!paese || c.paesi.includes(paese)) &&
    (!f || plain(c.nome).includes(f))));
}
let listLimit = 80;
function renderRoutes() {
  const ul = $('#routeList'); ul.innerHTML = '';
  const f = plain($('#filter').value.trim());
  const score = c => { if (!f) return 0; const n = plain(c.nome); return n.startsWith(f) ? 3 : new RegExp('\\b' + f.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b').test(n) ? 2 : 1; };
  const list = filteredRoutes().sort((a, b) => (S.chosen.has(b.slug) - S.chosen.has(a.slug)) || score(b) - score(a) || b.km - a.km);
  list.slice(0, listLimit).forEach(c => {
    const li = document.createElement('li');
    li.innerHTML = `<label><input type="checkbox" ${S.chosen.has(c.slug) ? 'checked' : ''}> <span style="color:${colorOf(c.slug)}">■</span> <b>${esc(c.nome)}</b>` +
      `<br><span class="small">${c.km} km · ${esc(c.paesi.join(', ') || '—')}${c.n > 1 ? ` · ${c.n} tratti OSM` : ''}</span>` +
      (c.approx ? `<br><span class="small" title="Tratto che su OpenStreetMap non esiste: calcolato su strade e sentieri passando dalle tappe ufficiali">⚠ tracciato approssimato: ${esc(c.approx.join(', '))}</span>` : '') + `</label>`;
    li.querySelector('input').onchange = e => toggle(c.slug, e.target.checked);
    li.onmouseenter = () => highlight(c.slug, true); li.onmouseleave = () => highlight(c.slug, false);
    ul.appendChild(li);
  });
  if (list.length > listLimit) {
    const li = document.createElement('li');
    li.innerHTML = `<button>Mostra altri (${list.length - listLimit})</button>`;
    li.querySelector('button').onclick = () => { listLimit += 200; renderRoutes(); };
    ul.appendChild(li);
  }
  if (!list.length) ul.innerHTML = '<li class="small">Nessun cammino con questi filtri.</li>';
  const ch = [...S.chosen].map(s => S.bySlug.get(s)).filter(Boolean);
  $('#chosenBox').innerHTML = ch.length ? `<b>Scelti:</b> ${ch.map(c => esc(c.nome)).join(' · ')} <span class="small">(${ch.reduce((a, c) => a + c.km, 0)} km di tracciati)</span>` : 'Nessun cammino scelto.';
}
function toggle(slug, on) {
  on ? S.chosen.add(slug) : S.chosen.delete(slug);
  restyleOver(); renderRoutes();
}
$('#filter').oninput = () => { listLimit = 80; renderRoutes(); drawOverview(); };
$('#fPaese').onchange = () => { listLimit = 80; renderRoutes(); drawOverview(); };
$('#fBrevi').onchange = () => { listLimit = 80; renderRoutes(); drawOverview(); };
$('#btnClearSel').onclick = () => { S.chosen.clear(); restyleOver(); renderRoutes(); };

/* ---------- panoramica sulla mappa (linee semplificate; tocca una linea per sceglierla) ---------- */
const overStyle = slug => S.chosen.has(slug) ? { color: colorOf(slug), weight: 6, opacity: 1 } : { color: colorOf(slug), weight: 2.5, opacity: .55 };
function restyleOver() { for (const [slug, l] of S.over) { l.setStyle(overStyle(slug)); if (S.chosen.has(slug)) l.bringToFront(); } }
function highlight(slug, on) { const l = S.over.get(slug); if (l) { l.setStyle(on ? { weight: 7, opacity: 1 } : overStyle(slug)); if (on) l.bringToFront(); } }
function drawOverview() {
  gOver.clearLayers(); S.over.clear();
  for (const c of filteredRoutes()) {
    const lines = S.pano.get(c.slug); if (!lines || !lines.length) continue;
    const l = L.polyline(lines, overStyle(c.slug)).addTo(gOver);
    l.bindTooltip(`${c.nome} · ${c.km} km`, { sticky: true });
    l.on('click', () => { toggle(c.slug, !S.chosen.has(c.slug)); status(`${c.nome}: ${S.chosen.has(c.slug) ? 'scelto' : 'tolto'} (${S.chosen.size} scelti)`); });
    S.over.set(c.slug, l);
  }
  restyleOver();
  if (!S.overFitted && S.over.size) { map.fitBounds(L.featureGroup([...S.over.values()]).getBounds()); S.overFitted = true; }
}

/* ---------- carico i cammini scelti (solo i loro file) ---------- */
async function fullOf(slug) {
  if (!fullCache.has(slug)) fullCache.set(slug, fetch('data/cammini/' + slug + '.json').then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }));
  return fullCache.get(slug);
}
$('#btnLoad').onclick = loadChosen;
async function loadChosen() {
  await catReady;
  const slugs = [...S.chosen].filter(s => S.bySlug.has(s));
  if (!slugs.length) { status('Scegli almeno un cammino.'); return; }
  S.tops = [];
  try {
    let done = 0;
    status(`Scarico ${slugs.length} cammini…`);
    const all = await Promise.all(slugs.map(async s => { const j = await fullOf(s); status(`Scarico i cammini ${++done}/${slugs.length}…`); return [s, j]; }));
    for (const [slug, j] of all) {
      const ways = [];
      for (const f of j.features || []) {
        const g = f.geometry; if (!g) continue;
        const lines = g.type === 'MultiLineString' ? g.coordinates : g.type === 'LineString' ? [g.coordinates] : [];
        for (const l of lines) { const w = l.map(([lon, lat]) => ({ lat, lon })); if (w.length > 1) ways.push(w); }
      }
      if (ways.length) S.tops.push({ id: slug, name: S.bySlug.get(slug).nome, ways });
    }
    if (!S.tops.length) throw new Error('nessun tracciato trovato');
    gOver.clearLayers(); S.over.clear();
    status('Costruisco la rete…'); await new Promise(r => setTimeout(r, 20));
    buildGraph(); renderPrefs(); drawNet();
    const km = G.adj.reduce((a, l) => a + l.reduce((s, e) => s + e.w, 0), 0) / 2;
    status(`Rete: ${S.tops.length} cammini, ~${km.toFixed(0)} km. Ora scegli partenza e arrivo.`);
    $('#s3').open = true; $('#s1').open = false;
    const pts = []; S.tops.forEach(t => t.ways.forEach(w => pts.push([w[0].lat, w[0].lon])));
    map.fitBounds(pts);
  } catch (e) { status('Errore nel caricare i cammini: ' + e.message); }
}
// per tornare a scegliere: riapre la panoramica
$('#s1').addEventListener('toggle', () => { if ($('#s1').open && !S.over.size && S.cat.length) drawOverview(); });

/* ---------- grafo ---------- */
const CELL = 0.002;
const cellKey = (lat, lon) => Math.floor(lat / CELL) + ',' + Math.floor(lon / CELL);
function nodeId(p) {
  const k = p.lat.toFixed(5) + ',' + p.lon.toFixed(5);
  let id = G.idx.get(k);
  if (id === undefined) {
    id = G.nodes.length; G.nodes.push({ lat: p.lat, lon: p.lon }); G.adj.push([]); G.idx.set(k, id);
    const ck = cellKey(p.lat, p.lon); let c = G.grid.get(ck); if (!c) G.grid.set(ck, c = []); c.push(id);
  }
  return id;
}
function addEdge(u, v, w, rel) {
  if (u === v || G.adj[u].some(e => e.v === v && e.rel === rel)) return;
  G.adj[u].push({ v, w, rel }); G.adj[v].push({ v: u, w, rel });
}
function buildGraph() {
  G = { nodes: [], adj: [], idx: new Map(), grid: new Map() };
  for (const t of S.tops) for (const w of t.ways) {
    let prev = -1;
    for (const p of w) { const id = nodeId(p); if (prev >= 0) addEdge(prev, id, hav(G.nodes[prev], G.nodes[id]), t.id); prev = id; }
  }
  // cuce i piccoli strappi (frequenti in OSM): dalla fine di un tratto al punto più vicino entro 500 m
  // che NON sia già raggiungibile da lì a piedi lungo la rete (altrimenti si cucirebbe il tratto con sé stesso)
  const GAP = 0.5;
  const local = u => {   // nodi raggiungibili da u entro ~1,2 km lungo la rete
    const dist = new Map([[u, 0]]), q = [u];
    while (q.length) {
      const x = q.shift(), dx = dist.get(x);
      for (const e of G.adj[x]) { const nd = dx + e.w; if (nd <= GAP * 2.4 && !(dist.get(e.v) <= nd)) { dist.set(e.v, nd); q.push(e.v); } }
    }
    return dist;
  };
  const ends = []; for (let u = 0; u < G.nodes.length; u++) if (G.adj[u].length === 1) ends.push(u);
  for (const u of ends) {
    const n = G.nodes[u], cx = Math.floor(n.lat / CELL), cy = Math.floor(n.lon / CELL), near = local(u);
    let best = -1, bd = GAP;
    for (let dx = -3; dx <= 3; dx++) for (let dy = -4; dy <= 4; dy++) {
      for (const v of G.grid.get((cx + dx) + ',' + (cy + dy)) || []) {
        if (near.has(v)) continue;
        const d = hav(n, G.nodes[v]); if (d < bd) { bd = d; best = v; }
      }
    }
    if (best >= 0) addEdge(u, best, bd * 2, 'gap');
  }
}
function nearestNode(lat, lon) {
  const cx = Math.floor(lat / CELL), cy = Math.floor(lon / CELL); let best = -1, bd = 1e9, found = -1;
  for (let r = 0; r <= 25; r++) {
    for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
      for (const v of G.grid.get((cx + dx) + ',' + (cy + dy)) || []) {
        const d = hav({ lat, lon }, G.nodes[v]); if (d < bd) { bd = d; best = v; }
      }
    }
    if (best >= 0 && found < 0) found = r;
    if (found >= 0 && r >= found + 1) break;
  }
  return best >= 0 ? { id: best, d: bd } : null;
}

/* ---------- routing (Dijkstra) ---------- */
class Heap {
  constructor() { this.a = []; }
  get size() { return this.a.length; }
  push(k, v) { const a = this.a; a.push([k, v]); let i = a.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (a[p][0] <= a[i][0]) break; [a[p], a[i]] = [a[i], a[p]]; i = p; } }
  pop() { const a = this.a, top = a[0], last = a.pop(); if (a.length) { a[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < a.length && a[l][0] < a[m][0]) m = l; if (r < a.length && a[r][0] < a[m][0]) m = r; if (m === i) break; [a[m], a[i]] = [a[i], a[m]]; i = m; } } return top; }
}
function factor(rel) { const p = S.pref[rel]; return p === 'x' ? null : p === 'p' ? 0.6 : 1; }
function shortest(src, dst) {
  const n = G.nodes.length, dist = new Float64Array(n).fill(Infinity), prev = new Int32Array(n).fill(-1), h = new Heap();
  dist[src] = 0; h.push(0, src);
  while (h.size) {
    const [d, u] = h.pop(); if (d > dist[u]) continue; if (u === dst) break;
    for (const e of G.adj[u]) { const f = factor(e.rel); if (f === null) continue; const nd = d + e.w * f; if (nd < dist[e.v]) { dist[e.v] = nd; prev[e.v] = u; h.push(nd, e.v); } }
  }
  if (!isFinite(dist[dst])) return null;
  const path = []; for (let u = dst; u !== -1; u = prev[u]) path.push(u);
  return path.reverse();
}

/* ---------- 2. preferenze ---------- */
function renderPrefs() {
  const ul = $('#prefList'); ul.innerHTML = '';
  S.tops.forEach((t, i) => {
    const li = document.createElement('li');
    li.innerHTML = `<span style="color:${colorOf(t.id)}">■</span> ${esc(t.name)}
      <select><option value="">neutro</option><option value="p" ${S.pref[t.id] === 'p' ? 'selected' : ''}>preferisci</option><option value="x" ${S.pref[t.id] === 'x' ? 'selected' : ''}>evita</option></select>`;
    li.querySelector('select').onchange = e => { if (e.target.value) S.pref[t.id] = e.target.value; else delete S.pref[t.id]; drawNet(); };
    ul.appendChild(li);
  });
}
$('#btnReroute').onclick = () => computeRoute();
function drawNet() {
  gNet.clearLayers();
  S.tops.forEach((t, i) => {
    const p = S.pref[t.id];
    t.ways.forEach(w => L.polyline(w.map(q => [q.lat, q.lon]), { color: p === 'x' ? '#999' : colorOf(t.id), weight: p === 'p' ? 4 : 2, opacity: p === 'x' ? .35 : .7, dashArray: p === 'x' ? '4 6' : null, interactive: false }).addTo(gNet));
  });
}

/* ---------- 3. punti dell'itinerario ---------- */
function addPoint(role, lat, lon, name) {
  const pt = { lat, lon, role, name: name || `${lat.toFixed(4)}, ${lon.toFixed(4)}` };
  if (role === 'start') { S.pts = S.pts.filter(p => p.role !== 'start'); S.pts.unshift(pt); }
  else if (role === 'end') { S.pts = S.pts.filter(p => p.role !== 'end'); S.pts.push(pt); }
  else { const e = S.pts.findIndex(p => p.role === 'end'); if (e >= 0) S.pts.splice(e, 0, pt); else S.pts.push(pt); }
  renderPts();
}
function renderPts() {
  const ul = $('#ptList'); ul.innerHTML = ''; gPts.clearLayers();
  S.pts.forEach((p, i) => {
    const lab = { start: '📍 Partenza', via: '➕ Via', end: '🏁 Arrivo' }[p.role];
    const li = document.createElement('li');
    li.innerHTML = `${lab}: ${esc(p.name)} <button title="Rimuovi">✕</button>`;
    li.querySelector('button').onclick = () => { S.pts.splice(i, 1); renderPts(); };
    ul.appendChild(li);
    L.marker([p.lat, p.lon], { icon: p.role === 'end' ? icon('shell', 'pin-end') : icon('arrow', p.role === 'start' ? 'pin-start' : 'pin-via') }).bindTooltip(lab + ' · ' + p.name).addTo(gPts);
  });
}
document.querySelectorAll('[data-add]').forEach(b => b.onclick = async () => {
  const q = $('#place').value.trim(); if (!q) { status('Scrivi il nome di una località.'); return; }
  status('Cerco “' + q + '”…');
  try {
    const r = await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q));
    const j = await r.json(); if (!j.length) { status('Località non trovata.'); return; }
    addPoint(b.dataset.add, +j[0].lat, +j[0].lon, q); map.setView([+j[0].lat, +j[0].lon], 10); status('Aggiunto: ' + j[0].display_name.slice(0, 60));
    $('#place').value = '';
  } catch (e) { status('Ricerca non riuscita: ' + e.message); }
});
document.querySelectorAll('[data-pick]').forEach(b => b.onclick = () => {
  S.pick = S.pick === b.dataset.pick ? null : b.dataset.pick;
  document.querySelectorAll('[data-pick]').forEach(x => x.classList.toggle('on', x.dataset.pick === S.pick));
  if (S.pick) status('Tocca la mappa.');
});
map.on('click', e => { if (!S.pick) return; addPoint(S.pick, e.latlng.lat, e.latlng.lng); if (S.pick !== 'via') { S.pick = null; document.querySelectorAll('[data-pick]').forEach(x => x.classList.remove('on')); } });
$('#btnClear').onclick = () => { S.pts = []; renderPts(); };
$('#btnFlip').onclick = () => {
  S.pts.reverse(); S.pts.forEach((p, i) => p.role = i === 0 ? 'start' : i === S.pts.length - 1 ? 'end' : 'via'); renderPts();
  if (S.samples.length) computeRoute();
};
$('#btnRoute').onclick = () => computeRoute();

/* ---------- calcolo del percorso ---------- */
async function computeRoute() {
  const tk = ++S.token;
  if (!G.nodes.length) { status('Carica prima la rete di cammini.'); return; }
  if (S.pts.length < 2 || S.pts[0].role !== 'start' || S.pts[S.pts.length - 1].role !== 'end') { status('Servono almeno partenza e arrivo.'); return; }
  const snaps = [];
  for (const p of S.pts) {
    const s = nearestNode(p.lat, p.lon);
    if (!s) { status(`“${p.name}” è lontano dalla rete (oltre 4 km).`); return; }
    snaps.push({ ...s, name: p.name });
  }
  let line = [];
  for (let i = 1; i < snaps.length; i++) {
    const path = shortest(snaps[i - 1].id, snaps[i].id);
    if (!path) { status(`Nessun collegamento tra “${snaps[i - 1].name}” e “${snaps[i].name}” (rete non connessa o cammini evitati).`); return; }
    const pts = path.map(id => G.nodes[id]); line = line.length ? line.concat(pts.slice(1)) : pts;
  }
  S.samples = resample(line); S.a = 0; S.b = S.samples.length - 1; S.places = []; S.lodging = []; S.sel = -1;
  map.fitBounds(S.samples.map(p => [p.lat, p.lon]));
  status(`Percorso di ${S.samples[S.b].d.toFixed(0)} km. Carico località e quote…`);
  recompute();
  try { await loadPOI(); } catch (e) { if (tk === S.token) status('Località/ostelli non disponibili: ' + e.message); }
  if (tk !== S.token) return; recompute();
  try { await loadElevation(tk); if (tk === S.token) status('Fatto.'); } catch (e) { if (tk === S.token) status('Quote non disponibili: ' + e.message); }
  if (tk === S.token) recompute();
}
function resample(line) {
  const out = [{ lat: line[0].lat, lon: line[0].lon, d: 0, ele: null }]; let d = 0, next = 0;
  for (let i = 1; i < line.length; i++) {
    const seg = hav(line[i - 1], line[i]);
    while (seg > 0 && d + seg >= next + STEP_KM) {
      next += STEP_KM; const t = (next - d) / seg;
      out.push({ lat: line[i - 1].lat + (line[i].lat - line[i - 1].lat) * t, lon: line[i - 1].lon + (line[i].lon - line[i - 1].lon) * t, d: next, ele: null });
    }
    d += seg;
  }
  const last = line[line.length - 1];
  if (d - out[out.length - 1].d > 0.05) out.push({ lat: last.lat, lon: last.lon, d, ele: null });
  return out;
}

/* ---------- località e alloggi ---------- */
// prima i file statici data/alloggi/<area>.json (generati dalla Action "Genera ostelli"), poi Overpass come riserva
const KIND_IT = { P: 'Albergue/accoglienza pellegrini', O: 'Ostello', R: 'Rifugio', B: 'Pensione/B&B', H: 'Hotel', C: 'Campeggio' };
let alloggiIndex;
const alloggiFiles = new Map();
async function loadPOIStatic() {
  if (alloggiIndex === undefined) {
    try { const r = await fetch('data/alloggi/indice.json'); alloggiIndex = r.ok ? await r.json() : null; } catch (e) { alloggiIndex = null; }
  }
  if (!alloggiIndex) return false;
  const keys = new Set(S.tops.flatMap(t => (S.bySlug.get(t.id) || { aree: [] }).aree));
  const files = alloggiIndex.aree.filter(a => keys.has(a.key));
  if (!files.length) return false;
  status('Carico località e ostelli…');
  const data = await Promise.all(files.map(a => {
    if (!alloggiFiles.has(a.file)) alloggiFiles.set(a.file, fetch('data/alloggi/' + a.file).then(r => r.ok ? r.json() : null).catch(() => null));
    return alloggiFiles.get(a.file);
  }));
  // griglia dei punti del percorso per trovare in fretta il più vicino
  const grid = new Map(), G = 0.05, gk = (la, lo) => Math.floor(la / G) + ',' + Math.floor(lo / G);
  S.samples.forEach((p, i) => { const k = gk(p.lat, p.lon); if (!grid.has(k)) grid.set(k, []); grid.get(k).push(i); });
  const nearest = (la, lo) => {
    let bi = -1, bd = 1e9; const a = Math.floor(la / G), b = Math.floor(lo / G);
    for (let x = a - 1; x <= a + 1; x++) for (let y = b - 1; y <= b + 1; y++) for (const i of grid.get(x + ',' + y) || []) {
      const d = hav({ lat: la, lon: lo }, S.samples[i]); if (d < bd) { bd = d; bi = i; } }
    return [bi, bd];
  };
  const seenP = new Set(), seenL = new Set(), places = [], lodging = [];
  for (const d of data) {
    if (!d) continue;
    for (const p of d.luoghi) {
      if (seenP.has(p.id)) continue; seenP.add(p.id);
      const [i, off] = nearest(p.la, p.lo); if (i < 0 || off > 2.5) continue;
      places.push({ name: p.n, type: p.t, lat: p.la, lon: p.lo, i, d: S.samples[i].d, off, cc: p.cc, region: p.re });
    }
    for (const h of d.alloggi) {
      if (seenL.has(h.id)) continue; seenL.add(h.id);
      const [i, off] = nearest(h.la, h.lo); if (i < 0 || off > 1.8) continue;
      lodging.push({ name: h.n || '(senza nome)', kind: KIND_IT[h.k] || h.k, k: h.k, lat: h.la, lon: h.lo, i, d: S.samples[i].d, off,
        tags: { phone: h.tel, website: h.web && !/^https?:/.test(h.web) ? 'https://' + h.web : h.web, beds: h.letti, fee: h.prezzo, opening_hours: h.orari } });
    }
  }
  S.places = places; S.lodging = lodging;
  return true;
}
async function loadPOI() {
  if (await loadPOIStatic()) return;
  const step = Math.max(2, Math.round(1 / STEP_KM));
  const pts = []; for (let i = 0; i < S.samples.length; i += step) pts.push(S.samples[i]);
  pts.push(S.samples[S.samples.length - 1]);
  const poly = pts.map(p => p.lat.toFixed(5) + ',' + p.lon.toFixed(5)).join(',');
  const q = `[out:json][timeout:180];
(node["place"~"^(city|town|village|hamlet)$"](around:2000,${poly}););
out tags center;
(nwr["tourism"~"^(hostel|guest_house|camp_site)$"](around:1500,${poly});
 nwr["tourism"]["name"~"lbergue|efugio|[Gg][iî]te|lberg|ilgrim"](around:1500,${poly}););
out tags center;`;
  status('Scarico località e ostelli da OSM (può richiedere un minuto)…');
  const j = await overpass(q);
  const cen = e => e.type === 'node' ? { lat: e.lat, lon: e.lon } : e.center;
  const nearest = p => { let bi = 0, bd = 1e9; for (let i = 0; i < S.samples.length; i++) { const d = Math.abs(S.samples[i].lat - p.lat) * 111 + Math.abs(S.samples[i].lon - p.lon) * 80; if (d < bd) { bd = d; bi = i; } } return bi; };
  const seen = new Set(), places = [], lodging = [];
  for (const e of j.elements) {
    const c = cen(e); if (!c || !e.tags) continue;
    const key = e.type + e.id; if (seen.has(key)) continue; seen.add(key);
    const i = nearest(c), off = hav(c, S.samples[i]);
    if (e.tags.place) { if (off <= 2.5 && e.tags.name) places.push({ name: e.tags.name, type: e.tags.place, lat: c.lat, lon: c.lon, i, d: S.samples[i].d, off }); }
    else if (off <= 1.8) lodging.push({ name: e.tags.name || '(senza nome)', kind: kindOf(e.tags), tags: e.tags, lat: c.lat, lon: c.lon, i, d: S.samples[i].d, off });
  }
  S.places = places; S.lodging = lodging;
}
function kindOf(t) {
  if (/lbergue|efugio|lberg|ilgrim|[Gg][iî]te/.test(t.name || '')) return 'Albergue/rifugio';
  return { hostel: 'Ostello', guest_house: 'Pensione', camp_site: 'Campeggio' }[t.tourism] || t.tourism;
}

/* ---------- quote (Open-Meteo) ---------- */
async function loadElevation(tk) {
  const pts = S.samples, N = 100, batches = []; for (let i = 0; i < pts.length; i += N) batches.push(i);
  let done = 0;
  const work = async () => {
    while (batches.length) {
      if (tk !== S.token) return;
      const i = batches.shift(), sl = pts.slice(i, i + N);
      const url = 'https://api.open-meteo.com/v1/elevation?latitude=' + sl.map(p => p.lat.toFixed(4)).join(',') + '&longitude=' + sl.map(p => p.lon.toFixed(4)).join(',');
      let ok = false;
      for (let t = 0; t < 3 && !ok; t++) {
        const r = await fetch(url);
        if (r.ok) { const j = await r.json(); j.elevation.forEach((e, k) => sl[k].ele = e); ok = true; }
        else await new Promise(r => setTimeout(r, 1500 * (t + 1)));
      }
      if (!ok) throw new Error('Open-Meteo non risponde');
      status(`Quote: ${Math.round(++done * N / pts.length * 100)}%`);
    }
  };
  await Promise.all([work(), work()]);
}

/* ---------- tappe ---------- */
function smooth(i) { const s = S.samples; let a = 0, n = 0; for (let k = i - 1; k <= i + 1; k++) if (s[k] && s[k].ele != null) { a += s[k].ele; n++; } return n ? a / n : null; }
function gainLoss(i0, i1) {
  let g = 0, l = 0, ref = null;
  for (let i = i0; i <= i1; i++) {
    const v = smooth(i); if (v == null) return null;
    if (ref == null) { ref = v; continue; }
    if (v - ref >= 5) { g += v - ref; ref = v; } else if (ref - v >= 5) { l += ref - v; ref = v; }
  }
  return { g: Math.round(g), l: Math.round(l) };
}
function nearestPlaceName(i) {
  let best = null, bd = 1e9; S.places.forEach(p => { const d = Math.abs(p.d - S.samples[i].d); if (d < bd) { bd = d; best = p; } });
  return best && bd < 3 ? best.name : `Km ${S.samples[i].d.toFixed(0)}`;
}
function computeStages() {
  const s = S.samples, a = S.a, b = S.b, daily = S.daily, km = i => s[i].d - s[a].d, total = km(b);
  const cands = S.places.filter(p => p.i > a && p.i < b).map(p => ({ ...p, km: km(p.i), lodge: S.lodging.filter(h => Math.abs(h.d - p.d) <= 1.5).length }))
    .filter(p => p.lodge > 0 || p.type === 'city' || p.type === 'town');
  const stops = []; let cur = 0;
  // le tappe intermedie scelte dall'utente restano fermate obbligate
  const must = S.pts.slice(1, -1).map(p => { const n = nearestSample(p); return { i: n, km: km(n), name: p.name }; }).sort((x, y) => x.km - y.km);
  while (total - cur > daily * 1.3 || must.some(m => m.km > cur + 0.5 && m.km < total - 0.5 && m.km <= cur + daily * 1.3)) {
    const m = must.find(m => m.km > cur + 0.5 && m.km < total - 0.5 && m.km <= cur + daily * 1.3);
    if (m) { stops.push({ i: m.i, name: m.name, km: m.km }); cur = m.km; continue; }
    const target = cur + daily;
    const win = cands.filter(c => c.km >= cur + daily * 0.7 && c.km <= cur + daily * 1.3 && !(m && c.km > m.km));
    let pick = null;
    if (win.length) pick = win.reduce((best, c) => { const score = Math.abs(c.km - target) - Math.min(c.lodge, 5) * 0.8 - (c.type === 'hamlet' ? 0 : 1); return !best || score < best.score ? { c, score } : best; }, null).c;
    if (pick) { stops.push({ i: pick.i, name: pick.name, km: pick.km }); cur = pick.km; }
    else { let i = a; while (i < b && km(i) < target) i++; stops.push({ i, name: `Km ${target.toFixed(0)} (nessuna località in OSM)`, km: km(i), free: true }); cur = km(i); }
  }
  const stages = []; let from = { i: a, name: S.pts[0] ? S.pts[0].name : nearestPlaceName(a) };
  stops.forEach(st => { stages.push({ from, to: st }); from = st; });
  stages.push({ from, to: { i: b, name: S.pts.length ? S.pts[S.pts.length - 1].name : nearestPlaceName(b) } });
  stages.forEach((g, n) => {
    g.n = n + 1; g.km = s[g.to.i].d - s[g.from.i].d; g.gl = gainLoss(g.from.i, g.to.i);
    g.lodging = S.lodging.filter(h => Math.abs(h.i - g.to.i) <= 6).sort((x, y) => x.off - y.off).slice(0, 12);
  });
  S.stages = stages;
}
function nearestSample(p) { let bi = 0, bd = 1e9; S.samples.forEach((q, i) => { const d = hav(p, q); if (d < bd) { bd = d; bi = i; } }); return bi; }

/* ---------- rendering ---------- */
function recompute() {
  if (!S.samples.length) return;
  computeStages(); renderStages(); drawStages(); drawProfile();
  const tot = S.samples[S.b].d - S.samples[S.a].d, gl = gainLoss(S.a, S.b);
  $('#totals').textContent = `${tot.toFixed(0)} km · ${S.stages.length} tappe` + (gl ? ` · +${gl.g} m / −${gl.l} m` : ' · quote in caricamento…');
  $('#result').hidden = false;
}
function renderStages() {
  const box = $('#stages'); box.innerHTML = '';
  S.stages.forEach((g, idx) => {
    const d = document.createElement('div'); d.className = 'stage' + (idx === S.sel ? ' sel' : '');
    const gl = g.gl ? `▲ ${g.gl.g} m ▼ ${g.gl.l} m` : 'quote n.d.';
    const gronze = 'https://www.google.com/search?q=' + encodeURIComponent('site:gronze.com ' + g.to.name.replace(/\(.*\)/, ''));
    d.innerHTML = `<h3>Tappa ${g.n}: ${esc(g.from.name)} → ${esc(g.to.name)}</h3>
      <div class="meta">${g.km.toFixed(1)} km · ${gl} · ${g.lodging.length} alloggi in OSM · <a href="${gronze}" target="_blank" rel="noopener">Gronze</a></div>`;
    if (idx === S.sel) {
      const ul = document.createElement('ul');
      if (!g.lodging.length) ul.innerHTML = '<li>Nessun alloggio in OSM vicino al punto di arrivo.</li>';
      g.lodging.forEach(h => {
        const t = h.tags, li = document.createElement('li');
        const phone = t.phone || t['contact:phone'], web = t.website || t['contact:website'], beds = t.capacity || t.beds, fee = t.fee || t.charge;
        li.innerHTML = `<b>${esc(h.name)}</b> <span class="small">${esc(h.kind)}${beds ? ' · ' + esc(beds) + ' posti' : ''}${fee ? ' · ' + esc(fee) : ''}</span>`
          + (phone ? `<br>📞 <a href="tel:${esc(phone)}">${esc(phone)}</a>` : '')
          + (web && /^https?:/.test(web) ? `<br>🔗 <a href="${esc(web)}" target="_blank" rel="noopener">sito</a>` : '')
          + (t.opening_hours ? `<br><span class="small">${esc(t.opening_hours)}</span>` : '');
        ul.appendChild(li);
      });
      d.appendChild(ul);
    }
    d.onclick = e => { if (e.target.tagName === 'A') return; S.sel = S.sel === idx ? -1 : idx; renderStages(); drawStages(); if (S.sel >= 0) map.fitBounds(S.samples.slice(g.from.i, g.to.i + 1).map(p => [p.lat, p.lon]), { padding: [30, 30] }); };
    box.appendChild(d);
  });
}
function drawStages() {
  gRoute.clearLayers(); gStops.clearLayers(); gLodging.clearLayers();
  S.stages.forEach((g, idx) => {
    const ll = S.samples.slice(g.from.i, g.to.i + 1).map(p => [p.lat, p.lon]);
    const pl = L.polyline(ll, { color: idx % 2 ? '#17468c' : '#d6408f', weight: idx === S.sel ? 7 : 5 }).addTo(gRoute);
    pl.bindTooltip(`Tappa ${g.n} · ${g.km.toFixed(1)} km`); pl.on('click', () => { S.sel = idx; renderStages(); drawStages(); });
    const e = S.samples[g.to.i];
    if (idx < S.stages.length - 1) L.marker([e.lat, e.lon], { icon: icon('shell', 'pin-stop') }).bindTooltip(g.to.name).addTo(gStops);
    if (idx === S.sel) g.lodging.forEach(h => L.circleMarker([h.lat, h.lon], { radius: 5, color: '#17468c', fillColor: '#f2c500', fillOpacity: .9 }).bindTooltip(h.name).addTo(gLodging));
  });
}
function drawProfile() {
  const c = $('#profile'), dpr = window.devicePixelRatio || 1, W = c.clientWidth, H = 140;
  c.width = W * dpr; c.height = H * dpr; const x = c.getContext('2d'); x.scale(dpr, dpr); x.clearRect(0, 0, W, H);
  const sl = S.samples.slice(S.a, S.b + 1);
  if (sl.some(p => p.ele == null)) { x.fillStyle = '#888'; x.fillText('Profilo altimetrico in caricamento…', 10, 20); return; }
  const d0 = sl[0].d, d1 = sl[sl.length - 1].d, es = sl.map((p, i) => smooth(S.a + i));
  const mn = Math.min(...es), mx = Math.max(...es), px = d => 30 + (d - d0) / (d1 - d0) * (W - 40), py = e => H - 16 - (e - mn) / Math.max(1, mx - mn) * (H - 30);
  x.strokeStyle = '#ddd'; S.stages.forEach(g => { const X = px(S.samples[g.to.i].d); x.beginPath(); x.moveTo(X, 5); x.lineTo(X, H - 16); x.stroke(); });
  x.beginPath(); sl.forEach((p, i) => i ? x.lineTo(px(p.d), py(es[i])) : x.moveTo(px(p.d), py(es[i])));
  x.strokeStyle = '#c2307f'; x.lineWidth = 2; x.stroke();
  x.fillStyle = '#666'; x.font = '10px sans-serif'; x.fillText(Math.round(mx) + ' m', 2, 12); x.fillText(Math.round(mn) + ' m', 2, H - 18); x.fillText((d1 - d0).toFixed(0) + ' km', W - 40, H - 3);
}
window.addEventListener('resize', () => S.samples.length && drawProfile());
$('#kmDay').oninput = e => { S.daily = +e.target.value; $('#kmLabel').textContent = S.daily; S.sel = -1; recompute(); };

/* ---------- export ---------- */
function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); }
$('#btnCsv').onclick = () => {
  const rows = [['Tappa', 'Da', 'A', 'Km', 'Salita m', 'Discesa m']];
  S.stages.forEach(g => rows.push([g.n, g.from.name, g.to.name, g.km.toFixed(1), g.gl ? g.gl.g : '', g.gl ? g.gl.l : '']));
  download('cammino.csv', rows.map(r => r.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(',')).join('\n'), 'text/csv');
};
$('#btnGpx').onclick = () => {
  const wpt = S.stages.map(g => { const p = S.samples[g.to.i]; return `<wpt lat="${p.lat}" lon="${p.lon}"><name>Tappa ${g.n}: ${esc(g.to.name)}</name></wpt>`; }).join('');
  const trk = S.samples.map(p => `<trkpt lat="${p.lat.toFixed(6)}" lon="${p.lon.toFixed(6)}">${p.ele != null ? `<ele>${p.ele}</ele>` : ''}</trkpt>`).join('');
  download('cammino.gpx', `<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="Pianifica il Cammino" xmlns="http://www.topografix.com/GPX/1/1">${wpt}<trk><name>Cammino</name><trkseg>${trk}</trkseg></trk></gpx>`, 'application/gpx+xml');
};
$('#btnShare').onclick = async () => {
  const p = S.pts.map(q => `${q.role}:${q.lat.toFixed(4)}:${q.lon.toFixed(4)}:${encodeURIComponent(q.name)}`).join('~');
  const pf = Object.entries(S.pref).map(([k, v]) => k + v).join(',');
  const url = location.origin + location.pathname + `#r=${[...S.chosen].join(',')}&k=${S.daily}&p=${p}&f=${pf}`;
  try { await navigator.clipboard.writeText(url); status('Link copiato.'); } catch { prompt('Copia il link:', url); }
};

/* ---------- avvio da link condiviso ---------- */
(async () => {
  const h = new URLSearchParams(location.hash.slice(1)); if (!h.get('r')) return;
  if (!(await catReady)) return;
  for (const x of h.get('r').split(',').filter(Boolean)) {
    if (S.bySlug.has(x)) S.chosen.add(x);
    else if (/^\d+$/.test(x)) { const c = S.cat.find(c => c.ids.includes(+x)); if (c) S.chosen.add(c.slug); }   // vecchi link con id OSM
  }
  renderRoutes(); restyleOver();
  if (h.get('k')) { S.daily = +h.get('k'); $('#kmDay').value = S.daily; $('#kmLabel').textContent = S.daily; }
  (h.get('f') || '').split(',').filter(Boolean).forEach(x => { S.pref[x.slice(0, -1)] = x.slice(-1); });
  await loadChosen();
  (h.get('p') || '').split('~').filter(Boolean).forEach(x => { const [role, lat, lon, name] = x.split(':'); S.pts.push({ role, lat: +lat, lon: +lon, name: decodeURIComponent(name || '') }); });
  renderPts(); if (S.pts.length >= 2) computeRoute();
})();
