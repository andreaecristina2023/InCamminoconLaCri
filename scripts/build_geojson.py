#!/usr/bin/env python3
"""
Genera data/cammini.geojson con i cammini di Santiago presi da OpenStreetMap (Overpass API).

Uso:
    python3 scripts/build_geojson.py              # cammini principali (consigliato)
    python3 scripts/build_geojson.py --broad      # ricerca larga (molte relazioni)
    python3 scripts/build_geojson.py --out altro.geojson

Solo libreria standard di Python. Dati © contributori di OpenStreetMap (ODbL).
"""
import argparse, datetime, json, os, sys, time, urllib.parse, urllib.request

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
BBOX = "35,-11,51.5,10"   # Penisola iberica + Francia occidentale e meridionale

NAMES = ("Franc[eé]s|Aragon[eé]s|Podiensis|Lemovicensis|Turonensis|Tolosana|del Norte|Primitivo|"
         "Portugu[eé]s|Ingl[eé]s|Fisterr|Finisterre|Mux[ií]a|de la Plata|Sanabr|Baztán|Vasco|Vézelay|"
         "Arles|Gebennensis|Camino de Santiago|Cami[nñ]o de Santiago|Mozárabe|Lebaniego|Invierno|"
         "Catal[aá]n|Levante|Saint-Jacques|Compostelle")
BROAD = ("Cami[nñ]o|Caminho|Chemin de (Saint|Compostelle)|Saint-Jacques|"
         "Via (de la Plata|Podiensis|Lemovicensis|Turonensis|Tolosana|Gebennensis)|"
         "Santiago|Compostelle|Fisterr|Finisterre|Mux[ií]a")


def overpass(query, tries=3):
    errs = []
    for t in range(tries):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    ep, data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "InCamminoConLaCri-geojson/1.0"})
                with urllib.request.urlopen(req, timeout=240) as r:
                    j = json.load(r)
                if j.get("elements") or "remark" not in j:
                    return j
                errs.append(f"{ep}: {j['remark']}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{ep}: {e}")
        print(f"  tentativo {t + 1}/{tries} fallito ({errs[-1][:160]}), riprovo…", file=sys.stderr, flush=True)
        time.sleep(15 * (t + 1))
    raise RuntimeError("Overpass non risponde: " + " | ".join(errs[-4:]))


def list_roots(rx):
    # solo le relazioni "radice": si tolgono quelle che sono tappe/figlie di altre
    q = f"""[out:json][timeout:120];
rel["type"="route"]["route"~"^(hiking|foot)$"]["name"~"{rx}"]({BBOX})->.all;
(.all; - rel(r.all););
out tags;"""
    return overpass(q)["elements"]


def fetch_geom(ids):
    q = (f"[out:json][timeout:240];rel(id:{','.join(map(str, ids))})->.p;"
         "(.p;rel(r.p););out geom;")
    return overpass(q)["elements"]


def ways_of(parent, by_id):
    """Tutte le linee della relazione e delle sue sotto-relazioni, senza duplicati."""
    group = [parent] + [by_id[m["ref"]] for m in parent.get("members", [])
                        if m["type"] == "relation" and m["ref"] in by_id]
    out, seen, used = [], set(), set()
    for rel in group:
        if rel["id"] in used:
            continue
        used.add(rel["id"])
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


def make_feature(parent, by_id):
    lines = ways_of(parent, by_id)
    if not lines:
        return None
    t = parent.get("tags", {})
    return {
        "type": "Feature",
        "properties": {
            "id": parent["id"], "name": t.get("name", f"relazione {parent['id']}"),
            "ref": t.get("ref", ""), "from": t.get("from", ""), "to": t.get("to", ""),
            "network": t.get("network", ""),
        },
        "geometry": {"type": "MultiLineString", "coordinates": lines},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broad", action="store_true", help="ricerca larga (molte relazioni)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data", "cammini.geojson"))
    a = ap.parse_args()

    print("Cerco le relazioni…", flush=True)
    roots = list_roots(BROAD if a.broad else NAMES)
    ids = sorted(e["id"] for e in roots)
    print(f"  trovate {len(ids)} relazioni radice")

    feats = []
    for i in range(0, len(ids), 3):
        chunk = ids[i:i + 3]
        print(f"  geometrie {min(i + 3, len(ids))}/{len(ids)}")
        try:
            els = fetch_geom(chunk)
        except Exception as e:  # noqa: BLE001
            print(f"  gruppo fallito ({str(e)[:120]}), provo uno alla volta", flush=True)
            els = []
            for rid in chunk:
                try:
                    els += fetch_geom([rid])
                except Exception as e2:  # noqa: BLE001
                    print(f"  SALTATO {rid}: {str(e2)[:120]}", flush=True)
                time.sleep(5)
        by_id = {e["id"]: e for e in els}
        for rid in chunk:
            if rid in by_id:
                f = make_feature(by_id[rid], by_id)
                if f:
                    feats.append(f)
        time.sleep(5)

    if not feats:
        sys.exit("Nessun cammino scaricato: non scrivo il file.")
    feats.sort(key=lambda f: f["properties"]["name"])
    doc = {"type": "FeatureCollection",
           "generated": datetime.date.today().isoformat(),
           "attribution": "© contributori di OpenStreetMap (ODbL)",
           "features": feats}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize(a.out) / 1e6
    print(f"Scritto {a.out}: {len(feats)} cammini, {mb:.1f} MB")


if __name__ == "__main__":
    main()