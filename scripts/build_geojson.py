#!/usr/bin/env python3
"""
Scarica da OpenStreetMap (Overpass API) i cammini e li salva in data/<area>.geojson + data/indice.json.

Regola principale: lo script NON TOGLIE MAI un cammino già scaricato e non cancella file con dati
che non siano già stati copiati altrove. Se un download fallisce, resta la versione precedente.

Uso:
    python3 scripts/build_geojson.py                    # riprende da dove era rimasto
    python3 scripts/build_geojson.py --refresh          # rifà anche la ricerca dei cammini su OSM
    python3 scripts/build_geojson.py --recupera b329b1c # recupera i cammini persi da un vecchio commit
    python3 scripts/build_geojson.py --ids 123,456      # aggiunge relazioni OSM a mano
    python3 scripts/build_geojson.py --pulisci          # (raro) toglie i cammini non più presenti su OSM

Solo libreria standard di Python. Dati © contributori di OpenStreetMap (ODbL).
"""
import argparse, datetime, glob, json, os, re, socket, subprocess, sys, time, urllib.parse, urllib.request

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
TODAY = datetime.date.today().isoformat()
DEADLINE = [None]
VERSION = 2

# ---------------------------------------------------------------- ricerca dei cammini

NAMES = ("Franc[eé]s|Aragon[eé]s|Podiensis|Lemovicensis|Turonensis|Tolosana|del Norte|Primitivo|"
         "Portugu[eé]s|Ingl[eé]s|Fisterr|Finisterre|Mux[ií]a|de la Plata|Sanabr|Baztán|Vasco|"
         "d.Arles|Arlatensis|Gebennensis|Camino de Santiago|Cami[nñ]o de Santiago|Mozárabe|Lebaniego|Invi?erno|"
         "Catal[aá]n|Levante|Saint-Jacques|Compostelle|"
         "Caminho (de Santiago|Portugu|Central|Interior|da Costa|Nascente|Torres)|Cami[nñ]o Portugu|Xacob|Via da Prata|"
         "Augusta|Salvador|Vadiniense|Cam[ií] (de Sant Jaume|Jacobeu|Ignasi)|Sant Jaume|Jacobeu|V[eé]zelay|"
         "Voie (de V[eé]zelay|de Tours|du Pi[eé]mont|des Pi[eé]monts|de Soulac|de Gen[eè]ve|du Puy|d.Arles|de Rocamadour|du Littoral|Limousine|Tolosane|Podiense)|"
         "Pi[eé]mont Pyr[eé]n|Soulac|Rocamadour|Plantagen|Xabr[eé]s|"
         "[Cc]hemin (du Puy|d.Arles|de V[eé]zelay|de Tours|de Paris|de Saint-Jacques|de Compostelle|de Cluny|de Rocamadour)|"
         "GR[ -]?(65|653|654|655|78)([^0-9]|$)|Via Podiensis|Via Lemovicensis|Via Turonensis|Via Tolosana|Via Gebennensis|"
         "Jacquaire|Jacquet|Saint-Jacques-de-Compostelle")
BROAD = ("Cami[nñ]o|Caminho|Chemin de (Saint|Compostelle)|Saint-Jacques|"
         "Via (de la Plata|Podiensis|Lemovicensis|Turonensis|Tolosana|Gebennensis)|"
         "Santiago|Compostelle|Fisterr|Finisterre|Mux[ií]a")
JACOB = "Santiago|Xacob|Jacob|Jakob|Compostel|Jacques|Giacomo|Sant.?Iago"
NORD = "Jakobs?weg|Jakobusweg|Jakobspilger|Jacobs(pad|route|weg)|Sint.?Jacob|Via (Mosana|Brabantica|Scaldea|Tenera|Monastica|Gallia Belgica|Arduinna)|Via Jacobi|(St\\.?|Saint) ?James|Saint James|Chemin de Saint-Jacques|Camino Ingl"
# cammini di pellegrinaggio di Gran Bretagna e Irlanda (anche Scozia, Galles, Irlanda del Nord)
ISOLE = ("Pilgrim|Camino|(St\\.?|Saint) ?James|Saint James|Way of St|Saints.? Way|(St\\.?|Saint) ?Cuthbert|(St\\.?|Saint) ?Michael|"
         "(St\\.?|Saint) ?Kevin|(St\\.?|Saint) ?Finbarr|(St\\.?|Saint) ?Declan|(St\\.?|Saint) ?Patrick|(St\\.?|Saint) ?Brigid|(St\\.?|Saint) ?Columba|"
         "(St\\.?|Saint) ?Andrews Way|(St\\.?|Saint) ?Hilda|(St\\.?|Saint) ?Oswald|(St\\.?|Saint) ?Ninian|(St\\.?|Saint) ?Conan|(St\\.?|Saint) ?Aidan|(St\\.?|Saint) ?Winefride|"
         "Whithorn|Walsingham|T[oó]char|Cos[aá]n na Naomh|B[oó]thar na Naomh|Lough Derg|Croagh Patrick|"
         "Pererin|Two Saints|Augustine")
