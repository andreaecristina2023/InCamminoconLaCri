#!/usr/bin/env python3
"""
Coordinate dei paesi di tappa di Gronze (scripts/tappe_gronze.json) con Nominatim (OpenStreetMap).

- Ogni paese si cerca solo dentro la "zona" del suo cammino (niente omonimi lontani).
- Fra più risultati si sceglie la località (città/paese/frazione) più vicina alle tappe vicine già trovate.
- I risultati si conservano in data/tappe_coord.json: si cercano solo i paesi nuovi (max 1 richiesta al secondo,
  come chiede Nominatim). Un paese non trovato si riprova al giro successivo.

    python3 scripts/geocodifica_tappe.py [--max-minutes 15]
Solo libreria standard di Python.
"""
import argparse, json, math, os, re, socket, sys, time, urllib.parse, urllib.request

_gai = socket.getaddrinfo   # i runner di GitHub spesso non hanno IPv6
socket.getaddrinfo = lambda h, p, f=0, *a, **k: _gai(h, p, socket.AF_INET, *a, **k)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "scripts", "tappe_gronze.json")
OUT = os.path.join(ROOT, "data", "tappe_coord.json")
URL = "https://nominatim.openstreetmap.org/search"
UA = "InCamminoConLaCri/1.0 (https://github.com/andreaecristina2023/InCamminoconLaCri)"
PLACE = {"city": 0, "town": 0, "village": 0, "hamlet": 1, "suburb": 2, "locality": 2, "isolated_dwelling": 2,
         "neighbourhood": 3, "municipality": 1, "administrative": 2}


def query_name(n):
    """'San Clemente (Cuenca)' -> 'San Clemente'; 'Salvatierra/Agurain' -> 'Salvatierra'."""
    n = re.sub(r"\s*\(.*?\)", "", n).strip()
    return n.split("/")[0].strip()


def hav(a, b):
    r = math.pi / 180
    h = math.sin((b[0] - a[0]) * r / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def nominatim(name, zona, cc):
    s, w, n, e = zona[0] - 0.3, zona[1] - 0.3, zona[2] + 0.3, zona[3] + 0.3
    params = {"q": query_name(name), "format": "jsonv2", "limit": 8, "countrycodes": cc,
              "viewbox": f"{w},{n},{e},{s}", "bounded": 1, "accept-language": "es,pt,fr,it,en"}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        res = json.load(r)
    return [{"lat": float(x["lat"]), "lon": float(x["lon"]), "tipo": x.get("addresstype") or x.get("type", ""),
             "nome": x.get("display_name", "")[:120]} for x in res]


def choose_chain(cand_lists):
    """Sceglie un candidato per ogni tappa in modo che il cammino sia il più corto e coerente possibile
    (programmazione dinamica): un omonimo lontano allunga molto il percorso e viene scartato.
    Piccola penalità per i risultati meno rilevanti di Nominatim e per quartieri/località minori."""
    INF = float("inf")
    idx = [i for i, cl in enumerate(cand_lists) if cl]
    res = [None] * len(cand_lists)
    if not idx:
        return res
    pen = lambda r, c: 2.0 * r + 8 * PLACE.get(c["tipo"], 4)   # noqa: E731
    prev_i = idx[0]
    cost = [pen(r, c) for r, c in enumerate(cand_lists[prev_i])]
    back = {prev_i: [None] * len(cost)}
    for i in idx[1:]:
        cur = cand_lists[i]
        nc, nb = [], []
        for r, c in enumerate(cur):
            best, bj = INF, None
            for j, p in enumerate(cand_lists[prev_i]):
                v = cost[j] + hav((p["lat"], p["lon"]), (c["lat"], c["lon"]))
                if v < best:
                    best, bj = v, j
            nc.append(best + pen(r, c))
            nb.append((prev_i, bj))
        cost, back[i], prev_i = nc, nb, i
    j = min(range(len(cost)), key=cost.__getitem__)
    i = prev_i
    while i is not None:
        res[i] = cand_lists[i][j]
        link = back[i][j]
        if link is None:
            break
        i, j = link
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=15)
    a = ap.parse_args()
    stop = time.time() + a.max_minutes * 60
    spec = json.load(open(SRC, encoding="utf-8"))["cammini"]
    try:
        cache = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    cand = cache.setdefault("_candidati", {})
    coords = cache.setdefault("tappe", {})
    asked = 0
    # 1) candidati per ogni paese (una richiesta per paese e zona)
    for c in spec:
        for t in c["tappe"]:
            k = f"{t}|{','.join(map(str, c['zona']))}"
            if k in cand:
                continue
            if time.time() > stop:
                break
            try:
                cand[k] = nominatim(t, c["zona"], c["paesi"])
            except Exception as e:  # noqa: BLE001
                print(f"  {t}: errore ({str(e)[:80]}), riprovo al prossimo giro", flush=True)
                time.sleep(3)
                continue
            asked += 1
            if not cand[k]:
                print(f"  {t} ({c['nome']}): non trovato", flush=True)
            time.sleep(1.1)
    # 2) scelta del candidato giusto, tappa per tappa, guardando le tappe vicine
    missing = 0
    for c in spec:
        z = ",".join(map(str, c["zona"]))
        keys = [f"{t}|{z}" for t in c["tappe"]]
        chosen = choose_chain([cand.get(k, []) for k in keys])
        fix = c.get("coord", {})   # correzioni a mano: {"Paese": [lat, lon]}
        for t, k in zip(c["tappe"], keys):
            if t in fix:
                coords[k] = fix[t]
        for k, ch in zip(keys, chosen):
            if k.split("|")[0] in fix:
                continue
            if ch:
                coords[k] = [round(ch["lat"], 5), round(ch["lon"], 5)]
            else:
                missing += 1
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0)
    os.replace(tmp, OUT)
    tot = sum(len(c["tappe"]) for c in spec)
    print(f"Coordinate tappe: {tot - missing}/{tot} trovate, {asked} nuove richieste a Nominatim")
    if any(f"{t}|{','.join(map(str, c['zona']))}" not in cand for c in spec for t in c["tappe"]):
        print("INCOMPLETO: restano paesi da cercare")


if __name__ == "__main__":
    main()
