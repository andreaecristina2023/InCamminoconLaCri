#!/usr/bin/env python3
"""
Genera data/cammini.geojson con i cammini di Santiago presi da OpenStreetMap (Overpass API).

Uso:
    python3 scripts/build_geojson.py              # cammini principali (consigliato)
    python3 scripts/build_geojson.py --broad      # ricerca larga (molte relazioni)
    python3 scripts/build_geojson.py --out altro.geojson

Solo libreria standard di Python. Dati © contributori di OpenStreetMap (ODbL).
"""
import argparse, datetime, glob, json, os, re, socket, sys, time, urllib.parse, urllib.request

# i runner di GitHub spesso non hanno IPv6: forza IPv4
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, *a, **k: _gai(h, p, socket.AF_INET, *a, **k)

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
BBOX = "35,-11,51.5,10"   # Penisola iberica + Francia occidentale e meridionale

NAMES = ("Franc[eé]s|Aragon[eé]s|Podiensis|Lemovicensis|Turonensis|Tolosana|del Norte|Primitivo|"
         "Portugu[eé]s|Ingl[eé]s|Fisterr|Finisterre|Mux[ií]a|de la Plata|Sanabr|Baztán|Vasco|"
         "Arles|Gebennensis|Camino de Santiago|Cami[nñ]o de Santiago|Mozárabe|Lebaniego|Invierno|"
         "Catal[aá]n|Levante|Saint-Jacques|Compostelle|"
         "Caminho (de Santiago|Portugu|Central|Interior|da Costa|Nascente|Torres)|Cami[nñ]o Portugu|Xacob|Via da Prata|"
         "Augusta|Salvador|Vadiniense|Cam[ií] (de Sant Jaume|Jacobeu|Ignasi)|Sant Jaume|Jacobeu|V[eé]zelay|"
         "Voie (de V[eé]zelay|de Tours|du Pi[eé]mont|des Pi[eé]monts|de Soulac|de Gen[eè]ve|du Puy|d.Arles|de Rocamadour|du Littoral|Limousine|Tolosane|Podiense)|"
         "Pi[eé]mont Pyr[eé]n|Soulac|Rocamadour|"
         "Jakob|Jacobus|Jacobi|Sint-Jacob|St\\.? James|Saint James")   # Germania, Austria, Svizzera, Inghilterra: il nome non contiene "Santiago"
BROAD =("Cami[nñ]o|Caminho|Chemin de (Saint|Compostelle)|Saint-Jacques|"
         "Via (de la Plata|Podiensis|Lemovicensis|Turonensis|Tolosana|Gebennensis)|"
         "Santiago|Compostelle|Fisterr|Finisterre|Mux[ií]a")


DEADLINE = [None]


def overpass(query, tries=2):
    errs = []
    for t in range(tries):
        for ep in ENDPOINTS:
            if DEADLINE[0] and time.time() > DEADLINE[0]:
                raise TimeoutError("tempo massimo raggiunto")
            try:
                req = urllib.request.Request(
                    ep, data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "InCamminoConLaCri-geojson/1.0"})
                with urllib.request.urlopen(req, timeout=100) as r:
                    j = json.load(r)
                # "remark" = il server ha avuto un problema (timeout, memoria): risultato parziale, da scartare
                if "remark" not in j:
                    return j
                errs.append(f"{ep}: {j['remark'][:100]}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{ep}: {e}")
        print(f"  tentativo {t + 1}/{tries} fallito ({errs[-1][:160]}), riprovo…", file=sys.stderr, flush=True)
        time.sleep(8 * (t + 1))
    raise RuntimeError("Overpass non risponde: " + " | ".join(errs[-4:]))


JACOB = "Santiago|Xacob|Jacob|Jakob|Compostel|Jacques|Giacomo|Sant.?Iago"

