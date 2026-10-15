#!/usr/bin/env python3
"""
Controllo dei buchi nei cammini principali.

Per ogni cammino c'è una fila di città di tappa note. Sulla rete scaricata (data/*.geojson) si calcola il percorso
tra ogni città e la successiva e si segnala:
  FUORI RETE    la città è a più di 3 km da qualunque tracciato
  NON COLLEGATO non esiste un percorso tra le due città
  GIRO LUNGO    il percorso è più di 2,2 volte la distanza in linea d'aria (manca probabilmente un pezzo)

    python3 scripts/verifica_cammini.py                 # tutti i cammini
    python3 scripts/verifica_cammini.py Francés Levante  # solo quelli che contengono queste parole

Scrive anche data/verifica.json (risultati per il sito). Solo libreria standard di Python.
"""
import glob, heapq, json, math, os, sys, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELL = 0.002          # griglia di ricerca (come app.js)
GAP_KM = 0.5          # come app.js: la fine di un tratto si cuce a un altro tracciato entro 500 m
SNAP_KM = 3.0
RATIO = 2.2          # i cammini serpeggiano: oltre 2,2 volte la linea d'aria è sospetto

# (stato, cammino, [(città, lat, lon), ...])
C = [
    ("Spagna", "Camino Francés", [("Saint-Jean-Pied-de-Port", 43.163, -1.237), ("Roncisvalle", 43.009, -1.319),
        ("Pamplona", 42.818, -1.644), ("Logroño", 42.465, -2.445), ("Burgos", 42.344, -3.697), ("Sahagún", 42.371, -5.030),
        ("León", 42.599, -5.567), ("Astorga", 42.458, -6.056), ("Ponferrada", 42.546, -6.596), ("Sarria", 42.777, -7.414),
        ("Santiago", 42.8805, -8.5457)]),
    ("Spagna", "Camino del Norte", [("Irún", 43.338, -1.789), ("San Sebastián", 43.318, -1.981), ("Bilbao", 43.263, -2.935),
        ("Santander", 43.462, -3.805), ("Ribadesella", 43.461, -5.059), ("Gijón", 43.532, -5.661), ("Avilés", 43.556, -5.924),
        ("Ribadeo", 43.536, -7.040), ("Mondoñedo", 43.428, -7.363), ("Arzúa", 42.927, -8.163), ("Santiago", 42.8805, -8.5457)]),
    ("Spagna", "Camino Primitivo", [("Oviedo", 43.362, -5.845), ("Tineo", 43.336, -6.414), ("Grandas de Salime", 43.217, -6.877),
        ("Lugo", 43.012, -7.556), ("Melide", 42.914, -8.015), ("Santiago", 42.8805, -8.5457)]),
    ("Spagna", "Camino Inglés", [("Ferrol", 43.484, -8.233), ("Betanzos", 43.280, -8.212), ("Santiago", 42.8805, -8.5457)]),
    ("Spagna", "Camino de Fisterra", [("Santiago", 42.8805, -8.5457), ("Negreira", 42.908, -8.736), ("Fisterra", 42.907, -9.264)]),
    ("Spagna", "Camino Aragonés", [("Somport", 42.792, -0.526), ("Jaca", 42.570, -0.549), ("Arrés", 42.578, -0.799), ("Ruesta", 42.622, -1.063),
        ("Sangüesa", 42.575, -1.283), ("Monreal", 42.700, -1.508), ("Puente la Reina", 42.672, -1.814)]),
    ("Spagna", "Camino del Salvador", [("León", 42.599, -5.567), ("Cabanillas", 42.700, -5.590), ("La Robla", 42.800, -5.623), ("Pola de Lena", 43.160, -5.828),
        ("Oviedo", 43.362, -5.845)]),
    ("Spagna", "Camino Vasco del Interior", [("Irún", 43.338, -1.789), ("Vitoria", 42.846, -2.672), ("Burgos", 42.344, -3.697)]),
    ("Spagna", "Camino de Madrid", [("Madrid", 40.417, -3.704), ("Segovia", 40.948, -4.118), ("Sahagún", 42.371, -5.030)]),
    ("Spagna", "Camino de Invierno", [("Ponferrada", 42.546, -6.596), ("O Barco", 42.414, -6.983), ("Monforte de Lemos", 42.522, -7.513),
        ("Chantada", 42.610, -7.770), ("Lalín", 42.661, -8.110), ("Santiago", 42.8805, -8.5457)]),
    ("Spagna", "Vía de la Plata", [("Sevilla", 37.389, -5.984), ("Zafra", 38.425, -6.417), ("Mérida", 38.916, -6.344),
        ("Cáceres", 39.475, -6.372), ("Salamanca", 40.965, -5.664), ("Zamora", 41.503, -5.745), ("Benavente", 42.003, -5.678),
        ("Astorga", 42.458, -6.056)]),
    ("Spagna", "Camino Sanabrés", [("Granja de Moreruela", 41.800, -5.740), ("Puebla de Sanabria", 42.054, -6.635),
        ("A Gudiña", 42.060, -7.140), ("Laza", 42.060, -7.460), ("Ourense", 42.336, -7.864), ("Santiago", 42.8805, -8.5457)]),
    ("Spagna", "Camino de Levante", [("Valencia", 39.470, -0.376), ("Albacete", 38.995, -1.856), ("Toledo", 39.857, -4.024),
        ("Ávila", 40.656, -4.700), ("Medina del Campo", 41.310, -4.910), ("Zamora", 41.503, -5.745)]),
    ("Spagna", "Camino Mozárabe", [("Granada", 37.177, -3.599), ("Córdoba", 37.888, -4.779), ("Alcaracejos", 38.384, -4.968),
        ("Castuera", 38.722, -5.545), ("Don Benito", 38.957, -5.861), ("Medellín", 38.965, -5.957),
        ("Torrefresneda", 38.984, -6.101), ("Mérida", 38.916, -6.344)]),
    ("Portogallo", "Camino Portugués Central", [("Lisboa", 38.710, -9.140), ("Santarém", 39.236, -8.687), ("Tomar", 39.602, -8.410),
        ("Coimbra", 40.210, -8.430), ("Porto", 41.145, -8.610), ("Barcelos", 41.530, -8.620), ("Ponte de Lima", 41.767, -8.583),
        ("Tui", 42.047, -8.645), ("Pontevedra", 42.430, -8.645), ("Padrón", 42.739, -8.660), ("Santiago", 42.8805, -8.5457)]),
    ("Portogallo", "Camino Portugués da Costa", [("Porto", 41.145, -8.610), ("Vila do Conde", 41.353, -8.745),
        ("Viana do Castelo", 41.694, -8.832), ("Caminha", 41.875, -8.838), ("A Guarda", 41.900, -8.870), ("Baiona", 42.118, -8.850),
        ("Vigo", 42.236, -8.720), ("Redondela", 42.283, -8.610)]),
    ("Portogallo", "Camino Portugués Interior", [("Coimbra", 40.209, -8.427), ("Penacova", 40.270, -8.281),
        ("Mortágua", 40.396, -8.235), ("Tondela", 40.517, -8.082), ("Viseu", 40.657, -7.913), ("Lamego", 41.097, -7.810), ("Vila Real", 41.300, -7.746),
        ("Chaves", 41.740, -7.470), ("Verín", 41.940, -7.436), ("Xunqueira de Ambía", 42.200, -7.730),
        ("Ourense", 42.336, -7.864)]),
    ("Francia", "Via Podiensis (Le Puy)", [("Le Puy-en-Velay", 45.043, 3.885), ("Conques", 44.599, 2.398), ("Figeac", 44.609, 2.030),
        ("Cahors", 44.447, 1.441), ("Moissac", 44.105, 1.085), ("Lectoure", 43.934, 0.623), ("Navarrenx", 43.320, -0.759),
        ("Saint-Jean-Pied-de-Port", 43.163, -1.237)]),
    ("Francia", "Via Tolosana (Arles)", [("Arles", 43.677, 4.630), ("Montpellier", 43.610, 3.877), ("Castres", 43.606, 2.240),
        ("Toulouse", 43.604, 1.444), ("Auch", 43.646, 0.586), ("Oloron-Sainte-Marie", 43.194, -0.607), ("Somport", 42.792, -0.526)]),
    ("Francia", "Via Lemovicensis (Vézelay)", [("Vézelay", 47.466, 3.743), ("Nevers", 46.990, 3.159), ("Limoges", 45.834, 1.262),
        ("Périgueux", 45.184, 0.718), ("Mont-de-Marsan", 43.890, -0.500), ("Ostabat", 43.256, -1.075)]),
    ("Francia", "Via Turonensis (Tours)", [("Parigi", 48.853, 2.349), ("Orléans", 47.902, 1.909), ("Tours", 47.394, 0.684),
        ("Poitiers", 46.580, 0.340), ("Saintes", 45.746, -0.633), ("Bordeaux", 44.838, -0.579), ("Dax", 43.708, -1.054),
        ("Ostabat", 43.256, -1.075)]),
    ("Francia", "Via Gebennensis (Ginevra)", [("Ginevra", 46.204, 6.143), ("Le Puy-en-Velay", 45.043, 3.885)]),
    ("Italia", "Via Francigena (Italia)", [("Gran San Bernardo", 45.869, 7.170), ("Aosta", 45.737, 7.315), ("Ivrea", 45.466, 7.876),
        ("Vercelli", 45.320, 8.420), ("Pavia", 45.185, 9.155), ("Piacenza", 45.052, 9.693), ("Fidenza", 44.866, 10.060),
        ("Pontremoli", 44.375, 9.880), ("Lucca", 43.843, 10.505), ("San Gimignano", 43.468, 11.043), ("Siena", 43.318, 11.331),
        ("Viterbo", 42.417, 12.105), ("Roma", 41.902, 12.457)]),
    ("Francia", "Via Francigena (Francia e Svizzera)", [("Calais", 50.951, 1.858), ("Wissant", 50.886, 1.663),
        ("Licques", 50.784, 1.936), ("Wisques", 50.722, 2.197), ("Arras", 50.290, 2.780),
        ("Péronne", 49.932, 2.934), ("Laon", 49.564, 3.620), ("Reims", 49.258, 4.032), ("Châlons-en-Champagne", 48.957, 4.365),
        ("Bar-sur-Aube", 48.233, 4.706), ("Langres", 47.862, 5.333), ("Besançon", 47.238, 6.024), ("Pontarlier", 46.904, 6.354),
        ("Losanna", 46.520, 6.630), ("Gran San Bernardo", 45.869, 7.170)]),
    ("Italia", "Via Francigena del Sud", [("Roma", 41.902, 12.457), ("Velletri", 41.687, 12.777), ("Priverno", 41.472, 13.188),
        ("Terracina", 41.291, 13.248), ("Fondi", 41.357, 13.426), ("Formia", 41.256, 13.606), ("Sessa Aurunca", 41.236, 13.932),
        ("Benevento", 41.130, 14.782), ("Troia", 41.361, 15.309), ("Monte Sant'Angelo", 41.707, 15.958),
        ("Barletta", 41.320, 16.283), ("Bari", 41.117, 16.872), ("Brindisi", 40.638, 17.946), ("Lecce", 40.352, 18.172),
        ("Otranto", 40.147, 18.486), ("Santa Maria di Leuca", 39.796, 18.359)]),
    ("Regno Unito", "Via Francigena (Inghilterra)", [("Canterbury", 51.280, 1.080), ("Dover", 51.126, 1.313)]),
    ("Austria", "Via Romea Germanica (Austria)", [("Augusta", 48.366, 10.894), ("Innsbruck", 47.269, 11.404),
        ("Brennero", 47.002, 11.505), ("Bolzano", 46.498, 11.354)]),
    ("Italia", "Via degli Dei", [("Bologna", 44.494, 11.343), ("Firenze", 43.769, 11.256)]),
    ("Italia", "Via di Francesco", [("La Verna", 43.707, 11.930), ("Assisi", 43.070, 12.617), ("Rieti", 42.404, 12.860),
        ("Roma", 41.902, 12.457)]),
    ("Italia", "Cammino di San Benedetto", [("Norcia", 42.793, 13.093), ("Subiaco", 41.925, 13.094), ("Montecassino", 41.490, 13.814)]),
    ("Svizzera", "ViaJacobi (Rorschach - Brunnen)", [("Rorschach", 47.478, 9.490), ("Einsiedeln", 47.128, 8.747),
        ("Brunnen", 46.995, 8.605)]),
    ("Svizzera", "ViaJacobi (Treib - Ginevra, dopo il battello)", [("Treib", 46.983, 8.598), ("Flüeli-Ranft", 46.873, 8.268),
        ("Brienz", 46.754, 8.037), ("Berna", 46.948, 7.447), ("Friburgo", 46.806, 7.162), ("Losanna", 46.520, 6.630), ("Ginevra", 46.204, 6.143)]),
    ("Germania", "Jakobsweg Norimberga - Costanza", [("Norimberga", 49.452, 11.077), ("Rothenburg", 49.377, 10.180),
        ("Ulma", 48.398, 9.990), ("Costanza", 47.660, 9.175)]),
    ("Germania", "Jakobsweg Colonia - Treviri", [("Colonia", 50.938, 6.960), ("Treviri", 49.756, 6.640)]),
    ("Austria", "Jakobsweg Austria", [("Vienna", 48.208, 16.373), ("Melk", 48.228, 15.332), ("Salisburgo", 47.800, 13.045),
        ("Innsbruck", 47.269, 11.404), ("Feldkirch", 47.237, 9.598)]),
    ("Regno Unito", "St James' Way", [("Reading", 51.455, -0.970), ("Southampton", 50.904, -1.404)]),
    ("Regno Unito", "St Cuthbert's Way", [("Melrose", 55.598, -2.720), ("Lindisfarne", 55.670, -1.800)]),
    ("Regno Unito", "Whithorn Way", [("Glasgow", 55.860, -4.250), ("Whithorn", 54.733, -4.417)]),
    ("Regno Unito", "Fife Pilgrim Way", [("Culross", 56.056, -3.629), ("St Andrews", 56.340, -2.796)]),
    ("Regno Unito", "North Wales Pilgrim's Way", [("Basingwerk", 53.290, -3.210), ("Aberdaron", 52.804, -4.712)]),
    ("Irlanda", "St Kevin's Way", [("Hollywood", 53.090, -6.590), ("Glendalough", 53.011, -6.327)]),
    ("Irlanda", "St Finbarr's Way", [("Drimoleague", 51.660, -9.260), ("Gougane Barra", 51.839, -9.318)]),
    ("Irlanda", "Tóchar Phádraig", [("Ballintubber", 53.760, -9.280), ("Croagh Patrick", 53.760, -9.659)]),
]


