#!/usr/bin/env python3
"""
Catalogo dei cammini PER NOME (non per stato), ricavato dai file data/<area>.geojson senza scaricare nulla.

Scrive:
    data/catalogo.json         elenco dei cammini: nome, famiglia, paesi, km, riquadro, file
    data/panoramica.json       tutti i cammini semplificati (solo per vederli sulla mappa all'apertura)
    data/cammini/<slug>.json   un file per cammino con i tracciati completi (si scarica solo quando serve)

I tratti con lo stesso nome (tappe "- 01", "Tappa 3", ", Nevers - Sancoins", Camino/Camiño/Caminho…) diventano
un solo cammino. Alcuni cammini sono composti da relazioni con nomi diversi: vedi COMPOSTI.

    python3 scripts/build_catalogo.py
Solo libreria standard di Python.
"""
import glob, json, math, os, re, sys, unicodedata, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(DATA, "cammini")
TOL = 0.0015          # semplificazione della panoramica (~150 m: basta per vedere i cammini)

AREA_LABEL = {"spagna": "Spagna", "portogallo": "Portogallo", "francia": "Francia", "italia": "Italia",
              "svizzera": "Svizzera", "inghilterra": "Regno Unito", "irlanda": "Irlanda", "germania": "Germania",
              "austria": "Austria", "europa": "Altri paesi"}

# cammini fatti di relazioni con nomi diversi: nome nel catalogo -> nomi delle relazioni che lo compongono
COMPOSTI = {
    "Camino Portugués Interior": ["Caminho Português do Interior", "Camiño Xabrés por Verín"],
    "Camino Sanabrés": ["Camino Sanabrés (Este) / Camiño Xabrés (Leste)", "Camiño Xabrés (Oeste)"],
    "Camino de Levante": ["Camino de Santiago de Levante", "Camino de Levante"],
}

FAMIGLIE = [  # (chiave, etichetta) nell'ordine in cui compaiono nel sito
    ("santiago", "Cammini di Santiago"),
    ("francigena", "Via Francigena"),
    ("romea", "Vie Romee"),
    ("italia", "Cammini italiani"),
    ("isole", "Gran Bretagna e Irlanda"),
]


def base_name(n):
    """Come baseName() in app.js: toglie tappa/tratto dal nome."""
    b = (n or "").strip()
    b = re.sub(r"[\s\-–:,.(]*\b(tappa|tapa|[ée]tape|etapa|stage|etappe|abschnitt|tratto|tramo|section|secci[oó]n|variante)"
               r"\b\.?\s*[\dIVX]+[a-z]?\b.*$", "", b, flags=re.I)
    b = re.sub(r"\s*[-–:]\s*\d+[a-z]?\b.*$", "", b, flags=re.I)
    for sep, rx in ((",", r"\s[-–]\s|\sà\s|\sa\s"), (":", r"[-–]|\sà\s|\sa\s")):
        c = b.find(sep)
        if c > 3 and re.search(rx, b[c:]):
            b = b[:c]
    m = re.search(r"(\S+)\s+\d+[a-z]?$", b, flags=re.I)
    if m and not re.fullmatch(r"GR|GRP|PR|SL|E|HRP|VA", m.group(1), flags=re.I):
        b = b[:m.start()] + m.group(1)
    return b.strip() or (n or "").strip()


def norm_name(n):
    s = unicodedata.normalize("NFD", base_name(n))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"\bcami(n|nh)o\b", "camino", s)
    return re.sub(r"\s+", " ", s).strip()


def slug(s):
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "cammino"