# gruppi di cammini: (chiave, area "sud,ovest,nord,est", regex nome, filtro network, cerca anche per descrizione)
GROUPS = [
    ("santiago", BBOX, None, None, True),        # regex scelta da --broad/NAMES
    ("santiago", "49.8,-10.7,60.9,2.0", None, None, True),   # Regno Unito e Irlanda
    ("santiago", "45.4,10,55.5,24", None, None, True),       # Europa centrale e orientale (oltre il riquadro principale)
    ("santiago", "51.4,2.0,55.5,10.1", None, None, True),    # Benelux e Germania nord-occidentale (il vuoto tra i riquadri sopra)
    # Via Francigena e Via Romea sono aree a sé (non per paese): la Francigena viene prima, così un
    # cammino che porta entrambi i nomi resta lì
    ("francigena", "39,-1,52.6,19", "Francigena|Via Francigena", None, False),
    ("romea", "40,5,54.5,20", "Via Romea|Romea (Germanica|Strata|Nonantolana|del Chianti)|Romea", None, False),
    ("italia", "36,6.5,47.2,18.8",
     "Cammino|Via (degli Dei|Appia|Traiana|Lauretana|Micaelica|Flaminia|Postumia|Francesca|Sacra|Clodia|Aurelia|Romea|Salaria)|"
     "Sentiero Italia|Sentiero Roma|Francesco|Benedetto|Sant.Antonio|Sant.Agostino|Dante|Assisi|Materano|Celeste|Balteo",
     "iwn|nwn|rwn", False),
]


REFS_FR = "^GR ?(65|653|654|655|78)([^0-9]|$)"   # GR 65 Puy, 653 Arles/Tolosana, 654 Vézelay, 655 Tours, 78 Piémont

STAGE = re.compile(r"[ée]tape|etapa|tappa|stage|etappe|jornada|tramo|secci[oó]n|section|journ[ée]e|d[ií]a \d|\bleg\b", re.I)


def pick_roots(els, rx):
    """Relazioni 'radice'. Una figlia con nome da cammino (non da tappa) resta cammino a sé;
    una relazione 'contenitore' (solo sotto-relazioni, almeno 2 cammini dentro) viene scartata."""
    by = {e["id"]: e for e in els}
    children = {m["ref"] for e in els for m in e.get("members", []) if m["type"] == "relation"}
    try:
        rxc = re.compile(rx, re.I)
    except re.error:
        rxc = None
    promoted = set()
    for cid in children:
        e = by.get(cid)
        name = (e or {}).get("tags", {}).get("name", "")
        if e and rxc and rxc.search(name) and not STAGE.search(name):
            promoted.add(cid)
    out = []
    for e in els:
        if e["id"] in children and e["id"] not in promoted:
            continue
        mem = e.get("members", [])
        kids = [m for m in mem if m["type"] == "relation" and m["ref"] in promoted]
        if len(kids) >= 2 and not any(m["type"] == "way" for m in mem):
            continue   # contenitore di cammini: restano i cammini al suo interno
        out.append(e)
    return out


def list_roots(rx, bbox=BBOX, net=None, jacob=True, refs=None):
    # query leggera (solo tag e membri). Santiago: trova per nome, per descrizione/altri nomi
    # (collegamenti, varianti, enlaces) e per sigla dei GR francesi.
    netf = f'["network"~"^({net})$"]' if net else ""
    extra = (f'\n  rel["type"="route"]["route"~"^(hiking|foot)$"]{netf}'
             f'[~"^(description|alt_name|official_name|old_name|name:.*|ref:name|destination|from|to)$"~"{JACOB}"]({bbox});') if jacob else ""
    if refs:
        extra += f'\n  rel["type"="route"]["route"~"^(hiking|foot)$"]["ref"~"{refs}"]({bbox});'
    q = f"""[out:json][timeout:120];
(
  rel["type"="route"]["route"~"^(hiking|foot)$"]{netf}["name"~"{rx}",i]({bbox});{extra}
);
out body;"""
    return pick_roots(overpass(q)["elements"], rx)


def fetch_geom(ids):
    q = (f"[out:json][timeout:90];rel(id:{','.join(map(str, ids))})->.p;"
         "rel(r.p)->.a;rel(r.a)->.b;rel(r.b)->.c;(.p;.a;.b;.c;);out geom;")
    return overpass(q)["elements"]


def ways_of(parent, by_id):
    """Tutte le linee della relazione e di tutte le sue sotto-relazioni (a qualsiasi livello), senza duplicati."""
    group, stack, used = [], [parent], set()
    while stack:
        rel = stack.pop()
        if rel["id"] in used:
            continue
        used.add(rel["id"])
        group.append(rel)
        for m in rel.get("members", []):
            if m["type"] == "relation" and m["ref"] in by_id:
                stack.append(by_id[m["ref"]])
    out, seen = [], set()
    for rel in group:
        for m in rel.get("members", []):
            if m["type"] != "way" or not m.get("geometry"):
                continue
            pts = []
            for p in m["geometry"]:
                if not p:
                    continue
                c = [round(p["lon"], 5), round(p["lat"], 5)]
                if not pts or pts[-1] != c:
                    pts.append(c)
            if len(pts) < 2:
                continue
            key = tuple(map(tuple, pts))
            if key in seen or key[::-1] in seen:
                continue
            seen.add(key)
            out.append(pts)
    return out