def hav(a, b):
    r = math.pi / 180
    h = math.sin((b[0] - a[0]) * r / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


class Net:
    def __init__(self):
        self.idx, self.pos, self.adj, self.grid, self.fine = {}, [], [], {}, {}

    def node(self, lat, lon):
        k = (round(lat, 5), round(lon, 5))   # come app.js: nodo = coordinata a 5 decimali
        i = self.idx.get(k)
        if i is None:
            i = self.idx[k] = len(self.pos)
            self.pos.append((lat, lon))
            self.adj.append({})
            self.grid.setdefault((math.floor(lat / 0.05), math.floor(lon / 0.05)), []).append(i)
            self.fine.setdefault((math.floor(lat / CELL), math.floor(lon / CELL)), []).append(i)
        return i

    def bridge(self):
        """Come app.js: dalla fine di un tratto al nodo più vicino entro GAP_KM che non sia già
        raggiungibile da lì lungo la rete entro ~1,2 km (peso doppio)."""
        ends = [u for u in range(len(self.pos)) if len(self.adj[u]) == 1]
        n = 0
        for u in ends:
            dist, pq = {u: 0.0}, [(0.0, u)]
            while pq:
                d, x = heapq.heappop(pq)
                if d > dist.get(x, 1e18):
                    continue
                for y, w in self.adj[x].items():
                    nd = d + w
                    if nd <= GAP_KM * 2.4 and nd < dist.get(y, 1e18):
                        dist[y] = nd
                        heapq.heappush(pq, (nd, y))
            la, lo = self.pos[u]
            cx, cy = math.floor(la / CELL), math.floor(lo / CELL)
            best, bd = None, GAP_KM
            for dx in range(-3, 4):
                for dy in range(-4, 5):
                    for v in self.fine.get((cx + dx, cy + dy), ()):
                        if v in dist:
                            continue
                        d = hav(self.pos[u], self.pos[v])
                        if d < bd:
                            best, bd = v, d
            if best is not None:
                self.adj[u][best] = bd * 2
                self.adj[best][u] = bd * 2
                n += 1
        return n

    def add_line(self, coords):
        prev = None
        for lon, lat in coords:
            n = self.node(lat, lon)
            if prev is not None and prev != n:
                w = hav(self.pos[prev], self.pos[n])
                if w < self.adj[prev].get(n, 1e9):
                    self.adj[prev][n] = w
                    self.adj[n][prev] = w
            prev = n

    def nearest(self, lat, lon):
        gi, gj = math.floor(lat / 0.05), math.floor(lon / 0.05)
        best, bd = None, 1e9
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in self.grid.get((gi + di, gj + dj), ()):
                    d = hav((lat, lon), self.pos[i])
                    if d < bd:
                        best, bd = i, d
        return best, bd

    def shortest(self, s, t, limit):
        dist, pq = {s: 0.0}, [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == t:
                return d
            if d > limit:
                return -1
            if d > dist.get(u, 1e18):
                continue
            for v, w in self.adj[u].items():
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return None


def main():
    want = [w.lower() for w in sys.argv[1:]]
    net = Net()
    files = sorted(glob.glob(os.path.join(ROOT, "data", "*.geojson")))
    n = 0
    for p in files:
        for f in json.load(open(p, encoding="utf-8"))["features"]:
            n += 1
            for line in f["geometry"]["coordinates"]:
                net.add_line(line)
    try:   # tratti aggiunti a mano (build_manuali.py)
        for f in json.load(open(os.path.join(ROOT, "data", "manuali.json"), encoding="utf-8"))["features"]:
            n += 1
            for line in f["geometry"]["coordinates"]:
                net.add_line(line)
    except (OSError, ValueError):
        pass
    nb = net.bridge()
    print(f"Rete: {n} cammini, {len(net.pos)} nodi, {nb} piccoli strappi cuciti\n", flush=True)
    # tappe ufficiali di Gronze (con coordinate trovate da geocodifica_tappe.py): sostituiscono le città scelte a mano
    lista, battelli, senza_coord = list(C), {}, {}
    try:
        g = json.load(open(os.path.join(ROOT, "scripts", "tappe_gronze.json"), encoding="utf-8"))["cammini"]
        coord = json.load(open(os.path.join(ROOT, "data", "tappe_coord.json"), encoding="utf-8")).get("tappe", {})
    except (OSError, ValueError):
        g, coord = [], {}
    if g and coord:
        gl = []
        for c in g:
            z = ",".join(map(str, c["zona"]))
            towns = [(t, *coord[f"{t}|{z}"]) for t in c["tappe"] if f"{t}|{z}" in coord]
            senza_coord[c["nome"]] = [t for t in c["tappe"] if f"{t}|{z}" not in coord]
            battelli[c["nome"]] = {tuple(b) for b in c.get("battello", [])}
            if len(towns) >= 2:
                gl.append((c["stato"], c["nome"], towns))
        coperti = ("Camino", "Vía de la Plata", "Via Podiensis", "Via Tolosana", "Via Lemovicensis", "Via Turonensis",
                   "Via Gebennensis", "Via Francigena", "ViaJacobi")
        lista = gl + [x for x in C if not x[1].startswith(coperti)]
        print(f"Tappe di Gronze: {len(gl)} cammini, {sum(len(x[2]) for x in gl)} tappe con coordinate\n", flush=True)

    out, problems = [], 0
    for stato, nome, towns in lista:
        if want and not any(w in nome.lower() for w in want):
            continue
        legs, snaps = [], []
        for t in towns:
            i, d = net.nearest(t[1], t[2])
            snaps.append((i, d))
        bad = []
        for (a, b), (sa, sb) in zip(zip(towns, towns[1:]), zip(snaps, snaps[1:])):
            crow = hav(a[1:], b[1:])
            leg = {"da": a[0], "a": b[0], "linea_aria": round(crow),
                   "ll": [[round(a[1], 4), round(a[2], 4)], [round(b[1], 4), round(b[2], 4)]]}
            if (a[0], b[0]) in battelli.get(nome, ()):
                leg["esito"] = "ok"
                leg["nota"] = "in battello"
            elif sa[0] is None or sa[1] > SNAP_KM or sb[0] is None or sb[1] > SNAP_KM:
                far = [x[0] for x, s in ((a, sa), (b, sb)) if s[0] is None or s[1] > SNAP_KM]
                leg["esito"] = "FUORI RETE: " + ", ".join(far)
            else:
                km = net.shortest(sa[0], sb[0], crow * 3 + 30)
                if km is None:
                    leg["esito"] = "NON COLLEGATO"
                elif km < 0:
                    leg["esito"] = "GIRO LUNGHISSIMO (oltre 3 volte la linea d'aria)"
                else:
                    leg["km"] = round(km)
                    leg["esito"] = "GIRO LUNGO" if km > crow * RATIO + 5 else "ok"
            legs.append(leg)
            if leg["esito"] != "ok":
                bad.append(leg)
        problems += len(bad)
        stato_txt = "OK" if not bad else f"{len(bad)} problemi"
        print(f"[{stato}] {nome}: {stato_txt}")
        for leg in legs:
            if leg["esito"] != "ok":
                km = f"{leg['km']} km" if "km" in leg else "—"
                print(f"    {leg['da']} → {leg['a']}: {leg['esito']} ({km}, in linea d'aria {leg['linea_aria']} km)")
        if senza_coord.get(nome):
            print(f"    (coordinate non trovate per: {', '.join(senza_coord[nome])})")
        out.append({"stato": stato, "cammino": nome, "tratti": legs, "problemi": len(bad),
                    **({"senza_coordinate": senza_coord[nome]} if senza_coord.get(nome) else {})})
    print(f"\nTotale: {problems} tratti con problemi su {sum(len(c['tratti']) for c in out)}")
    if not want:
        with open(os.path.join(ROOT, "data", "verifica.json"), "w", encoding="utf-8") as f:
            json.dump({"generated": datetime.date.today().isoformat(), "cammini": out}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