def hav(a, b):
    r = math.pi / 180
    h = math.sin((b[1] - a[1]) * r / 2) ** 2 + math.cos(a[1] * r) * math.cos(b[1] * r) * math.sin((b[0] - a[0]) * r / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def rdp(pts, tol):
    if len(pts) < 3:
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


def _k(p):
    return (round(p[0], 5), round(p[1], 5))


def unique_lines(lines):
    """Toglie i pezzi ripetuti (super-percorso e tappe che si sovrappongono) spezzando in segmenti unici."""
    seen, out = set(), []
    for l in lines:
        cur = []
        for a, b in zip(l, l[1:]):
            ka, kb = _k(a), _k(b)
            if ka == kb:
                continue
            key = (ka, kb) if ka < kb else (kb, ka)
            if key in seen:
                if len(cur) >= 2:
                    out.append(cur)
                cur = []
                continue
            seen.add(key)
            if not cur:
                cur = [a]
            cur.append(b)
        if len(cur) >= 2:
            out.append(cur)
    return out


def merge_lines(lines):
    """Unisce i pezzi che si toccano agli estremi in linee continue (OSM spezza i cammini in migliaia di vie)."""
    ends = {}
    for i, l in enumerate(lines):
        ends.setdefault(_k(l[0]), []).append(i)
        ends.setdefault(_k(l[-1]), []).append(i)
    used = [False] * len(lines)

    def take(pt):
        for j in ends.get(_k(pt), ()):
            if not used[j]:
                used[j] = True
                l = lines[j]
                return l if _k(l[0]) == _k(pt) else l[::-1]
        return None

    out = []
    for i, l in enumerate(lines):
        if used[i]:
            continue
        used[i] = True
        cur = list(l)
        while True:                       # in avanti
            nxt = take(cur[-1])
            if not nxt:
                break
            cur.extend(nxt[1:])
        while True:                       # all'indietro
            prv = take(cur[0])            # parte da cur[0] e va lontano
            if not prv:
                break
            cur = prv[::-1] + cur[1:]
        out.append(cur)
    return out


def famiglia(feats):
    groups = [f["properties"].get("group", "santiago") for f in feats]
    areas = [f["properties"].get("area", "") for f in feats]
    if "romea" in areas:
        return "romea"
    for g in ("francigena", "isole", "italia"):
        if g in groups:
            return g
    if all(a in ("inghilterra", "irlanda") for a in areas):
        return "isole"
    return "santiago"


def write_if_changed(path, obj):
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    try:
        if open(path, encoding="utf-8").read() == text:
            return False
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    json.loads(open(tmp, encoding="utf-8").read())
    os.replace(tmp, path)
    return True


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_geojson import _cc_at, CC_AREA   # confini veri (Natural Earth)

    def paesi_di(f):
        """Paesi attraversati per almeno il 3% del tracciato (o almeno 3 km)."""
        pts = [c for line in f["geometry"]["coordinates"] for c in line]
        step = max(1, len(pts) // 600)
        votes = {}
        for x, y in pts[::step]:
            cc = _cc_at(x, y)
            if cc:
                lab = AREA_LABEL.get(CC_AREA.get(cc, "europa"), "Altri paesi")
                votes[lab] = votes.get(lab, 0) + 1
        tot = sum(votes.values()) or 1
        return {k for k, v in votes.items() if v / tot >= 0.03 or v >= 12}

    feats = []
    for p in sorted(glob.glob(os.path.join(DATA, "*.geojson"))):
        feats += json.load(open(p, encoding="utf-8"))["features"]
    # paesi attraversati da ogni tracciato (Francigena e Romee sono file "per cammino", non per stato)
    for f in feats:
        f["_paesi"] = paesi_di(f) or {AREA_LABEL.get(f["properties"].get("area", ""), "Altri paesi")}

    by = {}
    for f in feats:
        name = f["properties"].get("name", "")
        if not name or re.fullmatch(r"relazione \d+", name):   # senza nome: ognuno per conto suo
            key = f"senza-nome:{f['properties']['id']}"
        else:
            key = norm_name(name)
        by.setdefault(key, []).append(f)
    for nome, parts in COMPOSTI.items():
        got = [norm_name(x) for x in parts if norm_name(x) in by]
        if len(got) >= 2:
            by["composto:" + nome] = [f for k in got for f in by.pop(k)]

    os.makedirs(OUT_DIR, exist_ok=True)
    cat, pano, used = [], [], set()
    for key, fl in by.items():
        if key.startswith("composto:"):
            nome = key[9:]
        elif key.startswith("senza-nome:"):
            p = fl[0]["properties"]
            nome = (f"{p.get('from')} → {p.get('to')}" if p.get("from") and p.get("to") else "Cammino senza nome") \
                + f" (OSM {p['id']})"
        else:
            names = {}
            for f in fl:
                b = base_name(f["properties"].get("name", ""))
                names[b] = names.get(b, 0) + 1
            nome = max(names, key=lambda n: (names[n], -len(n)))
        sl = slug(nome)
        while sl in used:
            sl += "-2"
        used.add(sl)
        lines = unique_lines([l for f in fl for l in f["geometry"]["coordinates"]])
        km = sum(hav(a, b) for l in lines for a, b in zip(l, l[1:]))
        merged = merge_lines(lines)
        simple = []
        for l in merged:
            s = rdp(l, TOL)
            if len(s) >= 2 and not (len(s) == 2 and hav(s[0], s[1]) < 0.1):
                simple.append([[round(x, 4), round(y, 4)] for x, y in s])
        xs = [p[0] for l in merged for p in l]
        ys = [p[1] for l in merged for p in l]
        paesi = sorted(set().union(*[f["_paesi"] for f in fl]))
        for f in fl:
            f.pop("_paesi", None)
        write_if_changed(os.path.join(OUT_DIR, sl + ".json"),
                         {"type": "FeatureCollection", "attribution": "© contributori di OpenStreetMap (ODbL)", "features": fl})
        cat.append({"slug": sl, "nome": nome, "fam": famiglia(fl), "paesi": paesi,
                    "aree": sorted({f["properties"].get("area", "") for f in fl}), "km": round(km), "n": len(fl),
                    "ids": sorted(f["properties"]["id"] for f in fl),
                    "bbox": [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]})
        pano.append({"s": sl, "l": simple})
    for p in glob.glob(os.path.join(OUT_DIR, "*.json")):   # cammini non più presenti
        if os.path.basename(p)[:-5] not in used:
            os.remove(p)
    order = [k for k, _ in FAMIGLIE]
    cat.sort(key=lambda c: (order.index(c["fam"]), -c["km"]))
    today = datetime.date.today().isoformat()
    write_if_changed(os.path.join(DATA, "catalogo.json"),
                     {"generated": today, "famiglie": [{"key": k, "label": v} for k, v in FAMIGLIE], "cammini": cat})
    write_if_changed(os.path.join(DATA, "panoramica.json"), {"generated": today, "cammini": pano})
    mb = os.path.getsize(os.path.join(DATA, "panoramica.json")) / 1e6
    fam_count = {}
    for c in cat:
        fam_count[c["fam"]] = fam_count.get(c["fam"], 0) + 1
    print(f"Catalogo: {len(cat)} cammini da {len(feats)} tracciati; panoramica {mb:.1f} MB; per famiglia {fam_count}")


if __name__ == "__main__":
    main()
