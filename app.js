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
  a: 0, b: 0, daily: 25, stages: [], sel: -1, pick: null, token: 0, forced: [], bivi: []
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
  for (const f of [{ key: '*', label: 'Tutti i percorsi' }, ...S.famiglie]) {
    const n = S.cat.filter(c => (f.key === '*' || c.fam === f.key) && c.km >= MIN_KM).length;
    if (!n) continue;   // famiglia ancora senza dati (es. appena aggiunta)
    const b = document.createElement('button');
    b.textContent = `${f.label} (${n})`; b.className = f.key === S.fam ? 'on' : '';
    b.onclick = () => { S.fam = f.key; renderFamTabs(); refreshList(); };
    box.appendChild(b);
  }
}
S.stati = new Set();      // paesi scelti (vuoto = tutti)
S.verifica = new Map();   // nome normalizzato del cammino -> esito del controllo dei buchi
fetch('data/verifica.json').then(r => r.ok ? r.json() : null).then(v => {
  if (!v) return;
  for (const c of v.cammini) S.verifica.set(plain(c.cammino.replace(/\(.*?\)/g, '')).trim(), c);
  if (S.cat.length) renderRoutes();
}).catch(() => {});
function esitoDi(c) {
  const n = plain(c.nome);
  for (const [k, v] of S.verifica) if (n === k || n.startsWith(k + ' ') || k.startsWith(n + ' ')) return v;
  return null;
}
function renderPaesi() {
  const box = $('#statiChips'); box.innerHTML = '';
  const paesi = [...new Set(S.cat.filter(c => (S.fam === '*' || c.fam === S.fam) && c.km >= MIN_KM).flatMap(c => c.paesi))].sort((a, b) => a.localeCompare(b));
  for (const s of [...S.stati]) if (!paesi.includes(s)) S.stati.delete(s);
  const all = document.createElement('button');
  all.textContent = 'Tutti i paesi'; all.className = S.stati.size ? '' : 'on';
  all.onclick = () => { S.stati.clear(); refreshList(); };
  box.appendChild(all);
  for (const p of paesi) {
    const b = document.createElement('button');
    b.textContent = p; b.className = S.stati.has(p) ? 'on' : '';
    b.onclick = () => { S.stati.has(p) ? S.stati.delete(p) : S.stati.add(p); refreshList(); };
    box.appendChild(b);
  }
}
function refreshList() { listLimit = 80; renderPaesi(); renderRoutes(); drawOverview(); }
const plain = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\bcami(n|nh)o\b/g, 'camino');
function filteredRoutes() {
  const f = plain($('#filter').value.trim()), brevi = $('#fBrevi').checked;
  return S.cat.filter(c => S.chosen.has(c.slug) || (
    (f || S.fam === '*' ? true : c.fam === S.fam) && (brevi || c.km >= MIN_KM || f) &&
    (!S.stati.size || c.paesi.some(p => S.stati.has(p))) &&
    (!f || plain(c.nome).includes(f))));
}
let listLimit = 80;
function renderRoutes() {
  const ul = $('#routeList'); ul.innerHTML = '';
  const f = plain($('#filter').value.trim());
  const score = c => { if (!f) return 0; const n = plain(c.nome); return n.startsWith(f) ? 3 : new RegExp('\\b' + f.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b').test(n) ? 2 : 1; };
  const list = filteredRoutes().sort((a, b) => (S.chosen.has(b.slug) - S.chosen.has(a.slug)) || score(b) - score(a) || b.km - a.km);
  $('#nRisultati').textContent = `${list.length} cammini`;
  list.slice(0, listLimit).forEach(c => {
    const li = document.createElement('li'), v = esitoDi(c);
    const ck = v ? (v.problemi ? `<span class="ck ko" title="Controllo tappa per tappa: ${v.problemi} tratti con problemi">⚠ ${v.problemi} buchi</span>` : '<span class="ck ok" title="Controllo tappa per tappa superato">✔ completo</span>') : '';
    li.innerHTML = `<label><input type="checkbox" ${S.chosen.has(c.slug) ? 'checked' : ''}> <span style="color:${colorOf(c.slug)}">■</span> <b>${esc(c.nome)}</b>${ck}` +
      `<br><span class="small muted">${c.km} km · ${esc(c.paesi.join(', ') || '—')}</span>` +
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
  $('#chosenBox').innerHTML = ch.length ? `<b>Scelti:</b> ${ch.map(c => esc(c.nome)).join(' · ')} <span class="muted">(${ch.reduce((a, c) => a + c.km, 0)} km di tracciati)</span>` : 'Nessun cammino scelto: tocca una linea sulla mappa o spunta nell\'elenco.';
  $('#bCammini').textContent = ch.length || '';
}
function toggle(slug, on) {
  on ? S.chosen.add(slug) : S.chosen.delete(slug);
  restyleOver(); renderRoutes();
}
let fT; $('#filter').oninput = () => { clearTimeout(fT); fT = setTimeout(refreshList, 150); };
$('#fBrevi').onchange = refreshList;
$('#btnClearSel').onclick = () => { S.chosen.clear(); restyleOver(); renderRoutes(); };

/* ---------- schede del pannello ---------- */
function showTab(name) {
  document.querySelectorAll('.tabbar [data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach(t => t.hidden = t.id !== 'tab-' + name);
  if (name === 'cammini' && !S.over.size && S.cat.length) drawOverview();
  if (name === 'tappe' && S.samples.length) drawProfile();
  $('#panel').scrollTop = 0;
}
document.querySelectorAll('.tabbar [data-tab]').forEach(b => b.onclick = () => showTab(b.dataset.tab));
$('#btnSheet').onclick = () => { document.body.classList.toggle('sheet'); setTimeout(() => map.invalidateSize(), 250); };

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
      const ways = [], names = [];
      for (const f of j.features || []) {
        const g = f.geometry; if (!g) continue;
        const lines = g.type === 'MultiLineString' ? g.coordinates : g.type === 'LineString' ? [g.coordinates] : [];
        const fn = (f.properties && f.properties.name) || '';
        for (const l of lines) { const w = l.map(([lon, lat]) => ({ lat, lon })); if (w.length > 1) { ways.push(w); names.push(fn); } }
      }
      if (ways.length) S.tops.push({ id: slug, name: S.bySlug.get(slug).nome, ways, names });
    }
    if (!S.tops.length) throw new Error('nessun tracciato trovato');
    gOver.clearLayers(); S.over.clear();
    status('Costruisco la rete…'); await new Promise(r => setTimeout(r, 20));
    buildGraph(); renderPrefs(); drawNet();
    const km = G.adj.reduce((a, l) => a + l.reduce((s, e) => s + e.w, 0), 0) / 2;
    status(`Rete pronta: ${S.tops.length} cammini, ~${km.toFixed(0)} km. Ora scegli da dove a dove.`);
    showTab('itinerario'); $('#pStart').focus();
    loadAllPlaces();
    const pts = []; S.tops.forEach(t => t.ways.forEach(w => pts.push([w[0].lat, w[0].lon])));
    map.fitBounds(pts);
  } catch (e) { status('Errore nel caricare i cammini: ' + e.message); }
}

// paesi di tappa ufficiali (Gronze) con coordinate: fermate preferite per la fine tappa
S.gronzeTowns = [];
Promise.all([fetch('data/tappe_coord.json').then(r => r.ok ? r.json() : null), fetch('scripts/tappe_gronze.json').then(r => r.ok ? r.json() : null)])
  .then(([c, g]) => {
    if (!c || !g) return;
    const seen = new Set();
    for (const cm of g.cammini) { const z = cm.zona.join(','); for (const t of cm.tappe) { const ll = c.tappe[t + '|' + z]; if (ll && !seen.has(t)) { seen.add(t); S.gronzeTowns.push({ name: t.replace(/\s*\(.*?\)/, ''), lat: ll[0], lon: ll[1], cammino: cm.nome }); } } }
  }).catch(() => {});
// paesi lungo i cammini caricati (per i suggerimenti mentre si scrive "Da" e "A")
async function loadAllPlaces() {
  try {
    const ix = await (await fetch('data/alloggi/indice.json')).json();
    const keys = new Set(S.tops.flatMap(t => (S.bySlug.get(t.id) || { aree: [] }).aree));
    const files = await Promise.all(ix.aree.filter(a => keys.has(a.key)).map(a => fetch('data/alloggi/' + a.file).then(r => r.ok ? r.json() : null).catch(() => null)));
    const seen = new Set(); S.allPlaces = [];
    for (const f of files) for (const p of (f && f.luoghi) || []) if (!seen.has(p.id)) { seen.add(p.id); S.allPlaces.push(p); }
    S.allPlaces.sort((a, b) => (b.a ? b.a.length : 0) - (a.a ? a.a.length : 0));
  } catch (e) { S.allPlaces = []; }
}

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
function addEdge(u, v, w, rel, fn) {
  if (u === v || G.adj[u].some(e => e.v === v && e.rel === rel)) return;
  G.adj[u].push({ v, w, rel, fn }); G.adj[v].push({ v: u, w, rel, fn });
}
function buildGraph() {
  G = { nodes: [], adj: [], idx: new Map(), grid: new Map() };
  for (const t of S.tops) t.ways.forEach((w, wi) => {
    const fn = (t.names && t.names[wi]) || t.name;
    let prev = -1;
    for (const p of w) { const id = nodeId(p); if (prev >= 0) addEdge(prev, id, hav(G.nodes[prev], G.nodes[id]), t.id, fn); prev = id; }
  });
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

/* ---------- 3. punti dell'itinerario: Da / passando per / A ---------- */
function addPoint(role, lat, lon, name, extra) {
  const pt = { lat, lon, role, name: name || `${lat.toFixed(4)}, ${lon.toFixed(4)}`, ...(extra || {}) };
  if (role === 'start') { S.pts = S.pts.filter(p => p.role !== 'start'); S.pts.unshift(pt); }
  else if (role === 'end') { S.pts = S.pts.filter(p => p.role !== 'end'); S.pts.push(pt); }
  else { const e = S.pts.findIndex(p => p.role === 'end'); if (e >= 0) S.pts.splice(e, 0, pt); else S.pts.push(pt); }
  renderPts();
}
function renderPts() {
  gPts.clearLayers();
  const st = S.pts.find(p => p.role === 'start'), en = S.pts.find(p => p.role === 'end');
  if (!$('#pStart').dataset.typing) $('#pStart').value = st ? st.name : '';
  if (!$('#pEnd').dataset.typing) $('#pEnd').value = en ? en.name : '';
  const ul = $('#viaList'); ul.innerHTML = '';
  S.pts.forEach((p, i) => {
    if (p.role === 'via') {
      const li = document.createElement('li');
      li.innerHTML = `<span class="dot via"></span><span>${p.auto ? '↪ ' : ''}${esc(p.name)}</span><button title="Togli">✕</button>`;
      li.querySelector('button').onclick = () => { S.pts.splice(i, 1); renderPts(); if (S.samples.length) computeRoute(); };
      ul.appendChild(li);
    }
    const lab = { start: 'Partenza', via: 'Passando per', end: 'Arrivo' }[p.role];
    L.marker([p.lat, p.lon], { icon: p.role === 'end' ? icon('shell', 'pin-end') : icon('arrow', p.role === 'start' ? 'pin-start' : 'pin-via') }).bindTooltip(lab + ' · ' + p.name).addTo(gPts);
  });
}
// suggerimenti mentre si scrive: prima le località lungo i cammini caricati, poi Nominatim
let sugT, sugFor = null;
async function suggest(input, role) {
  const q = input.value.trim(), box = $('#suggest');
  if (q.length < 3) { box.hidden = true; return; }
  sugFor = { input, role };
  let items = [];
  const pq = plain(q);
  for (const p of S.allPlaces || []) { if (plain(p.n).startsWith(pq)) items.push({ name: p.n, lat: p.la, lon: p.lo, sub: 'lungo i cammini' }); if (items.length >= 5) break; }
  box.innerHTML = items.map((it, k) => `<li data-k="${k}">${esc(it.name)} <span class="small muted">${esc(it.sub)}</span></li>`).join('') || '<li class="small muted">Cerco…</li>';
  box.hidden = false; box.items = items;
  try {
    const vb = G.nodes.length ? (() => { let a = 90, b = 180, c = -90, d = -180; for (let i = 0; i < G.nodes.length; i += 50) { const n = G.nodes[i]; a = Math.min(a, n.lat); c = Math.max(c, n.lat); b = Math.min(b, n.lon); d = Math.max(d, n.lon); } return `&viewbox=${b - .5},${c + .5},${d + .5},${a - .5}`; })() : '';
    const r = await fetch('https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&accept-language=it&q=' + encodeURIComponent(q) + vb);
    const j = await r.json();
    if (input.value.trim() !== q) return;
    items = items.concat(j.map(x => ({ name: x.name || x.display_name.split(',')[0], lat: +x.lat, lon: +x.lon, sub: x.display_name.split(',').slice(1, 3).join(',') })));
    box.innerHTML = items.map((it, k) => `<li data-k="${k}">${esc(it.name)} <span class="small muted">${esc(it.sub)}</span></li>`).join('') || '<li class="small muted">Nessuna località trovata.</li>';
    box.items = items;
  } catch (e) { if (!items.length) box.innerHTML = '<li class="small muted">Ricerca non disponibile.</li>'; }
}
$('#suggest').onclick = e => {
  const li = e.target.closest('li[data-k]'); if (!li || !sugFor) return;
  const it = $('#suggest').items[+li.dataset.k];
  delete sugFor.input.dataset.typing;
  addPoint(sugFor.role, it.lat, it.lon, it.name);
  if (sugFor.role === 'via') sugFor.input.value = '';
  $('#suggest').hidden = true; sugFor.input.blur(); renderPts();
  map.setView([it.lat, it.lon], Math.max(map.getZoom(), 9));
  if (S.pts.some(p => p.role === 'start') && S.pts.some(p => p.role === 'end')) computeRoute();
};
[['#pStart', 'start'], ['#pVia', 'via'], ['#pEnd', 'end']].forEach(([sel, role]) => {
  const inp = $(sel);
  inp.oninput = () => { inp.dataset.typing = '1'; clearTimeout(sugT); sugT = setTimeout(() => suggest(inp, role), 350); };
  inp.onblur = () => setTimeout(() => { if (!$('#suggest').matches(':hover')) { $('#suggest').hidden = true; delete inp.dataset.typing; renderPts(); } }, 200);
});
document.querySelectorAll('[data-pick]').forEach(b => b.onclick = () => {
  S.pick = S.pick === b.dataset.pick ? null : b.dataset.pick;
  document.querySelectorAll('[data-pick]').forEach(x => x.classList.toggle('on', x.dataset.pick === S.pick));
  if (S.pick) status('Tocca la mappa nel punto scelto.');
});
map.on('click', e => {
  if (!S.pick) return;
  const role = S.pick; S.pick = null; document.querySelectorAll('[data-pick]').forEach(x => x.classList.remove('on'));
  const n = (S.allPlaces || []).reduce((b, p) => { const d = hav({ lat: p.la, lon: p.lo }, { lat: e.latlng.lat, lon: e.latlng.lng }); return d < b.d ? { d, p } : b; }, { d: 3, p: null }).p;
  addPoint(role, e.latlng.lat, e.latlng.lng, n ? n.n : null);
  if (S.pts.some(p => p.role === 'start') && S.pts.some(p => p.role === 'end')) computeRoute();
});
$('#btnClear').onclick = () => { S.pts = []; S.forced = []; renderPts(); };
$('#btnFlip').onclick = () => {
  S.pts.reverse(); S.pts.forEach((p, i) => p.role = i === 0 ? 'start' : i === S.pts.length - 1 ? 'end' : 'via'); S.forced = []; renderPts();
  if (S.samples.length) computeRoute();
};
$('#btnRoute').onclick = () => computeRoute();

/* ---------- calcolo del percorso ---------- */
async function computeRoute() {
  const tk = ++S.token;
  if (!G.nodes.length) { status('Prima scegli i cammini (scheda 1) e premi "Usa i cammini scelti".'); showTab('cammini'); return; }
  if (S.pts.length < 2 || S.pts[0].role !== 'start' || S.pts[S.pts.length - 1].role !== 'end') { status('Servono almeno partenza e arrivo.'); return; }
  const snaps = [];
  for (const p of S.pts) {
    const s = nearestNode(p.lat, p.lon);
    if (!s) { status(`“${p.name}” è lontano dalla rete (oltre 4 km).`); return; }
    snaps.push({ ...s, name: p.name });
  }
  let nodes = [];
  for (let i = 1; i < snaps.length; i++) {
    const path = shortest(snaps[i - 1].id, snaps[i].id);
    if (!path) { status(`Nessun collegamento tra “${snaps[i - 1].name}” e “${snaps[i].name}” (rete non connessa o cammini evitati).`); return; }
    nodes = nodes.length ? nodes.concat(path.slice(1)) : path;
  }
  // percorso nodo per nodo (serve per i bivi) e km progressivi
  S.path = nodes; S.pathCum = [0];
  for (let i = 1; i < nodes.length; i++) S.pathCum.push(S.pathCum[i - 1] + hav(G.nodes[nodes[i - 1]], G.nodes[nodes[i]]));
  const line = nodes.map(id => G.nodes[id]);
  S.samples = resample(line); S.a = 0; S.b = S.samples.length - 1; S.places = []; S.lodging = []; S.sel = -1;
  map.fitBounds(S.samples.map(p => [p.lat, p.lon]));
  status(`Percorso di ${S.samples[S.b].d.toFixed(0)} km. Cerco i bivi, poi località e quote…`);
  await new Promise(r => setTimeout(r, 10));
  computeBivi();
  recompute();
  try { await loadPOI(); } catch (e) { if (tk === S.token) status('Località/ostelli non disponibili: ' + e.message); }
  if (tk !== S.token) return; recompute();
  try { await loadElevation(tk); if (tk === S.token) status('Fatto.'); } catch (e) { if (tk === S.token) status('Quote non disponibili: ' + e.message); }
  if (tk === S.token) recompute();
}

/* ---------- bivi: deviazioni lungo il percorso ----------
   Si tolgono dalla rete i nodi del percorso: ogni pezzo di rete rimasto che si riattacca al percorso in due punti
   diversi è una deviazione possibile tra quei due punti. I pezzi che si attaccano in un punto solo (altri cammini
   che vanno altrove) non sono bivi. */
function relName(rel) { return rel === 'gap' ? null : (S.bySlug.get(rel) || {}).nome || rel; }
function mainRel(nodes, key = 'rel') {
  const w = new Map();
  for (let k = 1; k < nodes.length; k++) {
    const e = G.adj[nodes[k - 1]].find(x => x.v === nodes[k]); if (!e || e.rel === 'gap') continue;
    w.set(e[key], (w.get(e[key]) || 0) + e.w);
  }
  let best = null, bw = -1; for (const [r, v] of w) if (v > bw) { bw = v; best = r; }
  return best;
}
// nome leggibile di un tratto: il nome OSM se dice qualcosa in più del cammino, altrimenti "variante per <paese>"
function tratto(nodes, rel, fn, other) {
  const cam = relName(rel) || 'deviazione';
  const base = x => plain(String(x || '').replace(/\s*[-–:(,].*$/, '').replace(/\s+\d+$/, '')).trim();
  if (fn && base(fn) !== base(cam) && base(fn) !== base(other)) return fn.length > 70 ? fn.slice(0, 68) + '…' : fn;
  if (fn && /variant|alternat|altern/i.test(fn) && fn !== other) return fn.length > 70 ? fn.slice(0, 68) + '…' : fn;
  const mid = G.nodes[nodes[Math.floor(nodes.length / 2)]];
  let best = null, bd = 4;
  for (const p of S.allPlaces || []) { const d = hav({ lat: p.la, lon: p.lo }, mid); if (d < bd) { bd = d; best = p.n; } }
  for (const t of S.gronzeTowns || []) { const d = hav(t, mid); if (d < bd) { bd = d; best = t.name; } }
  return best ? `${cam}, variante per ${best}` : cam;
}
function computeBivi() {
  const P = S.path, cum = S.pathCum, N = G.nodes.length;
  S.bivi = [];
  if (!P || P.length < 3) return;
  const onPath = new Int32Array(N).fill(-1);
  P.forEach((u, i) => { if (onPath[u] < 0) onPath[u] = i; });
  const comp = new Int32Array(N).fill(-1), attach = [];
  for (let i = 0; i < P.length; i++) {
    for (const e of G.adj[P[i]]) {
      const v = e.v; if (onPath[v] >= 0 || factor(e.rel) === null) continue;
      if (comp[v] < 0) {
        const id = attach.length; attach.push([]); comp[v] = id; const q = [v];
        while (q.length) { const x = q.pop(); for (const f of G.adj[x]) { if (factor(f.rel) === null) continue; const y = f.v; if (onPath[y] < 0 && comp[y] < 0) { comp[y] = id; q.push(y); } } }
      }
      attach[comp[v]].push({ i, v, w: e.w });
    }
  }
  const found = [];
  attach.forEach((list, cid) => {
    if (list.length < 2) return;
    list.sort((a, b) => a.i - b.i);
    const groups = [];
    for (const a of list) { const g = groups[groups.length - 1]; if (g && cum[a.i] - cum[g.i] < 0.3) { if (a.w < g.w) Object.assign(g, a); } else groups.push({ ...a }); }
    for (let k = 1; k < groups.length; k++) {
      const A = groups[k - 1], B = groups[k], main = cum[B.i] - cum[A.i];
      if (main < 0.4) continue;
      const limit = main * 2.5 + 10, alt = shortestIn(A.v, B.v, cid, comp, limit);
      if (!alt) continue;
      const len = alt.d + A.w + B.w;
      if (len < 0.3 || len > limit) continue;
      if (main < 1.5 && Math.abs(len - main) < 1) continue;   // microvarianti dentro un paese
      found.push({ i: A.i, j: B.i, main, alt: len, nodes: [P[A.i], ...alt.path, P[B.i]] });
    }
  });
  // i bivi vicini e quasi uguali si fondono; i più utili prima
  found.sort((a, b) => a.i - b.i || a.alt - b.alt);
  const out = [];
  for (const f of found) {
    const dup = out.find(o => Math.abs(cum[o.i] - cum[f.i]) < 1 && Math.abs(cum[o.j] - cum[f.j]) < 1 && Math.abs(o.alt - f.alt) < 1);
    if (dup) continue;
    const pm = P.slice(f.i, f.j + 1);
    f.relMain = mainRel(pm); f.relAlt = mainRel(f.nodes);
    f.fnMain = mainRel(pm, 'fn'); f.fnAlt = mainRel(f.nodes, 'fn');
    f.nameMain = tratto(pm, f.relMain, f.fnMain, null);
    f.nameAlt = tratto(f.nodes, f.relAlt, f.fnAlt, f.fnMain);
    if (f.nameAlt === f.nameMain) f.nameAlt += ' (altro percorso)';
    f.kmA = cum[f.i]; f.kmB = cum[f.j];
    out.push(f);
  }
  S.bivi = out;
}
function shortestIn(src, dst, cid, comp, limit) {
  const dist = new Map([[src, 0]]), prev = new Map(), h = new Heap(); h.push(0, src);
  let seen = 0;
  while (h.size) {
    const [d, u] = h.pop(); if (d > dist.get(u)) continue;
    if (u === dst) { const path = []; for (let x = dst; x !== undefined; x = prev.get(x)) path.push(x); return { d, path: path.reverse() }; }
    if (d > limit || ++seen > 300000) return null;
    for (const e of G.adj[u]) {
      const v = e.v; if (comp[v] !== cid) continue; const f = factor(e.rel); if (f === null) continue;
      const nd = d + e.w; if (nd < (dist.has(v) ? dist.get(v) : Infinity)) { dist.set(v, nd); prev.set(v, u); h.push(nd, v); }
    }
  }
  return null;
}
// sceglie una deviazione: diventa un punto di passaggio automatico ("↪"), che si toglie con ✕
function takeAlt(b) {
  const mid = G.nodes[b.nodes[Math.floor(b.nodes.length / 2)]];
  // toglie eventuali deviazioni già scelte nello stesso tratto
  S.pts = S.pts.filter(p => !(p.auto && p.km >= b.kmA - 0.5 && p.km <= b.kmB + 0.5));
  const vias = S.pts.filter(p => p.role === 'via');
  const name = b.nameAlt || 'Variante';
  vias.push({ lat: mid.lat, lon: mid.lon, role: 'via', name, auto: true, km: b.kmA });
  vias.forEach(v => { if (v.km == null) v.km = S.samples.length ? S.samples[nearestSample(v)].d : 0; });
  vias.sort((x, y) => x.km - y.km);
  S.pts = [S.pts.find(p => p.role === 'start'), ...vias, S.pts.find(p => p.role === 'end')].filter(Boolean);
  renderPts(); computeRoute();
}
function placeNear(km) {
  let best = null, bd = 2.5;
  for (const p of S.places || []) { const d = Math.abs(p.d - km); if (d < bd) { bd = d; best = p; } }
  return best ? best.name : null;
}
function renderMetro() {
  const ol = $('#metro'); ol.innerHTML = '';
  if (!S.samples.length) return;
  $('#metroHint').hidden = true;
  const items = [];
  const total = S.samples[S.b].d;
  items.push({ km: 0, type: 'st', name: S.pts[0] ? S.pts[0].name : 'Partenza', sub: 'partenza' });
  S.stages.slice(0, -1).forEach(g => items.push({ km: S.samples[g.to.i].d, type: 'st', name: g.to.name, sub: `fine tappa ${g.n}` }));
  items.push({ km: total, type: 'st end', name: S.pts[S.pts.length - 1] ? S.pts[S.pts.length - 1].name : 'Arrivo', sub: 'arrivo' });
  (S.bivi || []).forEach((b, k) => items.push({ km: b.kmA, type: 'bv', b, k }));
  items.sort((a, b) => a.km - b.km || (a.type === 'bv') - (b.type === 'bv'));
  let lastRel = null;
  for (const it of items) {
    const li = document.createElement('li');
    if (it.type.startsWith('st')) {
      li.className = it.type;
      li.innerHTML = `<span class="km">${it.km.toFixed(0)} km</span><b>${esc(it.name)}</b><br><span class="small muted">${esc(it.sub)}</span>`;
    } else {
      const b = it.b, d = b.alt - b.main, where = placeNear(b.kmA) || `km ${b.kmA.toFixed(0)}`, to = placeNear(b.kmB) || `km ${b.kmB.toFixed(0)}`;
      const nm = b.nameMain || 'percorso attuale', na = b.nameAlt || 'deviazione';
      li.className = 'bv';
      li.style.setProperty('--line-c', colorOf(b.relMain || ''));
      li.innerHTML = `<span class="km">${b.kmA.toFixed(0)} km</span><span class="q">Bivio a ${esc(where)}</span> <span class="small muted">→ si ricongiunge a ${esc(to)}</span>
        <div class="opt cur"><span class="sw" style="background:${colorOf(b.relMain || '')}"></span><span class="nm">${esc(nm)}</span><span class="d">${b.main.toFixed(1)} km</span><span class="small">attuale</span></div>
        <div class="opt"><span class="sw" style="background:${colorOf(b.relAlt || '')}"></span><span class="nm">${esc(na)}</span><span class="d ${d > 0 ? 'plus' : 'minus'}">${b.alt.toFixed(1)} km (${d > 0 ? '+' : ''}${d.toFixed(1)})</span><button>Prendi</button></div>`;
      li.querySelector('.opt:not(.cur) button').onclick = () => takeAlt(b);
      const altLine = L.polyline(b.nodes.map(n => [G.nodes[n].lat, G.nodes[n].lon]), { color: colorOf(b.relAlt || ''), weight: 6, dashArray: '6 6', opacity: .9 });
      li.onmouseenter = () => altLine.addTo(map); li.onmouseleave = () => map.removeLayer(altLine);
    }
    ol.appendChild(li);
  }
  const nb = (S.bivi || []).length;
  $('#totals').textContent = `${total.toFixed(0)} km · ${S.stages.length} tappe · ${nb ? nb + (nb === 1 ? ' bivio' : ' bivi') : 'nessun bivio'}`;
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
function addGronzeTowns() {
  // le tappe di Gronze vicine al percorso diventano località (se non ci sono già)
  for (const t of S.gronzeTowns) {
    let bi = -1, bd = 2; for (let i = 0; i < S.samples.length; i += 2) { const d = hav(t, S.samples[i]); if (d < bd) { bd = d; bi = i; } }
    if (bi < 0) continue;
    const dup = S.places.find(p => Math.abs(p.d - S.samples[bi].d) < 1.5 && plain(p.name) === plain(t.name));
    if (dup) { dup.gronze = true; continue; }
    S.places.push({ name: t.name, type: 'town', lat: t.lat, lon: t.lon, i: bi, d: S.samples[bi].d, off: bd, gronze: true });
  }
}
async function loadPOI() {
  try { await loadPOIReal(); } finally { addGronzeTowns(); }
}
async function loadPOIReal() {
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
  const cands = S.places.filter(p => p.i > a && p.i < b).map(p => ({ ...p, km: km(p.i), lodge: S.lodging.filter(h => Math.abs(h.d - p.d) <= 1.5).length + (p.gronze ? 3 : 0) }))
    .filter(p => p.lodge > 0 || p.type === 'city' || p.type === 'town');
  const stops = []; let cur = 0;
  // le tappe intermedie scelte dall'utente restano fermate obbligate
  // fermate obbligate: i passaggi scelti (non le deviazioni automatiche) e le fine tappa spostate a mano
  const must = S.pts.slice(1, -1).filter(p => !p.auto).map(p => { const n = nearestSample(p); return { i: n, km: km(n), name: p.name }; })
    .concat((S.forced || []).filter(f => f.i > a && f.i < b).map(f => ({ i: f.i, km: km(f.i), name: f.name, fixed: true })))
    .sort((x, y) => x.km - y.km);
  while (total - cur > daily * 1.3 || must.some(m => m.km > cur + 0.5 && m.km < total - 0.5)) {
    const m = must.find(m => m.km > cur + 0.5 && m.km < total - 0.5 && m.km <= cur + daily * 1.6);
    if (m) { stops.push({ i: m.i, name: m.name, km: m.km, fixed: m.fixed }); cur = m.km; continue; }
    if (total - cur <= daily * 1.3 && !must.some(m => m.km > cur + 0.5 && m.km < total - 0.5)) break;
    const target = cur + daily;
    const nextMust = must.find(x => x.km > cur + 0.5);
    const win = cands.filter(c => c.km >= cur + daily * 0.7 && c.km <= cur + daily * 1.3 && !(nextMust && c.km >= nextMust.km - 3));
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
    g.sforzo = g.km + (g.gl ? g.gl.g / 100 : 0);   // km-sforzo: +1 km ogni 100 m di salita
    g.warn = [];
    if (g.km > daily * 1.3) g.warn.push(`tappa lunga (${g.km.toFixed(0)} km, ne avevi chiesti ${daily})`);
    if (g.km < daily * 0.5 && n < stages.length - 1) g.warn.push('tappa corta');
    if (n < stages.length - 1 && S.lodging.length && !g.lodging.length) g.warn.push('nessun alloggio in OSM all\'arrivo');
    if (g.to.free) g.warn.push('nessuna località con alloggio nella fascia di km: la tappa finisce in aperta campagna');
    if (g.gl && g.gl.g > 900) g.warn.push(`dislivello impegnativo (+${g.gl.g} m)`);
  });
  S.stages = stages;
}
function moveStageEnd(idx, dir) {
  const g = S.stages[idx], s = S.samples; if (!g || idx === S.stages.length - 1) return;
  const next = S.stages[idx + 1];
  const cands = S.places.filter(p => (p.type === 'city' || p.type === 'town' || S.lodging.some(h => Math.abs(h.d - p.d) <= 1.5)))
    .filter(p => dir < 0 ? (p.i < g.to.i - 4 && p.i > g.from.i + 4) : (p.i > g.to.i + 4 && p.i < next.to.i - 4))
    .sort((x, y) => dir < 0 ? y.i - x.i : x.i - y.i);
  if (!cands.length) { status('Nessun altro paese con alloggio in quella direzione.'); return; }
  const c = cands[0];
  S.forced = (S.forced || []).filter(f => f.i <= g.from.i || f.i >= next.to.i);   // toglie la vecchia fine tappa fissata
  S.forced.push({ i: c.i, name: c.name });
  // le tappe precedenti restano come sono: le fisso
  S.stages.slice(0, idx).forEach(x => { if (!S.forced.some(f => f.i === x.to.i)) S.forced.push({ i: x.to.i, name: x.to.name }); });
  S.sel = idx; recompute();
  status(`Tappa ${g.n}: ora finisce a ${c.name}.`);
}
function nearestSample(p) { let bi = 0, bd = 1e9; S.samples.forEach((q, i) => { const d = hav(p, q); if (d < bd) { bd = d; bi = i; } }); return bi; }

/* ---------- rendering ---------- */
function recompute() {
  if (!S.samples.length) return;
  computeStages(); renderStages(); drawStages(); renderMetro();
  const tot = S.samples[S.b].d - S.samples[S.a].d, gl = gainLoss(S.a, S.b);
  const nw = S.stages.reduce((a, g) => a + g.warn.length, 0);
  $('#summary').innerHTML = `<div><b>${tot.toFixed(0)}</b>km</div><div><b>${S.stages.length}</b>tappe</div>` +
    `<div><b>${gl ? '+' + gl.g : '…'}</b>m di salita</div><div><b>${nw}</b>avvisi</div>`;
  $('#result').hidden = false; $('#resultEmpty').hidden = true;
  $('#bTappe').textContent = S.stages.length;
  if (!$('#tab-tappe').hidden) drawProfile();
}
function renderStages() {
  const box = $('#stages'); box.innerHTML = '';
  S.stages.forEach((g, idx) => {
    const d = document.createElement('div'); d.className = 'stage' + (idx === S.sel ? ' sel' : '');
    const gl = g.gl ? `▲ ${g.gl.g} m ▼ ${g.gl.l} m` : 'quote n.d.';
    const gronze = 'https://www.google.com/search?q=' + encodeURIComponent('site:gronze.com ' + g.to.name.replace(/\(.*\)/, ''));
    const last = idx === S.stages.length - 1;
    d.innerHTML = `<h3>Tappa ${g.n}: ${esc(g.from.name)} → ${esc(g.to.name)}${g.to.fixed ? ' 📌' : ''}</h3>
      <div class="meta">${g.km.toFixed(1)} km · ${gl}${g.gl ? ` · ${g.sforzo.toFixed(0)} km-sforzo` : ''} · ${g.lodging.length} alloggi · <a href="${gronze}" target="_blank" rel="noopener">Gronze</a></div>` +
      g.warn.map(w => `<div class="warn">⚠ ${esc(w)}</div>`).join('') +
      (last ? '' : `<div class="mv"><button data-mv="-1" title="Fine tappa al paese con alloggio precedente">◀ finisci prima</button><button data-mv="1" title="Fine tappa al paese con alloggio successivo">finisci dopo ▶</button></div>`);
    d.querySelectorAll('[data-mv]').forEach(bt => bt.onclick = ev => { ev.stopPropagation(); moveStageEnd(idx, +bt.dataset.mv); });
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
    d.onclick = e => { if (e.target.tagName === 'A' || e.target.tagName === 'BUTTON') return; S.sel = S.sel === idx ? -1 : idx; renderStages(); drawStages(); if (S.sel >= 0) map.fitBounds(S.samples.slice(g.from.i, g.to.i + 1).map(p => [p.lat, p.lon]), { padding: [30, 30] }); };
    box.appendChild(d);
  });
}
function drawStages() {
  gRoute.clearLayers(); gStops.clearLayers(); gLodging.clearLayers();
  S.stages.forEach((g, idx) => {
    const ll = S.samples.slice(g.from.i, g.to.i + 1).map(p => [p.lat, p.lon]);
    const pl = L.polyline(ll, { color: idx % 2 ? '#17468c' : '#d6408f', weight: idx === S.sel ? 7 : 5 }).addTo(gRoute);
    pl.bindTooltip(`Tappa ${g.n} · ${g.km.toFixed(1)} km`); pl.on('click', () => { S.sel = idx; showTab('tappe'); renderStages(); drawStages(); document.querySelectorAll('.stage')[idx]?.scrollIntoView({ block: 'center' }); });
    const e = S.samples[g.to.i];
    if (idx < S.stages.length - 1) L.circleMarker([e.lat, e.lon], { radius: idx === S.sel || idx === S.sel - 1 ? 7 : 5, color: '#fff', weight: 2, fillColor: '#1e4aa0', fillOpacity: 1 }).bindTooltip(`Fine tappa ${g.n}: ${g.to.name}`).addTo(gStops);
    if (idx === 0) (S.bivi || []).forEach(b => { const n = G.nodes[S.path[b.i]]; L.circleMarker([n.lat, n.lon], { radius: 4, color: '#8a6d00', weight: 1.5, fillColor: '#f2c500', fillOpacity: 1 }).bindTooltip(`Bivio: ${b.nameAlt || 'deviazione'} (${(b.alt - b.main) > 0 ? '+' : ''}${(b.alt - b.main).toFixed(1)} km)`).on('click', () => showTab('itinerario')).addTo(gStops); });
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
$('#kmDay').oninput = e => { S.daily = +e.target.value; $('#kmLabel').textContent = S.daily; S.sel = -1; S.forced = []; recompute(); };

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