def make_feature(parent, by_id, group="santiago"):
    lines = ways_of(parent, by_id)
    if not lines:
        return None
    t = parent.get("tags", {})
    return {
        "type": "Feature",
        "properties": {
            "id": parent["id"], "name": t.get("name", f"relazione {parent['id']}"),
            "ref": t.get("ref", ""), "from": t.get("from", ""), "to": t.get("to", ""),
            "network": t.get("network", ""), "v": 2, "group": group,
        },
        "geometry": {"type": "MultiLineString", "coordinates": lines},
    }


LABELS = {
    "spagna": "Spagna",
    "italia": "Italia",
    "portogallo": "Portogallo",
    "francia": "Francia",
    "svizzera": "Svizzera",
    "inghilterra": "Inghilterra",
    "germania": "Germania",
    "austria": "Austria",
    "francigena": "Via Francigena",
    "romea": "Via Romea",
    "europa": "Altri paesi",
}
THEMES = ("francigena", "romea")   # aree per cammino, non per paese: contano il gruppo e non i contorni

# Contorni GROSSOLANI dei paesi, (lon, lat): servono solo a capire in che paese cade un cammino.
# Fuori da tutti i contorni = "europa" (Galles, Scozia, Irlanda, Belgio, Olanda, ecc.).
# Dove i contorni si sovrappongono vale l'ordine dell'elenco: vince il primo.
# Il paese di un cammino è quello in cui cade la maggior parte dei suoi punti.
COUNTRIES = [
    ("portogallo", [[(-9.5, 37.0), (-7.4, 37.2), (-7.4, 38.2), (-6.9, 39.0), (-7.2, 39.7), (-6.8, 40.3), (-6.9, 41.0),
                     (-6.2, 41.6), (-6.6, 41.9), (-7.2, 41.9), (-8.1, 42.1), (-8.85, 41.87), (-9.0, 41.0), (-9.5, 38.7)]]),
    ("spagna", [[(-9.3, 43.2), (-8.0, 43.8), (-1.8, 43.4), (-1.4, 43.0), (0.7, 42.8), (1.7, 42.5), (3.2, 42.4), (3.4, 41.8),
                 (0.9, 41.0), (-0.3, 39.5), (-0.7, 38.2), (-1.9, 36.9), (-4.4, 36.6), (-5.6, 36.0), (-6.4, 36.8), (-7.4, 37.2),
                 (-7.4, 38.2), (-6.9, 39.0), (-7.2, 39.7), (-6.8, 40.3), (-6.9, 41.0), (-6.2, 41.6), (-6.6, 41.9), (-7.2, 41.9),
                 (-8.1, 42.1), (-8.85, 41.87), (-9.3, 43.0)],
                [(1.1, 38.6), (4.4, 38.6), (4.4, 40.2), (1.1, 40.2)]]),          # Baleari
    ("francia", [[(-5.1, 48.4), (-1.6, 49.7), (2.5, 51.1), (4.2, 50.0), (6.4, 49.5), (8.2, 49.0), (7.6, 47.6), (6.9, 47.35),
                  (6.0, 46.2), (7.0, 45.9), (7.0, 44.4), (7.7, 43.8), (6.0, 43.1), (3.1, 43.2), (3.2, 42.4), (1.7, 42.5),
                  (0.7, 42.8), (-1.4, 43.0), (-1.8, 43.4), (-1.2, 45.6), (-2.2, 47.2), (-4.5, 47.8)],
                 [(8.5, 41.3), (9.7, 41.3), (9.7, 43.1), (8.5, 43.1)]]),          # Corsica
    ("svizzera", [[(6.0, 46.2), (6.9, 47.35), (7.6, 47.6), (8.6, 47.8), (9.6, 47.5), (10.5, 46.9), (10.4, 46.5), (9.0, 45.8),
                   (8.4, 46.1), (7.0, 45.9)]]),
    ("italia", [[(7.0, 44.1), (7.0, 45.9), (8.4, 46.1), (9.0, 45.8), (10.4, 46.5), (12.2, 47.1), (13.8, 46.6), (13.9, 45.6),
                 (13.6, 44.5), (18.7, 40.0), (18.0, 39.7), (17.2, 38.5), (16.0, 37.8), (15.6, 38.3), (15.0, 40.0), (12.0, 41.5),
                 (10.2, 43.0), (8.8, 44.1)],
                [(12.3, 36.6), (15.7, 36.6), (15.7, 38.3), (12.3, 38.3)],          # Sicilia
                [(8.1, 38.8), (9.9, 38.8), (9.9, 41.3), (8.1, 41.3)]]),            # Sardegna
    ("austria", [[(9.53, 47.05), (9.62, 47.55), (10.45, 47.55), (10.9, 47.5), (11.6, 47.58), (12.2, 47.7), (12.8, 47.65),
                  (13.05, 47.75), (12.95, 48.1), (13.45, 48.35), (13.84, 48.77), (14.7, 48.6), (15.0, 49.0), (16.0, 48.75),
                  (16.95, 48.6), (17.1, 48.0), (17.1, 47.7), (16.1, 46.85), (15.7, 46.7), (15.0, 46.65), (14.5, 46.4),
                  (13.7, 46.5), (12.4, 46.7), (12.2, 47.1), (11.5, 47.0), (10.9, 46.77), (10.45, 46.85), (10.15, 46.9)]]),
    ("germania", [[(6.0, 50.8), (6.0, 51.9), (6.7, 52.5), (7.2, 53.25), (8.0, 53.7), (8.9, 54.0), (8.6, 54.9), (9.4, 54.85),
                   (10.0, 54.45), (10.9, 54.0), (12.0, 54.2), (13.8, 54.1), (14.2, 53.9), (14.4, 53.3), (14.55, 52.6),
                   (14.75, 52.1), (15.0, 51.55), (14.95, 51.0), (14.3, 50.9), (13.5, 50.7), (12.2, 50.3), (12.5, 50.1),
                   (13.0, 49.9), (12.4, 49.6), (13.2, 49.1), (13.84, 48.77), (13.45, 48.35), (12.95, 48.1), (13.05, 47.75),
                   (12.8, 47.65), (12.2, 47.7), (11.6, 47.58), (10.9, 47.5), (10.45, 47.55), (9.62, 47.55), (9.2, 47.65),
                   (8.6, 47.65), (7.55, 47.58), (7.58, 48.0), (7.8, 48.6), (8.2, 49.0), (7.3, 49.15), (6.4, 49.45),
                   (6.35, 49.85), (6.2, 50.5)]]),
    # solo l'Inghilterra (Galles e Scozia restano in "Altri paesi")
    ("inghilterra", [[(-5.7, 50.1), (-4.2, 50.3), (-3.5, 50.2), (-1.9, 50.6), (-0.8, 50.75), (0.9, 50.85), (1.4, 51.15),
                      (1.5, 51.4), (1.7, 52.5), (1.2, 52.95), (0.4, 53.0), (0.1, 53.5), (-0.1, 54.1), (-0.7, 54.5),
                      (-1.2, 54.7), (-1.6, 55.3), (-2.0, 55.8), (-2.3, 55.3), (-3.0, 55.0), (-3.2, 54.95), (-3.5, 54.5),
                      (-3.3, 54.1), (-3.0, 53.8), (-3.1, 53.4), (-2.95, 53.0), (-3.0, 52.5), (-3.0, 52.0), (-2.7, 51.6),
                      (-2.5, 51.4), (-3.0, 51.2), (-3.6, 51.2), (-4.2, 51.2), (-4.6, 50.9)]]),
]