# trekking a lunga percorrenza (non di pellegrinaggio), in tutta l'Europa occidentale e centrale
TREK = ("Sentiero Italia|Cammino d.Italia|Alta Via|Grande Traversata|Via Alpina|Tour du Mont.?Blanc|Tour of (the )?Mont Blanc|"
        "Tour (du )?Mont.?Rose|Tour Monte Rosa|Tour des Combins|Haute Route|Grande Escursione Appenninica|Sentiero Frassati|"
        "Selvaggio Blu|Alpe.?Adria|Via Dinarica|Sentiero della Pace|Via degli Abati|Sentiero Azzurro|Via Spluga|"
        "West Highland Way|West Island Way|Great Glen Way|Rob Roy Way|Speyside Way|Southern Upland Way|John Muir Way|"
        "Fife Coastal Path|Cape Wrath|Pennine Way|Coast to Coast|Hadrian.s Wall|Offa.s Dyke|Cotswold Way|South Downs Way|"
        "North Downs Way|Thames Path|South West Coast Path|Wales Coast Path|Pembrokeshire Coast|Cleveland Way|Dales Way|"
        "Cumbria Way|Glynd.r.s Way|Kerry Way|Wicklow Way|Dingle Way|Beara Way|Sheep.s Head Way|Causeway Coast|Ulster Way|"
        "Burren Way|Rota Vicentina|Fisherman.?s Trail|Via Algarviana|Grande Rota|Senda Pirenaica|Transcant[aá]brica|"
        "GR ?(5|10|11|20|7|1|34|92|54|58)( |$|[^0-9])|Kungsleden|Rennsteig|Westweg|Malerweg|Rheinsteig|Eifelsteig|"
        "Rothaarsteig|Hermannsweg|Goldsteig|Walserweg|Via Engiadina|Trans.?Swiss Trail|Alpenpassroute|Jura.?H[oö]henweg")
# trekking da includere sempre
SEMPRE_TREK = [1021025,   # Sentiero Italia CAI (~7.700 km, regioni e tappe annidate)
               16287,     # West Highland Way
               1756633,   # West Island Way (Bute)
               7011030,   # Sentiero Italia - Sardegna (super-percorso, ~585 km)
               9791036,   # Selvaggio Blu (Sardegna)
               7973533,   # Pieterpad (Paesi Bassi, ~500 km)
               1955979,   # Noordzee Kustpad (Paesi Bassi)
               9176775]   # GR 5 Nederland
# cammini italiani da includere sempre (gruppo "italia")
SEMPRE_ITALIA = [11790314,   # Cammino Minerario di Santa Barbara (Sardegna, ~500 km)
                 20203159]   # Cammino di Sant'Antioco (Sardegna)
# nomi da NON considerare cammini anche se la ricerca li trova (es. "King Charles" contiene "arles")
ESCLUDI = re.compile(r"England Coast Path|King Charles III|Wales Coast Path", re.I)
REFS_FR = "^GR[ -]?(65|653|654|655|78)([^0-9]|$)"   # GR 65 Puy, 653 Arles/Tolosana, 654 Vézelay, 655 Tours, 78 Piémont

