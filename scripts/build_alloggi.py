#!/usr/bin/env python3
"""
Scarica da OpenStreetMap le LOCALITÀ e gli ALLOGGI vicino ai tracciati dei cammini (data/<area>.geojson)
e li salva, già pronti per il sito, in:

    data/alloggi/<area>.json     località lungo i cammini di quell'area, con i loro alloggi
    data/alloggi/riepilogo.json  conteggio alloggi per stato → regione → paese (per ostelli.html)
    data/alloggi/indice.json     elenco dei file, conteggi, data
    data/alloggi/_celle.json     copia grezza per riquadri (serve a riprendere e ad aggiornare)

Funziona a riquadri di 0,25° (~25 km): ogni riquadro attraversato da un cammino viene scaricato una volta
e riscaricato solo quando è più vecchio di --vecchio giorni (di default 25). Si può interrompere e riprendere:
alla fine stampa "COMPLETATO" se non resta niente da scaricare.

Regola principale (come per i cammini): NON SI PERDE MAI NIENTE. Un riquadro che non si riesce a scaricare
resta con i dati vecchi; i file vengono scritti per intero e solo alla fine (prima .tmp, poi rinominati).

    python3 scripts/build_alloggi.py                   # riprende / aggiorna
    python3 scripts/build_alloggi.py --max-minutes 20  # si ferma dopo 20 minuti (salvando)
    python3 scripts/build_alloggi.py --offline         # rifà solo i file del sito dalla copia grezza
    python3 scripts/build_alloggi.py --aree spagna,francia

Solo libreria standard di Python. Dati © contributori di OpenStreetMap (ODbL);
confini di stati e regioni: Natural Earth (dominio pubblico).
"""
import argparse, datetime, glob, json, math, os, re, socket, sys, time, urllib.parse, urllib.request

_gai = socket.getaddrinfo   # i runner di GitHub spesso non hanno IPv6: forza IPv4
socket.getaddrinfo = lambda h, p, f=0, *a, **k: _gai(h, p, socket.AF_INET, *a, **k)

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "alloggi")
CACHE = os.path.join(OUT, "_celle.json")
TODAY = datetime.date.today()
CELL = 0.25          # lato del riquadro scaricato (gradi)
SPACING = 0.25       # km tra i punti del tracciato tenuti per le distanze
LODGE_KM = 1.5       # un alloggio conta se è entro questa distanza da un cammino
PLACE_KM = 2.5       # una località conta se è entro questa distanza da un cammino
# raggio entro cui un alloggio viene attribuito a una località, per tipo di località
RADIUS = {"city": 8.0, "town": 4.0, "village": 2.5, "hamlet": 1.5}
PLACE_RX = "^(city|town|village|hamlet)$"
TOURISM = "hostel|guest_house|camp_site|alpine_hut|wilderness_hut|hotel|motel|apartment|chalet"
NAME_RX = ("^(Albergue|Alberg|G[iî]te|Ostello|Refugio|Refuge|Herberge|Pilgerherberge|Accoglienza|"
           "Accueil|Casa del Pellegrino|Casa de Peregrinos)")
PILGRIM = re.compile(r"albergue|alberg|g[iî]te|pellegrin|peregrin|pilgr|p[eè]lerin|jacquai|herberge|"
                     r"accoglienza|accueil|refugio|donativo|camino|cammino", re.I)
KINDS = {"P": "Accoglienza pellegrini", "O": "Ostello", "R": "Rifugio", "B": "Pensione / B&B",
         "H": "Hotel", "C": "Campeggio"}
KEEP_TAGS = ("name", "tourism", "phone", "contact:phone", "mobile", "contact:mobile", "website", "contact:website",
             "url", "email", "contact:email", "beds", "capacity", "opening_hours", "fee", "charge", "description",
             "addr:street", "addr:housenumber", "addr:city", "booking", "reservation", "pilgrim", "seasonal")
DEADLINE = [None]


# ------------------------------------------------------------------ utilità

def hav(la1, lo1, la2, lo2):
    r = math.pi / 180
    a = math.sin((la2 - la1) * r / 2) ** 2 + math.cos(la1 * r) * math.cos(la2 * r) * math.sin((lo2 - lo1) * r / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(a)))