def _inside(poly, x, y):
    ins = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def country_of(lon, lat):
    for key, polys in COUNTRIES:
        if any(_inside(p, lon, lat) for p in polys):
            return key
    return "europa"


def area_of(f):
    """Area (= file) di un cammino: Via Francigena e Via Romea per gruppo, gli altri per paese
    (dove cade la maggior parte dei loro punti)."""
    if f["properties"].get("group") in THEMES:
        return f["properties"]["group"]
    pts =[c for line in f["geometry"]["coordinates"] for c in line]
    step = max(1, len(pts) // 80)
    votes = {}
    for lon, lat in pts[::step]:
        k = country_of(lon, lat)
        votes[k] = votes.get(k, 0) + 1
    # a parità di voti vale l'ordine dell'elenco LABELS
    return max(LABELS, key=lambda k: votes.get(k, 0))


def save(outdir, feats):
    """Scrive un file per area (data/<area>.geojson) e l'indice data/indice.json."""
    os.makedirs(outdir, exist_ok=True)
    by_area = {}
    for f in feats.values():
        f["properties"]["area"] = area_of(f)
        by_area.setdefault(f["properties"]["area"], []).append(f)
    today = datetime.date.today().isoformat()
    index, written = [], set()
    for key in LABELS:
        fl = sorted(by_area.get(key, []), key=lambda f: f["properties"]["name"])
        if not fl:
            continue
        path = os.path.join(outdir, key + ".geojson")
        doc = {"type": "FeatureCollection", "generated": today,
               "attribution": "© contributori di OpenStreetMap (ODbL)", "features": fl}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
        written.add(os.path.abspath(path))
        index.append({"key": key, "label": LABELS[key], "file": key + ".geojson",
                      "count": len(fl), "mb": round(os.path.getsize(path) / 1e6, 2)})
    with open(os.path.join(outdir, "indice.json"), "w", encoding="utf-8") as fh:
        json.dump({"generated": today, "areas": index}, fh, ensure_ascii=False, indent=1)
    # si toglie solo il vecchio file unico: i file delle aree non si cancellano mai da qui,
    # altrimenti una ricerca andata male farebbe sparire un'intera area dal sito
    legacy = os.path.abspath(os.path.join(outdir, "cammini.geojson"))
    if os.path.exists(legacy) and legacy not in written:
        os.remove(legacy)
    return sum(e["mb"] for e in index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broad", action="store_true", help="ricerca larga (molte relazioni)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data"),
                    help="cartella dei dati (un file .geojson per area)")
    ap.add_argument("--max-minutes", type=float, default=35, help="tempo massimo; poi salva quanto scaricato")
    ap.add_argument("--ids", default="", help="ID di relazioni OSM da aggiungere (separati da virgola)")
    ap.add_argument("--fresh", action="store_true", help="ignora il file esistente e riscarica tutto")
    a = ap.parse_args()
    DEADLINE[0] = time.time() + a.max_minutes * 60

    # riprende dai file esistenti: i cammini già presenti non si riscaricano
    feats = {}
    absorbed = []   # file letti per intero (servono a togliere i file delle vecchie aree a fine lavoro)
    if not a.fresh:
        for p in glob.glob(os.path.join(a.out, "*.geojson")):
            try:
                for f in json.load(open(p, encoding="utf-8"))["features"]:
                    feats[f["properties"]["id"]] = f
                absorbed.append(p)
            except Exception as e:  # noqa: BLE001
                print(f"File {p} non leggibile, lo ignoro: {e}", flush=True)
        if feats:
            print(f"Riprendo: {len(feats)} cammini già nei file", flush=True)

    print("Cerco le relazioni…", flush=True)
    roots, grp_of = [], {}
    failed = []   # gruppi la cui ricerca è fallita: i loro cammini già salvati NON vanno toccati
    for key, bbox, rx, net, jac in GROUPS:
        rx = rx or (BROAD if a.broad else NAMES)
        try:
            found = list_roots(rx, bbox, net, jac, REFS_FR if key == "santiago" else None)
        except TimeoutError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"  gruppo {key}: ricerca fallita ({str(e)[:100]}), salto", flush=True)
            failed.append(f"{key} {bbox}")
            continue
        new = [e for e in found if e["id"] not in grp_of]
        for e in new:
            grp_of[e["id"]] = key
        roots += new
        print(f"  {key}: {len(new)} relazioni radice", flush=True)
    for x in re.findall(r"\d+", a.ids or ""):
        rid = int(x)
        if rid not in grp_of:
            grp_of[rid] = "santiago"
            roots.append({"id": rid, "tags": {"network": "iwn"}})
    prio = {"iwn": 0, "nwn": 1, "rwn": 2}   # prima i percorsi a lunga distanza
    gprio = {"santiago": 0, "francigena": 1, "romea": 2, "italia": 3}
    # un cammino già scaricato che cambia solo gruppo (es. dalla Francigena alla Via Romea) non si riscarica:
    # basta cambiargli l'etichetta
    for i in grp_of:
        f = feats.get(i)
        if f and f["properties"].get("v") == 2 and f["properties"].get("group") != grp_of[i]:
            f["properties"]["group"] = grp_of[i]
    roots.sort(key=lambda e: (gprio[grp_of[e["id"]]], prio.get(e.get("tags", {}).get("network"), 3), e["id"]))
    ids = [e["id"] for e in roots]
    todo = [i for i in ids if i not in feats or feats[i]["properties"].get("v") != 2
            or feats[i]["properties"].get("group") != grp_of[i]]
    print(f"  trovate {len(ids)} relazioni radice, da scaricare {len(todo)}", flush=True)

    skipped = []
    timed_out = False   # True se il tempo massimo è finito prima di scaricare tutto
    n = 0
    try:
        for i in range(0, len(todo), 2):
            chunk = todo[i:i + 2]
            print(f"  geometrie {min(i + 2, len(todo))}/{len(todo)}", flush=True)
            try:
                els = fetch_geom(chunk)
            except TimeoutError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"  gruppo fallito ({str(e)[:100]}), provo uno alla volta", flush=True)
                els = []
                for rid in chunk:
                    try:
                        els += fetch_geom([rid])
                    except TimeoutError:
                        raise
                    except Exception as e2:  # noqa: BLE001
                        skipped.append(rid)
                        print(f"  SALTATO {rid}: {str(e2)[:100]}", flush=True)
            by_id = {e["id"]: e for e in els}
            for rid in chunk:
                if rid in by_id:
                    f = make_feature(by_id[rid], by_id, grp_of[rid])
                    if f:
                        feats[rid] = f
            n += 1
            if n % 10 == 0 and feats:
                print(f"  salvataggio intermedio: {len(feats)} cammini, {save(a.out, feats):.1f} MB", flush=True)
            time.sleep(3)
    except TimeoutError:
        timed_out = True
        print("Tempo massimo raggiunto: salvo quanto scaricato (rilancia per continuare).", flush=True)

    if not feats:
        sys.exit("Nessun cammino scaricato: non scrivo il file.")
    # Si tolgono i cammini che OSM non elenca più SOLO se tutto è andato bene (nessuna ricerca fallita,
    # nessun salto, tempo non scaduto) e se non ne sparirebbe una parte grande: un elenco incompleto
    # restituito da Overpass non deve cancellare cammini buoni.
    idset = set(ids)
    if not skipped and not failed and time.time() < DEADLINE[0] and len(ids) > 20:
        gone = [k for k in feats if k not in idset]
        if len(gone) > 0.2 * len(feats):
            print(f"ATTENZIONE: sparirebbero {len(gone)} cammini su {len(feats)}: elenco di OSM probabilmente "
                  "incompleto, NON li tolgo.", flush=True)
        else:
            feats = {k: v for k, v in feats.items() if k in idset}   # via i residui obsoleti
    mb = save(a.out, feats)
    # i file delle vecchie aree (santiago-spagna, francigena, ...) sono già stati assorbiti in feats e
    # riscritti nelle aree nuove: ora si tolgono, così non restano doppioni nel repository
    for p in absorbed:
        if os.path.basename(p)[:-len(".geojson")] not in LABELS and os.path.exists(p):
            os.remove(p)
            print(f"Tolto il file della vecchia area: {os.path.basename(p)}", flush=True)
    print(f"Scritti i file in {a.out}: {len(feats)} cammini su {len(ids)}, {mb:.1f} MB in totale; saltati {len(skipped)}", flush=True)
    # dice al workflow se il lavoro è finito: se no, lui si rilancia da solo (riprende da quanto già salvato)
    complete = not failed and not skipped and not timed_out
    print("COMPLETO: tutti i cammini sono stati scaricati." if complete
          else "INCOMPLETO: serve un'altra corsa (riprende da dove è arrivato).", flush=True)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"complete={'true' if complete else 'false'}\n")
    if failed:
        # i dati sono salvati (niente è andato perso), ma l'Action diventa rossa così te ne accorgi
        sys.exit(f"Ricerca fallita per i gruppi: {', '.join(failed)}. Rilancia l'Action.")


if __name__ == "__main__":
    main()