# unità di ricerca: (nome, gruppo, riquadro "sud,ovest,nord,est", regex nome, filtro network, cerca anche per descrizione, sigle)
SEARCHES = [
    ("santiago", "santiago", "35,-11,51.5,10", None, None, True, REFS_FR),   # regex da --broad/NAMES
    ("santiago-nord", "santiago", "45.5,-6,56,17.5", NORD, None, False, None),
    ("francigena", "francigena", "39,-1,52.6,19",
     "Francigena|Via Romea|Romea (Germanica|Strata|Nonantolana)|Via Francigena", None, False, None),
    ("isole", "isole", "49.8,-11,61,2", ISOLE, None, False, None),
    ("trekking", "trekking", "35,-11,61,20", TREK, "iwn|nwn|rwn", False, None),
    ("italia", "italia", "36,6.5,47.2,18.8",
     "Cammino|Via (degli Dei|Appia|Traiana|Lauretana|Micaelica|Flaminia|Postumia|Francesca|Sacra|Clodia|Aurelia|Romea|Salaria)|"
     "Sentiero Italia|Sentiero Roma|Francesco|Benedetto|Sant.Antonio|Sant.Agostino|Dante|Assisi|Materano|Celeste|Balteo",
     "iwn|nwn|rwn", False, None),
]
# cammini principali da includere sempre, anche se nomi o struttura in OSM sfuggono alla ricerca
# (409737 = "GR-65 - Le chemin du Puy", Via Podiensis)
SEMPRE = [409737,
          2163573,    # Camiño Francés (super-percorso: contiene anche le tappe 02 e 05)
          138227,     # Via Podiensis / Voie du Puy (super-percorso)
          1868399,    # Camiño de Inverno
          19298101,   # Camino Primitivo
          7542876,    # Camiño Xabrés por Verín (Portoghese Interior: Verín -> Laza)
          19001007,   # Camino del Norte (internazionale, intero)
          241329,     # Vía de la Plata (internazionale)
          1106898,    # Voie Limousine / Via Lemovicensis
          6026933,    # Via Turonensis
          389715,     # Voie d'Arles / Via Tolosana
          11813130,   # La voie d'Arles
          14900298,   # Voie de Vézelay (GR 654)
          2289173,    # Via Gebennensis
          7164214,    # Camino de Levante (super-percorso, Castilla y León, GR 239)
          19556718, 19552606, 19552607, 19552806,   # GR 239 tappe 24-27: Arévalo - Medina - Toro - Zamora
          3624753,    # Camino de Santiago de Levante (Valencia - El Toboso)
          15764571,   # St James' Way (Reading - Southampton, verso Santiago)
          7776868,    # Fife Pilgrim Way
          9581839,    # Augustine Camino (Kent)
          4515009,    # Saint Cuthbert's Way (Melrose - Lindisfarne)
          14612088,   # Whithorn Way (Glasgow - Whithorn)
          238293,     # Saint Kevin's Way (Hollywood - Glendalough)
          # Belgio e Paesi Bassi
          151765,     # Via Mosana (Aquisgrana - Liegi - Namur)
          1241530, 18632711,   # Via Brabantica
          18201304,   # Via Scaldea
          18711028,   # Via Tenera
          12614917,   # Via Monastica
          8382432,    # Via Gallia Belgica
          19226888,   # Jacobspad (Paesi Bassi)
          9588884,    # Pelgrimspad deel 1 (Amsterdam - 's-Hertogenbosch), LAW 7
          8446574,    # Pelgrimspad deel 2 ('s-Hertogenbosch - Maastricht - Visé)
          20956421,   # Jacobsweg Amstelredam
          5429547,    # Jabikspaad (Frisia)
          # Francia
          1247178,    # Voie de Soulac
          15471251,   # Voie de Rocamadour
          8375828,    # Voie des Plantagenêts
          20499402,   # Chemin des Plantagenêts
          11959152,   # Chemin de Saint-Jacques via Le Puy (GR 65, intero)
          9204501,    # Via Romea (Germanica) completa, super-percorso: Stade - Austria (Fernpass, Innsbruck) - Roma
          16908565]   # Romea Strata, super-percorso (Tallinn - Roma)
SEARCH_VER = 12   # da aumentare quando cambiano NAMES, SEARCHES o pick_roots

GPRIO = {"santiago": 0, "francigena": 1, "isole": 2, "italia": 2, "trekking": 4}
NPRIO = {"iwn": 0, "nwn": 1, "rwn": 2}   # prima i percorsi a lunga distanza

STAGE = re.compile(r"[ée]tape|etapa|tappa|stage|etappe|jornada|tramo|secci[oó]n|section|journ[ée]e|d[ií]a \d|\bleg\b", re.I)


def overpass(query, tries=2):
    errs = []
    for t in range(tries):
        for ep in ENDPOINTS:
            if DEADLINE[0] and time.time() > DEADLINE[0]:
                raise TimeoutError("tempo massimo raggiunto")
            try:
                req = urllib.request.Request(
                    ep, data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "InCamminoConLaCri-geojson/2.0"})
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
        ref = (e or {}).get("tags", {}).get("ref", "")
        if e and not STAGE.search(name) and ((rxc and rxc.search(name)) or re.search(REFS_FR, ref, re.I)
                                              or re.search(JACOB, name, re.I)):
            promoted.add(cid)
    # un super-percorso che ha anche linee proprie (es. Camiño Francés) è il cammino: le sue figlie non si ripetono
    covered = set()
    for e in els:
        mem = e.get("members", [])
        if e.get("tags", {}).get("type") == "superroute" and any(m["type"] == "way" for m in mem):
            covered |= {m["ref"] for m in mem if m["type"] == "relation"}
    promoted -= covered
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


