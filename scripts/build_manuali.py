#!/usr/bin/env python3
"""
Tratti aggiunti a mano (scripts/tratti_manuali.json): pezzi di cammino che su OpenStreetMap non esistono come percorso.

Per ogni tratto:
  - se c'è il file data/manuali/<gpx>, si usa quel tracciato (es. il GPX ufficiale);
  - altrimenti si calcola con BRouter (https://brouter.de, profilo a piedi) passando dalle tappe indicate,
    cioè su strade e sentieri reali di OSM: è un tracciato APPROSSIMATO.
Il calcolo si rifà solo se le tappe cambiano. Se BRouter non risponde resta la versione precedente.

Scrive data/manuali.json (FeatureCollection), letto da build_catalogo.py e verifica_cammini.py.
    python3 scripts/build_manuali.py
Solo libreria standard di Python.
"""
import glob, hashlib, json, os, socket, sys, time, urllib.request
import xml.etree.ElementTree as ET

_gai = socket.getaddrinfo   # i runner di GitHub spesso non hanno IPv6
socket.getaddrinfo = lambda h, p, f=0, *a, **k: _gai(h, p, socket.AF_INET, *a, **k)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "scripts", "tratti_manuali.json")
OUT = os.path.join(ROOT, "data", "manuali.json")
GPX_DIR = os.path.join(ROOT, "data", "manuali")
BROUTER = "https://brouter.de/brouter?lonlats={}&profile=hiking-mountain&alternativeidx=0&format=geojson"


def gpx_lines(path):
    t = ET.parse(path).getroot()
    lines = []
    for seg in t.iter():
        if seg.tag.endswith("trkseg") or seg.tag.endswith("rte"):
            pts = [[round(float(p.get("lon")), 5), round(float(p.get("lat")), 5)]
                   for p in seg if p.tag.endswith("trkpt") or p.tag.endswith("rtept")]
            if len(pts) > 1:
                lines.append(pts)
    return lines


def brouter(tappe):
    """Un tratto BRouter per ogni coppia di tappe (più affidabile di una richiesta sola)."""
    lines = []
    for a, b in zip(tappe, tappe[1:]):
        url = BROUTER.format(f"{a[2]},{a[1]}|{b[2]},{b[1]}")
        for t in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "InCamminoConLaCri/1.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    j = json.load(r)
                coords = j["features"][0]["geometry"]["coordinates"]
                lines.append([[round(c[0], 5), round(c[1], 5)] for c in coords])
                print(f"    {a[0]} → {b[0]}: {j['features'][0]['properties'].get('track-length', '?')} m", flush=True)
                break
            except Exception as e:  # noqa: BLE001
                print(f"    {a[0]} → {b[0]}: tentativo {t + 1} fallito ({str(e)[:120]})", flush=True)
                time.sleep(5 * (t + 1))
        else:
            raise RuntimeError(f"BRouter non risponde per {a[0]} → {b[0]}")
        time.sleep(1)
    return lines


AUTO_MAX_KM = 30   # i salti veri più corti di così si riempiono da soli con un raccordo BRouter


def auto_tratti():
    """Raccordi automatici per i salti trovati da trova_salti.py (non coperti da altri percorsi, fino a 30 km)."""
    try:
        salti = json.load(open(os.path.join(ROOT, "data", "salti.json"), encoding="utf-8"))["salti"]
    except (OSError, ValueError):
        return []
    out = []
    for s in salti:
        if s.get("coperto") or not (0.6 < s["km_salto"] <= AUTO_MAX_KM) or s.get("gruppo") == "gr":
            continue
        pezzo = s["da"]["pezzo"] or s["a"]["pezzo"]
        if not pezzo:
            continue
        key = f"{s['da']['ll']}{s['a']['ll']}"
        tid = -(100000 + int(hashlib.sha1(key.encode()).hexdigest()[:7], 16))
        out.append({"id": tid, "nome": f"{pezzo} (raccordo)", "unisci_a": pezzo, "group": s.get("gruppo") or "santiago", "auto": True,
                    "fonte": f"Salto di {s['km_salto']} km trovato da trova_salti.py; raccordo approssimato calcolato con BRouter",
                    "tappe": [["inizio del salto", *s["da"]["ll"]], ["fine del salto", *s["a"]["ll"]]]})
    return out


def main():
    spec = json.load(open(SRC, encoding="utf-8"))["tratti"] + auto_tratti()
    try:
        old = {f["properties"]["id"]: f for f in json.load(open(OUT, encoding="utf-8"))["features"]}
    except (OSError, ValueError):
        old = {}
    feats, errors = [], 0
    for t in spec:
        gpx = os.path.join(GPX_DIR, t["gpx"]) if t.get("gpx") else None
        if gpx and os.path.exists(gpx):
            firma = "gpx:" + hashlib.sha1(open(gpx, "rb").read()).hexdigest()[:12]
        else:
            firma = "brouter:" + hashlib.sha1(json.dumps(t["tappe"]).encode()).hexdigest()[:12]
        prev = old.get(t["id"])
        print(f"{t['nome']}:", flush=True)
        if prev and prev["properties"].get("firma") == firma:
            print("  invariato", flush=True)
            feats.append(prev)
            continue
        try:
            if firma.startswith("gpx:"):
                lines, origine = gpx_lines(gpx), "GPX " + t["gpx"]
            else:
                lines, origine = brouter(t["tappe"]), "calcolato con BRouter (approssimato)"
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ERRORE: {e}. Tengo la versione precedente.", flush=True)
            if prev:
                feats.append(prev)
            continue
        feats.append({"type": "Feature",
                      "properties": {"id": t["id"], "name": t["nome"], "group": t.get("group", "santiago"), "auto": bool(t.get("auto")),
                                     "unisci_a": t.get("unisci_a", ""), "fonte": t.get("fonte", ""), "origine": origine,
                                     "manuale": True, "firma": firma, "from": t["tappe"][0][0], "to": t["tappe"][-1][0]},
                      "geometry": {"type": "MultiLineString", "coordinates": lines}})
        print(f"  ok ({origine})", flush=True)
    have = {f["properties"]["id"] for f in feats}
    for k, f in old.items():
        if f["properties"].get("auto") and k not in have:
            feats.append(f)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "attribution": "Tracciati su dati © contributori di OpenStreetMap (ODbL)",
                   "features": feats}, f, ensure_ascii=False, separators=(",", ":"))
    json.load(open(tmp, encoding="utf-8"))
    os.replace(tmp, OUT)
    print(f"{len(feats)} tratti aggiunti a mano in data/manuali.json ({errors} errori)")


if __name__ == "__main__":
    main()
