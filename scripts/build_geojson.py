#!/usr/bin/env python3
"""
Genera data/cammini.geojson con i cammini di Santiago presi da OpenStreetMap (Overpass API).

Uso:
    python3 scripts/build_geojson.py              # cammini principali (consigliato)
    python3 scripts/build_geojson.py --broad      # ricerca larga (molte relazioni)
    python3 scripts/build_geojson.py --out altro.geojson

Solo libreria standard di Python. Dati © contributori di OpenStreetMap (ODbL).
"""
import argparse, datetime, json, os, socket, sys, time, urllib.parse, urllib.request

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
         "Portugu[eé]s|Ingl[eé]s|Fisterr|Finisterre|Mux[ií]a|de la Plata|Sanabr|Baztán|Vasco|Vézelay|"
         "Arles|Gebennensis|Camino de Santiago|Cami[nñ]o de Santiago|Mozárabe|Lebaniego|Invierno|"
         "Catal[aá]n|Levante|Saint-Jacques|Compostelle|"
         "Caminho (de Santiago|Portugu|Central|Interior|da Costa|Nascente|Torres)|Cami[nñ]o Portugu|Xacob|Via da Prata")
BROAD = ("Cami[nñ]o|Caminho|Chemin de (Saint|Compostelle)|Saint-Jacques|"
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
    ("francigena", "39,-1,52.6,19",
     "Francigena|Via Romea|Romea (Germanica|Strata|Nonantolana)|Via Francigena", None, False),
    ("italia", "36,6.5,47.2,18.8",
     "Cammino|Via (degli Dei|Appia|Traiana|Lauretana|Micaelica|Flaminia|Postumia|Francesca|Sacra|Clodia|Aurelia|Romea|Salaria)|"
     "Sentiero Italia|Sentiero Roma|Francesco|Benedetto|Sant.Antonio|Sant.Agostino|Dante|Assisi|Materano|Celeste|Balteo",
     "iwn|nwn|rwn", False),
]


def list_roots(rx, bbox=BBOX, net=None, jacob=True):
    # query leggera (solo tag e membri); le relazioni "radice" si distinguono qui:
    # si tolgono quelle che sono membri di un'altra relazione trovata (tappe/figlie).
    # Santiago: trova per nome OPPURE per descrizione/altri nomi (collegamenti, varianti, enlaces)
    netf = f'["network"~"^({net})$"]' if net else ""
    extra = (f'\n  rel["type"="route"]["route"~"^(hiking|foot)$"]{netf}'
             f'[~"^(description|alt_name|official_name|old_name|name:.*|ref:name|destination|from|to)$"~"{JACOB}"]({bbox});') if jacob else ""
    q = f"""[out:json][timeout:120];
(
  rel["type"="route"]["route"~"^(hiking|foot)$"]{netf}["name"~"{rx}"]({bbox});{extra}
);
out body;"""
    els = overpass(q)["elements"]
    children = {m["ref"] for e in els for m in e.get("members", []) if m["type"] == "relation"}
    return [e for e in els if e["id"] not in children]


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


def save(path, feats):
    feats = sorted(feats.values(), key=lambda f: f["properties"]["name"])
    doc = {"type": "FeatureCollection",
           "generated": datetime.date.today().isoformat(),
           "attribution": "© contributori di OpenStreetMap (ODbL)",
           "features": feats}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path) / 1e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broad", action="store_true", help="ricerca larga (molte relazioni)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data", "cammini.geojson"))
    ap.add_argument("--max-minutes", type=float, default=35, help="tempo massimo; poi salva quanto scaricato")
    ap.add_argument("--fresh", action="store_true", help="ignora il file esistente e riscarica tutto")
    a = ap.parse_args()
    DEADLINE[0] = time.time() + a.max_minutes * 60

    # riprende dal file esistente: i cammini già presenti non si riscaricano
    feats = {}
    if os.path.exists(a.out) and not a.fresh:
        try:
            for f in json.load(open(a.out, encoding="utf-8"))["features"]:
                feats[f["properties"]["id"]] = f
            print(f"Riprendo: {len(feats)} cammini già nel file", flush=True)
        except Exception as e:  # noqa: BLE001
            print("File esistente non leggibile, riparto da zero:", e, flush=True)

    print("Cerco le relazioni…", flush=True)
    roots, grp_of = [], {}
    for key, bbox, rx, net, jac in GROUPS:
        rx = rx or (BROAD if a.broad else NAMES)
        try:
            found = list_roots(rx, bbox, net, jac)
        except TimeoutError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"  gruppo {key}: ricerca fallita ({str(e)[:100]}), salto", flush=True)
            continue
        new = [e for e in found if e["id"] not in grp_of]
        for e in new:
            grp_of[e["id"]] = key
        roots += new
        print(f"  {key}: {len(new)} relazioni radice", flush=True)
    prio = {"iwn": 0, "nwn": 1, "rwn": 2}   # prima i percorsi a lunga distanza
    gprio = {"santiago": 0, "francigena": 1, "italia": 2}
    roots.sort(key=lambda e: (gprio[grp_of[e["id"]]], prio.get(e.get("tags", {}).get("network"), 3), e["id"]))
    ids = [e["id"] for e in roots]
    todo = [i for i in ids if i not in feats or feats[i]["properties"].get("v") != 2
            or feats[i]["properties"].get("group") != grp_of[i]]
    print(f"  trovate {len(ids)} relazioni radice, da scaricare {len(todo)}", flush=True)

    skipped = []
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
        print("Tempo massimo raggiunto: salvo quanto scaricato (rilancia per continuare).", flush=True)

    if not feats:
        sys.exit("Nessun cammino scaricato: non scrivo il file.")
    if not skipped and time.time() < DEADLINE[0] and len(ids) > 20:
        feats = {k: v for k, v in feats.items() if k in set(ids)}   # lavoro completo: via i residui obsoleti
    mb = save(a.out, feats)
    print(f"Scritto {a.out}: {len(feats)} cammini su {len(ids)}, {mb:.1f} MB; saltati {len(skipped)}", flush=True)


if __name__ == "__main__":
    main()