def list_roots(rx, bbox, net=None, jacob=False, refs=None):
    netf = f'["network"~"^({net})$"]' if net else ""
    extra = (f'\n  rel["type"~"^(route|superroute)$"]["route"~"^(hiking|foot)$"]{netf}'
             f'[~"^(description|alt_name|official_name|old_name|name:.*|ref:name|destination|from|to)$"~"{JACOB}"]({bbox});') if jacob else ""
    if refs:
        extra += f'\n  rel["type"~"^(route|superroute)$"]["route"~"^(hiking|foot)$"]["ref"~"{refs}"]({bbox});'
    q = f"""[out:json][timeout:150];
(
  rel["type"~"^(route|superroute)$"]["route"~"^(hiking|foot)$"]{netf}["name"~"{rx}",i]({bbox});{extra}
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


def make_feature(parent, by_id, group):
    lines = ways_of(parent, by_id)
    if not lines:
        return None
    t = parent.get("tags", {})
    return {
        "type": "Feature",
        "properties": {
            "id": parent["id"], "name": t.get("name", f"relazione {parent['id']}"),
            "ref": t.get("ref", ""), "from": t.get("from", ""), "to": t.get("to", ""),
            "network": t.get("network", ""), "v": VERSION, "group": group, "dl": TODAY,
        },
        "geometry": {"type": "MultiLineString", "coordinates": lines},
    }


# ---------------------------------------------------------------- aree (devono coincidere con data-grp in index.html)

LABELS = {
    "spagna": "Spagna", "portogallo": "Portogallo", "francia": "Francia", "italia": "Italia",
    "svizzera": "Svizzera", "inghilterra": "Regno Unito", "irlanda": "Irlanda", "germania": "Germania", "austria": "Austria",
    "belgio": "Belgio", "paesibassi": "Paesi Bassi", "lussemburgo": "Lussemburgo",
    "francigena": "Via Francigena", "romea": "Via Romea", "trekking": "Trekking a lunga percorrenza", "europa": "Altri paesi",
}

# contorni approssimati (lon, lat): bastano per capire in che paese sta la maggior parte di un cammino.
# L'ordine conta: al primo contorno che contiene il punto ci si ferma.
_PT = [(-9.6, 42.2), (-8.2, 42.15), (-6.2, 41.95), (-6.9, 41.0), (-6.9, 40.0), (-7.0, 39.6), (-7.3, 39.0),
       (-7.0, 38.2), (-7.5, 37.5), (-7.4, 37.0), (-9.0, 36.8), (-9.7, 38.5), (-9.7, 40.0)]
_ES = [(-9.5, 43.9), (-1.8, 43.4), (0.7, 42.85), (3.3, 42.45), (3.5, 41.8), (0.5, 40.5), (-0.3, 39.0),
       (-2.1, 36.6), (-5.6, 35.9), (-7.4, 37.0), (-7.5, 37.5), (-7.0, 38.2), (-7.3, 39.0), (-7.0, 39.6),
       (-6.9, 40.0), (-6.9, 41.0), (-6.2, 41.95), (-8.2, 42.15), (-9.6, 42.2)]
_CH = [(6.0, 46.15), (6.9, 47.45), (8.6, 47.8), (9.6, 47.55), (10.5, 46.9), (10.1, 46.2), (9.0, 45.8),
       (8.4, 46.3), (7.0, 45.85)]
_AT = [(9.5, 47.55), (9.6, 47.05), (10.5, 46.85), (12.2, 47.0), (12.4, 46.7), (13.7, 46.5), (14.6, 46.4),
       (16.1, 46.8), (16.5, 47.4), (17.2, 48.0), (16.9, 48.75), (15.0, 49.05), (13.8, 48.8), (13.0, 48.3),
       (12.9, 47.6), (10.5, 47.5)]
_DE = [(6.0, 51.0), (6.1, 50.1), (6.4, 49.5), (8.2, 49.0), (7.6, 47.6), (8.6, 47.8), (9.6, 47.55), (10.5, 47.5),
       (12.9, 47.6), (13.0, 48.3), (13.8, 48.8), (12.1, 50.3), (14.8, 50.9), (14.6, 52.6), (14.2, 53.9),
       (11.0, 54.5), (9.0, 54.9), (8.5, 53.6), (7.0, 53.4), (6.7, 52.3), (6.0, 51.9)]
_FR = [(-4.9, 48.5), (-1.6, 49.75), (1.6, 50.95), (2.5, 51.1), (4.2, 49.95), (5.8, 49.5), (8.2, 49.0), (7.6, 47.6),
       (6.0, 46.15), (7.0, 45.85), (6.6, 45.1), (7.0, 44.2), (7.6, 43.8), (6.0, 43.0), (3.2, 43.3), (3.2, 42.4),
       (1.7, 42.5), (0.7, 42.85), (-1.8, 43.4), (-1.5, 44.0), (-1.3, 45.5), (-2.3, 47.1), (-4.9, 47.8)]
_IT = [(6.6, 45.1), (7.0, 45.85), (8.4, 46.3), (9.0, 45.8), (10.1, 46.2), (10.5, 46.85), (12.2, 47.0), (12.4, 46.7),
       (13.7, 46.5), (13.8, 45.6), (12.4, 44.9), (13.6, 43.5), (15.9, 42.0), (16.3, 41.95), (18.7, 40.3),
       (18.6, 39.7), (17.0, 39.0),
       (16.6, 38.0), (15.6, 37.9), (15.6, 40.0), (12.0, 41.6), (10.5, 43.0), (9.5, 44.1), (7.6, 43.8), (7.0, 44.2)]
_SIC = [(12.3, 38.25), (15.7, 38.35), (15.2, 36.6), (12.3, 37.5)]
_SAR = [(8.0, 41.3), (9.9, 41.3), (9.8, 38.9), (8.3, 38.8)]
_GB = [(-5.9, 49.9), (1.9, 50.9), (1.9, 52.9), (0.1, 53.7), (-1.5, 55.1), (-2.0, 55.85), (-3.1, 55.05),
       (-3.5, 54.0), (-3.0, 53.45), (-4.8, 53.45), (-5.4, 51.7), (-3.0, 51.25), (-5.9, 50.4)]
COUNTRIES = [("portogallo", _PT), ("spagna", _ES), ("svizzera", _CH), ("austria", _AT), ("germania", _DE),
             ("francia", _FR), ("italia", _IT), ("italia", _SIC), ("italia", _SAR), ("inghilterra", _GB)]
ROMEA = re.compile(r"\bRomea\b", re.I)
FRANCIGENA = re.compile(r"Francigena", re.I)


def _inside(x, y, poly):
    ins, j = False, len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


# stato -> area del sito (gli altri stati vanno in "europa")
CC_AREA = {"ES": "spagna", "AD": "spagna", "PT": "portogallo", "FR": "francia", "MC": "francia",
           "IT": "italia", "SM": "italia", "VA": "italia", "CH": "svizzera", "LI": "svizzera",
           "AT": "austria", "DE": "germania", "GB": "inghilterra", "IM": "inghilterra", "JE": "inghilterra",
           "GG": "inghilterra", "IE": "irlanda", "BE": "belgio", "NL": "paesibassi", "LU": "lussemburgo"}
_NE = [None]


def _ne():
    """Confini veri degli stati (scripts/regioni-europa.json, Natural Earth), raggruppati per stato."""
    if _NE[0] is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regioni-europa.json")
        by = {}
        try:
            for a in json.load(open(path, encoding="utf-8"))["aree"]:
                for r in a["rings"]:
                    xs, ys = [p[0] for p in r], [p[1] for p in r]
                    by.setdefault(a["cc"], []).append(((min(xs), min(ys), max(xs), max(ys)), r))
        except Exception:  # noqa: BLE001
            by = {}
        _NE[0] = by
    return _NE[0]


_CC_MEMO = {}


def _cc_at(x, y):
    k = (round(x, 2), round(y, 2))
    if k not in _CC_MEMO:
        hit = ""
        for cc, rings in _ne().items():
            if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] and _inside(x, y, r) for b, r in rings):
                hit = cc
                break
        _CC_MEMO[k] = hit
    return _CC_MEMO[k]


def country_of(f):
    pts = [c for line in f["geometry"]["coordinates"] for c in line]
    step = max(1, len(pts) // 400)
    votes = {}
    use_ne = bool(_ne())
    for x, y in pts[::step]:
        if use_ne:
            cc = _cc_at(x, y)
            if not cc:
                continue   # in mare o su un confine semplificato: non vota
            k = CC_AREA.get(cc, "europa")
        else:   # riserva: contorni approssimati
            k = next((k for k, p in COUNTRIES if _inside(x, y, p)), "europa")
        votes[k] = votes.get(k, 0) + 1
    return max(votes, key=votes.get) if votes else "europa"


def area_of(f):
    p = f["properties"]
    name = p.get("name", "")
    if ROMEA.search(name):
        return "romea"
    if p.get("group") == "francigena" or FRANCIGENA.search(name):
        return "francigena"
    if p.get("group") == "trekking":
        return "trekking"
    return country_of(f)


# ---------------------------------------------------------------- lettura e scrittura sicure

def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_atomic(path, text):
    """Scrive su un file temporaneo e poi lo sostituisce: un errore a metà non rovina il file vecchio."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_features(outdir):
    """Tutti i cammini presenti nei file .geojson. Se un file non si legge, si ferma tutto (non si rischia di sovrascriverlo)."""
    feats, per_file = {}, {}
    for p in sorted(glob.glob(os.path.join(outdir, "*.geojson"))):
        try:
            fl = read_json(p)["features"]
        except Exception as e:  # noqa: BLE001
            sys.exit(f"ERRORE: il file {p} non è leggibile ({e}). Mi fermo per non perdere dati.")
        per_file[p] = {f["properties"]["id"] for f in fl}
        for f in fl:
            old = feats.get(f["properties"]["id"])
            if not old or f["properties"].get("dl", "") >= old["properties"].get("dl", ""):
                feats[f["properties"]["id"]] = f
    return feats, per_file