def write_json(path, obj, pretty=False):
    """Scrittura sicura: file temporaneo + controllo + rinomina."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        else:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    with open(tmp, encoding="utf-8") as f:
        json.load(f)   # se non si rilegge, non sostituisce il file buono
    os.replace(tmp, path)


def cell_key(la, lo):
    return f"{math.floor(la / CELL) * CELL:.2f},{math.floor(lo / CELL) * CELL:.2f}"


def overpass(query, tries=2):
    errs = []
    for t in range(tries):
        for ep in ENDPOINTS:
            if DEADLINE[0] and time.time() > DEADLINE[0]:
                raise TimeoutError("tempo massimo raggiunto")
            try:
                req = urllib.request.Request(ep, data=urllib.parse.urlencode({"data": query}).encode(),
                                             headers={"User-Agent": "InCamminoConLaCri-alloggi/1.0"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    j = json.load(r)
                if "remark" not in j:      # "remark" = risultato parziale o errore del server: si scarta
                    return j
                errs.append(f"{ep}: {j['remark'][:100]}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{ep}: {e}")
        print(f"  tentativo {t + 1}/{tries} fallito ({errs[-1][:160]}), riprovo…", file=sys.stderr, flush=True)
        time.sleep(8 * (t + 1))
    raise RuntimeError("Overpass non risponde: " + " | ".join(errs[-4:]))


# ------------------------------------------------------------------ tracciati

class Corridor:
    """Punti dei tracciati (uno ogni ~250 m) con indice a griglia, per misurare le distanze in fretta."""
    G = 0.05

    def __init__(self):
        self.grid = {}
        self.n = 0

    def add_line(self, coords):
        last = None
        for lo, la in coords:
            if last and hav(last[0], last[1], la, lo) < SPACING:
                continue
            last = (la, lo)
            self.grid.setdefault((int(la // self.G), int(lo // self.G)), []).append((la, lo))
            self.n += 1
        if coords and last != (coords[-1][1], coords[-1][0]):
            lo, la = coords[-1]
            self.grid.setdefault((int(la // self.G), int(lo // self.G)), []).append((la, lo))

    def dist(self, la, lo, limit):
        gi, gj = int(la // self.G), int(lo // self.G)
        best = 1e9
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for p in self.grid.get((gi + di, gj + dj), ()):
                    if abs(p[0] - la) * 111 > limit or abs(p[1] - lo) * 80 > limit + 30:
                        continue
                    d = hav(la, lo, p[0], p[1])
                    if d < best:
                        best = d
        return best

    def cells(self):
        """Riquadri da scaricare: quelli toccati dai tracciati più i vicini quando il cammino passa vicino al bordo."""
        m = PLACE_KM / 111 + 0.01
        out = set()
        for pts in self.grid.values():
            for la, lo in pts:
                for dla in (-m, 0, m):
                    for dlo in (-m * 1.5, 0, m * 1.5):
                        out.add(cell_key(la + dla, lo + dlo))
        return out


def load_areas(only):
    idx = json.load(open(os.path.join(DATA, "indice.json"), encoding="utf-8"))
    areas = {}
    for e in idx["areas"]:
        if only and e["key"] not in only:
            continue
        path = os.path.join(DATA, e["file"])
        if not os.path.exists(path):
            print(f"  (manca {e['file']}, salto)", flush=True)
            continue
        c = Corridor()
        for f in json.load(open(path, encoding="utf-8"))["features"]:
            g = f.get("geometry") or {}
            lines = g.get("coordinates", []) if g.get("type") == "MultiLineString" else [g.get("coordinates", [])]
            for l in lines:
                if l:
                    c.add_line(l)
        areas[e["key"]] = {"label": e.get("label", e["key"]), "corr": c}
        print(f"  {e['key']}: {c.n} punti di tracciato", flush=True)
    return areas


# ------------------------------------------------------------------ download di un riquadro

def fetch_cell(key, corr):
    """Scarica un riquadro e tiene solo ciò che serve: alloggi vicino ai cammini e le località a cui
    possono appartenere (così la copia grezza resta piccola)."""
    s, w = map(float, key.split(","))
    bb = f"{s:.2f},{w:.2f},{s + CELL:.2f},{w + CELL:.2f}"
    q = f"""[out:json][timeout:110];
