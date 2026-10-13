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
         "Pi[eé]mont Pyr[eé]n|Soulac|Rocamadour")
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
    "santiago-spagna": "Cammini di Santiago · Spagna",
    "santiago-francia": "Cammini di Santiago · Francia",
    "santiago-portogallo": "Cammini di Santiago · Portogallo",
    "francigena": "Via Francigena",
    "italia": "Cammini in Italia",
}


def area_of(f):
    """File/area geografica di un cammino: i cammini di Santiago si assegnano dal centro del riquadro che li contiene."""
    g = f["properties"].get("group", "santiago")
    if g != "santiago":
        return g
    pts = [c for line in f["geometry"]["coordinates"] for c in line]
    lons, lats = [c[0] for c in pts], [c[1] for c in pts]
    clon, clat = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    if clat > 43.1 and clon > -1.7:
        return "santiago-francia"
    if clon < -6.3 and clat < 42.3:
        return "santiago-portogallo"
    return "santiago-spagna"


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
    for p in glob.glob(os.path.join(outdir, "*.geojson")):   # via i file vecchi (compreso cammini.geojson)
        if os.path.abspath(p) not in written:
            os.remove(p)
    return sum(e["mb"] for e in index)


CAND = "candidati.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broad", action="store_true", help="ricerca larga (molte relazioni)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data"),
                    help="cartella dei dati (un file .geojson per area)")
    ap.add_argument("--max-minutes", type=float, default=25, help="tempo massimo del lotto; poi salva quanto scaricato")
    ap.add_argument("--ids", default="", help="ID di relazioni OSM da aggiungere (separati da virgola)")
    ap.add_argument("--fresh", action="store_true", help="ignora i file esistenti e riscarica tutto")
    ap.add_argument("--refresh", action="store_true", help="rifà la ricerca dei candidati (altrimenti usa data/candidati.json)")
    ap.add_argument("--solo-elenco", action="store_true", help="salva solo l'elenco dei candidati e termina")
    a = ap.parse_args()
    DEADLINE[0] = time.time() + a.max_minutes * 60
    os.makedirs(a.out, exist_ok=True)

    # riprende dai file esistenti: i cammini già presenti non si riscaricano
    feats = {}
    if not a.fresh:
        for p in glob.glob(os.path.join(a.out, "*.geojson")):
            try:
                for f in json.load(open(p, encoding="utf-8"))["features"]:
                    feats[f["properties"]["id"]] = f
            except Exception as e:  # noqa: BLE001
                print(f"File {p} non leggibile, lo ignoro: {e}", flush=True)
        if feats:
            print(f"Riprendo: {len(feats)} cammini già nei file", flush=True)

    # candidati: dal file salvato (se recente) oppure con la ricerca su OSM
    cpath = os.path.join(a.out, CAND)
    roots, grp_of = [], {}
    if os.path.exists(cpath) and not (a.refresh or a.fresh):
        try:
            c = json.load(open(cpath, encoding="utf-8"))
            age = (datetime.date.today() - datetime.date.fromisoformat(c["generated"])).days
            if age <= 30 and c.get("roots"):
                for r in c["roots"]:
                    roots.append({"id": r["id"], "tags": {"name": r.get("name", ""), "network": r.get("network", "")}})
                    grp_of[r["id"]] = r["group"]
                print(f"Uso i {len(roots)} candidati salvati il {c['generated']} (--refresh per rifare la ricerca)", flush=True)
        except Exception as e:  # noqa: BLE001
            print("Elenco candidati non leggibile, rifaccio la ricerca:", e, flush=True)
    if not roots:
        print("Cerco le relazioni…", flush=True)
        for key, bbox, rx, net, jac in GROUPS:
            rx = rx or (BROAD if a.broad else NAMES)
            try:
                found = list_roots(rx, bbox, net, jac, REFS_FR if key == "santiago" else None)
            except TimeoutError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"  gruppo {key}: ricerca fallita ({str(e)[:100]}), salto", flush=True)
                continue
            new = [e for e in found if e["id"] not in grp_of]
            for e in new:
                grp_of[e["id"]] = key
            roots += new
            print(f"  {key}: {len(new)} relazioni radice; esempi: "
                  + "; ".join((e.get("tags", {}).get("name") or str(e["id"]))[:40] for e in new[:8]), flush=True)
        if roots:
            with open(cpath, "w", encoding="utf-8") as fh:
                json.dump({"generated": datetime.date.today().isoformat(),
                           "roots": [{"id": e["id"], "name": e.get("tags", {}).get("name", ""),
                                      "network": e.get("tags", {}).get("network", ""), "group": grp_of[e["id"]]}
                                     for e in roots]}, fh, ensure_ascii=False, separators=(",", ":"))
            print(f"Elenco dei candidati salvato in {cpath}", flush=True)
    if a.solo_elenco:
        print("COMPLETATO (solo elenco)", flush=True)
        return

    for x in re.findall(r"\d+", a.ids or ""):
        rid = int(x)
        if rid not in grp_of:
            grp_of[rid] = "santiago"
            roots.append({"id": rid, "tags": {"network": "iwn"}})
    prio = {"iwn": 0, "nwn": 1, "rwn": 2}   # prima i percorsi a lunga distanza
    gprio = {"santiago": 0, "francigena": 1, "italia": 2}
    roots.sort(key=lambda e: (gprio[grp_of[e["id"]]], prio.get(e.get("tags", {}).get("network"), 3), e["id"]))
    ids = [e["id"] for e in roots]
    todo = [i for i in ids if i not in feats or feats[i]["properties"].get("v") != 2
            or feats[i]["properties"].get("group") != grp_of[i]]
    print(f"  {len(ids)} candidati, già scaricati {len(ids) - len(todo)}, da scaricare {len(todo)}", flush=True)

    skipped = []
    size, i, last_save, timed_out = 4, 0, time.time(), False
    try:
        while i < len(todo):
            chunk = todo[i:i + size]
            print(f"  geometrie {i + len(chunk)}/{len(todo)} (gruppo da {len(chunk)})", flush=True)
            try:
                els = fetch_geom(chunk)
                size = min(size + 1, 8)          # il server risponde: si prova con gruppi più grandi
            except TimeoutError:
                raise
            except Exception as e:  # noqa: BLE001
                size = max(1, size // 2)         # in difficoltà: gruppi più piccoli
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
            i += len(chunk)
            if feats and time.time() - last_save > 120:
                print(f"  salvataggio intermedio: {len(feats)} cammini, {save(a.out, feats):.1f} MB", flush=True)
                last_save = time.time()
            time.sleep(1)
    except TimeoutError:
        timed_out = True
        print("Tempo del lotto finito: salvo quanto scaricato.", flush=True)

    if not feats:
        sys.exit("Nessun cammino scaricato: non scrivo il file.")
    if not timed_out and not skipped and len(ids) > 20:
        feats = {k: v for k, v in feats.items() if k in set(ids)}   # lavoro completo: via i residui obsoleti
    mb = save(a.out, feats)
    print(f"Scritti i file in {a.out}: {len(feats)} cammini su {len(ids)}, {mb:.1f} MB in totale; saltati {len(skipped)}", flush=True)
    print("COMPLETATO" if not timed_out else "INCOMPLETO: servono altri lotti", flush=True)


if __name__ == "__main__":
    main()