def features_from_git(ref, outdir):
    """Cammini contenuti nei file .geojson di un vecchio commit (per recuperare quelli persi)."""
    rel = os.path.relpath(outdir, git_root(outdir)).replace(os.sep, "/")
    names = subprocess.run(["git", "ls-tree", "--name-only", ref, rel + "/"], cwd=git_root(outdir),
                           capture_output=True, text=True, check=True).stdout.split()
    out = {}
    for n in names:
        if not n.endswith(".geojson"):
            continue
        raw = subprocess.run(["git", "show", f"{ref}:{n}"], cwd=git_root(outdir), capture_output=True, check=True).stdout
        for f in json.loads(raw.decode("utf-8"))["features"]:
            out[f["properties"]["id"]] = f
        print(f"  {ref}:{n}: letto", flush=True)
    return out


def git_root(path):
    return subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True,
                          text=True, check=True).stdout.strip()


def save(outdir, feats, old_files):
    """Un file per area + indice. Riscrive solo i file cambiati. Un vecchio file si toglie
    soltanto se TUTTI i suoi cammini sono già nei file nuovi."""
    by_area = {}
    for f in feats.values():
        f["properties"]["area"] = area_of(f)
        by_area.setdefault(f["properties"]["area"], []).append(f)
    idx_path = os.path.join(outdir, "indice.json")
    try:
        old_idx = {e["key"]: e for e in read_json(idx_path).get("areas", [])}
    except Exception:  # noqa: BLE001
        old_idx = {}
    index, written, changed = [], {}, False
    for key in LABELS:
        fl = sorted(by_area.get(key, []), key=lambda f: (f["properties"]["name"], f["properties"]["id"]))
        if not fl:
            continue
        path = os.path.join(outdir, key + ".geojson")
        doc = {"type": "FeatureCollection", "attribution": "© contributori di OpenStreetMap (ODbL)", "features": fl}
        text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        try:
            same = open(path, encoding="utf-8").read() == text
        except OSError:
            same = False
        if not same:
            write_atomic(path, text)
            changed = True
        written[os.path.abspath(path)] = {f["properties"]["id"] for f in fl}
        upd = old_idx.get(key, {}).get("updated", TODAY) if same else TODAY
        index.append({"key": key, "label": LABELS[key], "file": key + ".geojson", "count": len(fl),
                      "mb": round(os.path.getsize(path) / 1e6, 2), "updated": upd})
    all_new = set().union(*written.values()) if written else set()
    for p, ids in old_files.items():   # file vecchi (es. santiago-spagna.geojson): via solo se già copiati
        ap = os.path.abspath(p)
        if ap in written or not os.path.exists(p):
            continue
        if ids <= all_new:
            os.remove(p)
            changed = True
            print(f"  {os.path.basename(p)}: tutti i suoi {len(ids)} cammini sono nei file nuovi, lo tolgo", flush=True)
        else:
            print(f"  ATTENZIONE: tengo {os.path.basename(p)} ({len(ids - all_new)} cammini non copiati)", flush=True)
    if changed or not os.path.exists(idx_path):
        write_atomic(idx_path, json.dumps({"generated": TODAY, "areas": index}, ensure_ascii=False, indent=1))
    return sum(e["mb"] for e in index), changed


