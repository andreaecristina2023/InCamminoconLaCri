#!/usr/bin/env python3
"""
Prepara scripts/regioni-europa.json: confini semplificati di stati, regioni e province (Natural Earth,
dominio pubblico), usati da build_alloggi.py per sapere in che stato/regione sta ogni paese.
Va lanciato solo se si vuole rigenerare il file (di solito mai).

    python3 scripts/prepara_regioni.py [percorso/ne_10m_admin_1_states_provinces.geojson]

Senza argomento scarica il file da GitHub (≈40 MB).
"""
import json, os, sys, urllib.request

URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
       "ne_10m_admin_1_states_provinces.geojson")
BOX = (34.0, -11.5, 61.0, 31.0)   # sud, ovest, nord, est: Europa occidentale e centrale
TOL = 0.004                       # semplificazione (gradi, ~400 m)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regioni-europa.json")


def rdp(pts, tol):
    """Douglas-Peucker iterativo."""
    if len(pts) < 4:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        n2 = dx * dx + dy * dy
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            x, y = pts[i]
            if n2 == 0:
                d = (x - x1) ** 2 + (y - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / n2))
                d = (x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2
            if d > best:
                best, bi = d, i
        if bi > 0 and best > tol * tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if src:
        data = json.load(open(src, encoding="utf-8"))
    else:
        print("Scarico Natural Earth…", flush=True)
        with urllib.request.urlopen(URL, timeout=300) as r:
            data = json.load(r)
    out = []
    for f in data["features"]:
        p, g = f["properties"], f["geometry"]
        if not g:
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        rings = []
        for poly in polys:
            ring = poly[0]   # solo il bordo esterno: i "buchi" non servono a questo scopo
            if not any(BOX[1] <= x <= BOX[3] and BOX[0] <= y <= BOX[2] for x, y in ring):
                continue
            r = rdp([(round(x, 4), round(y, 4)) for x, y in ring], TOL)
            if len(r) >= 4:
                rings.append([[round(x, 3), round(y, 3)] for x, y in r])
        if not rings:
            continue
        xs = [x for r in rings for x, _ in r]
        ys = [y for r in rings for _, y in r]
        cc = p.get("iso_a2") or ""
        if cc in ("", "-1"):
            cc = (p.get("adm0_a3") or "")[:2]
        out.append({"cc": cc, "stato": p.get("admin") or "", "regione": p.get("region") or p.get("name") or "",
                    "provincia": p.get("name") or "", "bbox": [min(xs), min(ys), max(xs), max(ys)], "rings": rings})
    json.dump({"fonte": "Natural Earth (dominio pubblico), semplificato", "aree": out},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(out)} aree scritte in {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