node["place"~"{PLACE_RX}"]["name"]({bb})->.p;
(nwr["tourism"~"^({TOURISM})$"]({bb});
 nwr["name"~"{NAME_RX}",i]["tourism"]({bb});
 nwr["name"~"{NAME_RX}",i]["building"]({bb});
 nwr["name"~"{NAME_RX}",i]["amenity"~"^(shelter|social_facility|place_of_worship)$"]({bb});)->.l;
.p out tags;
.l out tags center;"""
    j = overpass(q)
    places, lodging = [], []
    for e in j.get("elements", []):
        t = e.get("tags") or {}
        if e["type"] == "node" and "place" in t and "tourism" not in t:
            far = RADIUS.get(t["place"], 1.5) + LODGE_KM
            if corr.dist(e["lat"], e["lon"], far) > max(far, PLACE_KM):
                continue
            places.append([e["id"], t.get("name", ""), t["place"], round(e["lat"], 5), round(e["lon"], 5),
                           t.get("name:it") or ""])
            continue
        c = e if e["type"] == "node" else e.get("center")
        if not c:
            continue
        if t.get("amenity") in ("restaurant", "bar", "cafe", "fast_food"):
            continue
        if corr.dist(c["lat"], c["lon"], LODGE_KM) > LODGE_KM:
            continue
        lodging.append([e["type"][0] + str(e["id"]), {k: t[k] for k in KEEP_TAGS if k in t},
                        round(c["lat"], 5), round(c["lon"], 5)])
    return {"d": TODAY.isoformat(), "p": places, "l": lodging}


# ------------------------------------------------------------------ stati e regioni

class Regions:
    def __init__(self):
        path = os.path.join(ROOT, "scripts", "regioni-europa.json")
        self.aree = json.load(open(path, encoding="utf-8"))["aree"] if os.path.exists(path) else []
        self.memo = {}

    @staticmethod
    def inside(x, y, ring):
        res, j = False, len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                res = not res
            j = i
        return res

    def find(self, la, lo):
        k = (round(la, 2), round(lo, 2))
        if k in self.memo:
            return self.memo[k]
        hit = None
        for a in self.aree:
            b = a["bbox"]
            if b[0] <= lo <= b[2] and b[1] <= la <= b[3] and any(self.inside(lo, la, r) for r in a["rings"]):
                hit = a
                break
        if not hit:   # sulla costa o su un confine semplificato: l'area più vicina (entro ~5 km)
            best = 0.05
            for a in self.aree:
                b = a["bbox"]
                if b[0] - best <= lo <= b[2] + best and b[1] - best <= la <= b[3] + best:
                    for r in a["rings"]:
                        for x, y in r[::2]:
                            d = abs(x - lo) + abs(y - la)
                            if d < best:
                                best, hit = d, a
        res = (hit["cc"], hit["stato"], hit["regione"], hit["provincia"]) if hit else ("", "", "", "")
        self.memo[k] = res
        return res


# ------------------------------------------------------------------ classificazione

def kind_of(t):
    tour = t.get("tourism", "")
    text = " ".join(t.get(k, "") for k in ("name", "description", "pilgrim"))
    if tour in ("alpine_hut", "wilderness_hut"):
        return "R"
    if tour == "camp_site":
        return "C"
    if PILGRIM.search(text) or t.get("pilgrim") in ("yes", "only") or t.get("fee") == "donation":
        return "P"
    if tour == "hostel":
        return "O"
    if tour in ("hotel", "motel"):
        return "H"
    if tour in ("guest_house", "apartment", "chalet"):
        return "B"
    return "P" if re.search(NAME_RX, t.get("name", ""), re.I) else "B"


def lodging_record(oid, t, la, lo):
    g = lambda *ks: next((t[k] for k in ks if t.get(k)), "")   # noqa: E731
    r = {"id": oid, "n": t.get("name") or "", "k": kind_of(t), "la": la, "lo": lo}
    for key, ks in (("tel", ("phone", "contact:phone", "mobile", "contact:mobile")),
                    ("web", ("website", "contact:website", "url", "booking")), ("mail", ("email", "contact:email")),
                    ("letti", ("beds", "capacity")), ("orari", ("opening_hours", "seasonal")),
                    ("prezzo", ("charge", "fee")),
                    ("ind", ("addr:street",))):
        v = g(*ks)
        if v:
            r[key] = v[:200]
    if r.get("ind") and t.get("addr:housenumber"):
        r["ind"] += " " + t["addr:housenumber"]
    return r


# ------------------------------------------------------------------ costruzione dei file del sito

def build_outputs(areas, cache, regions):
    os.makedirs(OUT, exist_ok=True)
    # tutti i luoghi e alloggi unici (un riquadro e i vicini possono ripetersi)
    P, Lg = {}, {}
    for c in cache.values():
        for p in c["p"]:
            P[p[0]] = p
        for l in c["l"]:
            Lg[l[0]] = l
    places = list(P.values())
    pgrid = {}
    for p in places:
        pgrid.setdefault((int(p[3] // 0.1), int(p[4] // 0.1)), []).append(p)

    def owner(la, lo):
        """Località a cui attribuire un alloggio: la più vicina in proporzione alla sua grandezza."""
        best, score = None, 1e9
        gi, gj = int(la // 0.1), int(lo // 0.1)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for p in pgrid.get((gi + di, gj + dj), ()):
                    d = hav(la, lo, p[3], p[4])
                    rad = RADIUS.get(p[2], 1.5)
                    if d <= rad and d / rad < score:
                        best, score = p, d / rad
        return best

    index = {"generated": TODAY.isoformat(), "fonte": "© contributori di OpenStreetMap (ODbL)",
             "tipi": KINDS, "aree": []}
    summary = {}
    for key, a in areas.items():
        corr = a["corr"]
        lod, by_place = [], {}
        for oid, t, la, lo in Lg.values():
            if corr.dist(la, lo, LODGE_KM) > LODGE_KM:
                continue
            rec = lodging_record(oid, t, la, lo)
            p = owner(la, lo)
            if p:
                by_place.setdefault(p[0], []).append(len(lod))
            lod.append(rec)
        out_places = []
        for p in places:
            if p[0] not in by_place and corr.dist(p[3], p[4], PLACE_KM) > PLACE_KM:
                continue
            cc, stato, reg, prov = regions.find(p[3], p[4])
            ids = by_place.get(p[0], [])
            cnt = {}
            for i in ids:
                cnt[lod[i]["k"]] = cnt.get(lod[i]["k"], 0) + 1
            rec = {"id": p[0], "n": p[1], "t": p[2], "la": p[3], "lo": p[4], "cc": cc, "re": reg, "pr": prov,
                   "a": ids, "c": cnt}
            if p[5] and p[5] != p[1]:
                rec["it"] = p[5]
            out_places.append(rec)
            s = summary.setdefault(p[0], {"id": p[0], "n": p[1], "t": p[2], "la": p[3], "lo": p[4], "cc": cc, "stato": stato,
                                          "re": reg, "pr": prov, "c": cnt, "aree": []})
            if key not in s["aree"]:
                s["aree"].append(key)
            if sum(cnt.values()) > sum(s["c"].values()):
                s["c"] = cnt
        out_places.sort(key=lambda r: (r["cc"], r["re"], r["n"]))
        tot = {}
        for r in lod:
            tot[r["k"]] = tot.get(r["k"], 0) + 1
        fname = f"{key}.json"
        write_json(os.path.join(OUT, fname), {"generated": TODAY.isoformat(), "area": key, "tipi": KINDS,
                                              "luoghi": out_places, "alloggi": lod})
        index["aree"].append({"key": key, "label": a["label"], "file": fname, "luoghi": len(out_places),
                              "con_alloggio": sum(1 for r in out_places if r["a"]), "alloggi": len(lod), "tipi": tot,
                              "mb": round(os.path.getsize(os.path.join(OUT, fname)) / 1e6, 2)})
        print(f"  {key}: {len(out_places)} località ({index['aree'][-1]['con_alloggio']} con alloggio), "
              f"{len(lod)} alloggi {tot}", flush=True)
    rows = sorted(summary.values(), key=lambda r: (r["stato"], r["re"], r["pr"], r["n"]))
    write_json(os.path.join(OUT, "riepilogo.json"), {"generated": TODAY.isoformat(), "tipi": KINDS, "luoghi": rows})
    return index


def merge_index(new):
    """Le aree non ricostruite in questo giro restano nell'indice com'erano."""
    path = os.path.join(OUT, "indice.json")
    if os.path.exists(path):
        old = json.load(open(path, encoding="utf-8"))
        have = {a["key"] for a in new["aree"]}
        new["aree"] += [a for a in old.get("aree", []) if a["key"] not in have
                        and os.path.exists(os.path.join(OUT, a["file"]))]
    return new


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=0)
    ap.add_argument("--vecchio", type=int, default=25, help="riscarica i riquadri più vecchi di N giorni")
    ap.add_argument("--offline", action="store_true", help="non scaricare, rifai solo i file dalla copia grezza")
    ap.add_argument("--aree", default="", help="solo queste aree (separate da virgola)")
    args = ap.parse_args()
    t0 = time.time()
    if args.max_minutes:
        DEADLINE[0] = t0 + args.max_minutes * 60
    only = {a.strip() for a in args.aree.split(",") if a.strip()}

    print("Leggo i tracciati…", flush=True)
    areas = load_areas(set())
    union = Corridor()
    for a in areas.values():
        for k, pts in a["corr"].grid.items():
            union.grid.setdefault(k, []).extend(pts)
    if only:
        areas = {k: v for k, v in areas.items() if k in only}
    if not areas:
        print("ERRORE: nessun tracciato in data/ (lancia prima la Action dei cammini)")
        sys.exit(1)
    os.makedirs(OUT, exist_ok=True)
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    need, prio = set(), {}
    for n, a in enumerate(areas.values()):   # ordine dell'indice: prima Spagna, Portogallo, Francia…
        for k in a["corr"].cells():
            need.add(k)
            prio.setdefault(k, n)
    limit = (TODAY - datetime.timedelta(days=args.vecchio)).isoformat()
    todo = sorted(k for k in need if k not in cache or cache[k]["d"] < limit)
    todo.sort(key=lambda k: (k in cache, prio[k]))   # prima i mai scaricati, area per area
    print(f"Riquadri: {len(need)} in totale, {len(todo)} da scaricare", flush=True)

    done, fails, streak = 0, 0, 0
    if not args.offline:
        for k in todo:
            if DEADLINE[0] and time.time() > DEADLINE[0] - 60:
                print("Tempo quasi finito: salvo e mi fermo.", flush=True)
                break
            try:
                cache[k] = fetch_cell(k, union)
                done += 1
                streak = 0
                if done % 10 == 0:
                    write_json(CACHE, cache)
                    print(f"  {done}/{len(todo)} riquadri ({time.time() - t0:.0f} s)", flush=True)
                time.sleep(1)
            except TimeoutError:
                break
            except Exception as e:  # noqa: BLE001
                fails += 1
                streak += 1
                print(f"  riquadro {k} saltato: {str(e)[:200]}", file=sys.stderr, flush=True)
                if streak >= 8:
                    print("Troppi errori di fila: Overpass sembra fuori uso, salvo e mi fermo.", flush=True)
                    break
        write_json(CACHE, cache)

    print("Costruisco i file del sito…", flush=True)
    index = build_outputs(areas, cache, Regions())
    index["riquadri"] = {"totali": len(need), "scaricati": sum(1 for k in need if k in cache)}
    write_json(os.path.join(OUT, "indice.json"), merge_index(index), pretty=True)
    left = [k for k in need if k not in cache or cache[k]["d"] < limit]
    print(f"Scaricati {done} riquadri, {fails} errori, ne restano {len(left)}.", flush=True)
    if not left:
        print("COMPLETATO")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"ERRORE: {e}", flush=True)
        raise