# ---------------------------------------------------------------- candidati (elenco dei cammini da scaricare)

CAND = "candidati.json"


def load_candidates(path):
    """Formato: {"generated", "searches": {nome: data}, "roots": [{id, name, network, group}]}"""
    try:
        c = read_json(path)
    except FileNotFoundError:
        return {}, {}
    except Exception as e:  # noqa: BLE001
        print(f"Elenco candidati non leggibile ({e}): lo ricostruisco dai cammini già scaricati", flush=True)
        return {}, {}
    roots = {r["id"]: r for r in c.get("roots", [])}
    searches = c.get("searches") or {}
    if not searches and c.get("generated"):   # vecchio formato: data unica
        searches = {s[0]: c["generated"] for s in SEARCHES}
    if c.get("ricerca") != SEARCH_VER:        # i criteri di ricerca sono cambiati: si rifanno tutte le ricerche
        print("Criteri di ricerca aggiornati: rifaccio la ricerca dei cammini", flush=True)
        searches = {}
    return roots, searches


def save_candidates(path, roots, searches):
    rl = sorted(roots.values(), key=lambda r: r["id"])
    write_atomic(path, json.dumps({"generated": TODAY, "ricerca": SEARCH_VER, "searches": searches, "roots": rl},
                                  ensure_ascii=False, separators=(",", ":")))


def search_all(broad, roots, searches, only_old_days=None):
    """Rifà la ricerca su OSM. I risultati si AGGIUNGONO all'elenco: una ricerca fallita o parziale non toglie nulla."""
    complete, found_ids = True, set()
    for name, group, bbox, rx, net, jac, refs in SEARCHES:
        last = searches.get(name)
        if only_old_days is not None and last and \
                (datetime.date.today() - datetime.date.fromisoformat(last)).days < only_old_days:
            continue
        rx = rx or (BROAD if broad else NAMES)
        try:
            found = list_roots(rx, bbox, net, jac, refs)
        except TimeoutError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"  ricerca {name}: fallita ({str(e)[:120]}), tengo l'elenco precedente", flush=True)
            complete = False
            continue
        new = 0
        found_ids.update(e["id"] for e in found)
        found = [e for e in found if not ESCLUDI.search(e.get("tags", {}).get("name", ""))]
        for e in found:
            if e["id"] not in roots:
                new += 1
                roots[e["id"]] = {"id": e["id"], "name": e.get("tags", {}).get("name", ""),
                                  "network": e.get("tags", {}).get("network", ""), "group": group}
        searches[name] = TODAY
        print(f"  ricerca {name}: {len(found)} relazioni radice, {new} nuove; esempi: "
              + "; ".join((e.get("tags", {}).get("name") or str(e["id"]))[:40] for e in found[:6]), flush=True)
    return complete, found_ids


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broad", action="store_true", help="ricerca larga (molte relazioni)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"),
                    help="cartella dei dati")
    ap.add_argument("--max-minutes", type=float, default=25, help="tempo massimo del lotto; poi salva quanto scaricato")
    ap.add_argument("--ids", default="", help="ID di relazioni OSM da aggiungere (separati da virgola)")
    ap.add_argument("--refresh", action="store_true", help="rifà la ricerca dei cammini su OSM")
    ap.add_argument("--aggiorna-giorni", type=int, default=90,
                    help="riscarica i cammini scaricati da più di N giorni (0 = mai)")
    ap.add_argument("--recupera", default="", help="commit git da cui recuperare i cammini mancanti")
    ap.add_argument("--pulisci", action="store_true",
                    help="toglie i cammini non più trovati su OSM (solo se tutte le ricerche riescono)")
    ap.add_argument("--offline", action="store_true", help="niente rete: riorganizza solo i file esistenti")
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)
    DEADLINE[0] = time.time() + a.max_minutes * 60
    os.makedirs(a.out, exist_ok=True)

    feats, old_files = load_features(a.out)
    print(f"Nei file: {len(feats)} cammini", flush=True)
    tolti = [k for k, f in feats.items() if ESCLUDI.search(f["properties"].get("name", ""))]
    for k in tolti:
        del feats[k]
    if tolti:
        print(f"Tolti {len(tolti)} percorsi che non sono cammini (es. sentieri costieri inglesi)", flush=True)

    if a.recupera:
        rec = features_from_git(a.recupera, a.out)
        added = [k for k in rec if k not in feats]
        for k in added:
            feats[k] = rec[k]
        print(f"Recupero da {a.recupera}: {len(rec)} cammini letti, {len(added)} aggiunti perché mancavano", flush=True)

    cpath = os.path.join(a.out, CAND)
    roots, searches = load_candidates(cpath)
    for k in [k for k, r in roots.items() if ESCLUDI.search(r.get("name", ""))]:
        del roots[k]
    for k, f in feats.items():   # ogni cammino già scaricato è anche un candidato
        if k not in roots:
            p = f["properties"]
            roots[k] = {"id": k, "name": p.get("name", ""), "network": p.get("network", ""),
                        "group": p.get("group", "santiago")}
    for x in SEMPRE_ITALIA:
        roots.setdefault(int(x), {"id": int(x), "name": "", "network": "rwn", "group": "italia"})
    for x in SEMPRE_TREK:
        roots.setdefault(int(x), {"id": int(x), "name": "", "network": "nwn", "group": "trekking"})
    for x in SEMPRE + [int(v) for v in re.findall(r"\d+", a.ids or "")]:
        roots.setdefault(int(x), {"id": int(x), "name": "", "network": "iwn", "group": "santiago"})

    timed_out, complete_search, found_ids, skipped = False, False, set(), []
    try:
        if not a.offline:
            print("Ricerca dei cammini su OSM…", flush=True)
            complete_search, found_ids = search_all(a.broad, roots, searches, None if a.refresh else 30)
        save_candidates(cpath, roots, searches)

        if not a.offline:
            old = (datetime.date.today() - datetime.timedelta(days=a.aggiorna_giorni)).isoformat() \
                if a.aggiorna_giorni > 0 else ""
            order = sorted(roots.values(), key=lambda r: (GPRIO.get(r["group"], 3), NPRIO.get(r.get("network"), 3), r["id"]))
            missing = [r["id"] for r in order if r["id"] not in feats
                       or feats[r["id"]]["properties"].get("v") != VERSION]
            stale = [r["id"] for r in order if r["id"] in feats and r["id"] not in missing and old
                     and feats[r["id"]]["properties"].get("dl", "") < old]
            todo = missing + stale
            print(f"  {len(roots)} candidati: da scaricare {len(missing)}, da aggiornare {len(stale)}", flush=True)

            size, i, last_save = 4, 0, time.time()
            while i < len(todo):
                chunk = todo[i:i + size]
                print(f"  geometrie {i + len(chunk)}/{len(todo)} (gruppo da {len(chunk)})", flush=True)
                try:
                    els = fetch_geom(chunk)
                    size = min(size + 1, 8)          # il server risponde: gruppi più grandi
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
                        f = make_feature(by_id[rid], by_id, roots[rid]["group"])
                        if f:   # se il nuovo download è vuoto, resta la versione vecchia
                            feats[rid] = f
                i += len(chunk)
                if time.time() - last_save > 180:
                    mb, _ = save(a.out, feats, old_files)
                    print(f"  salvataggio intermedio: {len(feats)} cammini, {mb:.1f} MB", flush=True)
                    last_save = time.time()
                time.sleep(1)
    except TimeoutError:
        timed_out = True
        print("Tempo del lotto finito: salvo quanto scaricato.", flush=True)
        save_candidates(cpath, roots, searches)

    if a.pulisci:
        if complete_search and a.refresh and not timed_out:
            # tiene solo i cammini ritrovati in questa ricerca (più quelli aggiunti a mano con --ids)
            keep = found_ids | {int(x) for x in re.findall(r"\d+", a.ids or "")}
            gone = [k for k in feats if k not in keep]
            if len(gone) > 0.15 * max(1, len(feats)):
                print(f"PULIZIA ANNULLATA: toglierebbe {len(gone)} cammini su {len(feats)}, troppi.", flush=True)
            else:
                for k in gone:
                    del feats[k]
                    roots.pop(k, None)
                save_candidates(cpath, roots, searches)
                print(f"Pulizia: tolti {len(gone)} cammini", flush=True)
        else:
            print("Pulizia saltata: serve --refresh con tutte le ricerche riuscite.", flush=True)

    if not feats:
        sys.exit("Nessun cammino: non scrivo nulla.")
    mb, changed = save(a.out, feats, old_files)
    counts = {}
    for f in feats.values():
        counts[f["properties"]["area"]] = counts.get(f["properties"]["area"], 0) + 1
    print(f"Dati in {a.out}: {len(feats)} cammini, {mb:.1f} MB; saltati {len(skipped)}; "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), flush=True)
    print("COMPLETATO" if not timed_out else "INCOMPLETO: servono altri lotti", flush=True)


if __name__ == "__main__":
    main()
