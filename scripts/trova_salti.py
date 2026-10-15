#!/usr/bin/env python3
"""
Trova i "salti" in TUTTI i percorsi: punti dove un cammino si interrompe (spesso al confine fra due regioni o
stati, perché su OSM è diviso in pezzi regionali e un pezzo manca o non tocca il successivo).

Per ogni percorso (tutti i pezzi con lo stesso nome di base: "Via Romea - Bayern", "Via Romea - Tratto Trentino"…
diventano "Via Romea") si costruisce la rete dei suoi tracciati, cucendo gli strappi fino a 500 m come nel sito.
Se restano più pezzi staccati lunghi almeno MIN_KM, si collegano i pezzi più vicini fra loro e ogni collegamento
mancante è un salto: lunghezza, posizione dei due capi e nomi dei pezzi.

    python3 scripts/trova_salti.py            # scrive data/salti.json e stampa i salti più grandi
Solo libreria standard di Python.
"""
import datetime, glob, json, math, os, re, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from verifica_cammini import Net, hav   # noqa: E402

MIN_KM = 5          # pezzi più corti non contano (varianti, raccordi)
MAX_SALTO = 200     # oltre questa distanza non è un salto ma un altro cammino con lo stesso nome
MIN_SALTO = 0.6     # sotto, il sito lo cuce già da solo
# nomi troppo generici: pezzi con questi nomi non sono lo stesso percorso
GENERICI = {"jakobsweg", "camino de santiago", "chemin de saint-jacques", "chemin de compostelle", "sentiero", "cammino",
            "via", "gr", "jakobusweg", "camino", "caminho", "pilgrims way", "pilgrim's way", "pilgrim path", "jacobsroute",
            "sint jacobsroute", "camino de santiago del sur", "saint-jacques", "e", "variante", "rundweg", "wanderweg"}


def famiglia(name):
    n = unicodedata.normalize("NFD", name or "")
    n = "".join(c for c in n if unicodedata.category(c) != "Mn").lower()
    n = re.split(r"\s+[-–:]\s+|,|\(|:", n)[0]
    n = re.sub(r"\b(tappa|etapa|etape|stage|etappe|abschnitt|tratto|tramo|section)\b.*$", "", n)
    n = re.sub(r"\s+\d+[a-z]?$", "", n)
    n = re.sub(r"\bcami(n|nh)o\b", "camino", n)
    return re.sub(r"\s+", " ", n).strip(" -")


def main():
    feats = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "*.geojson"))):
        feats += json.load(open(p, encoding="utf-8"))["features"]
    try:
        feats += json.load(open(os.path.join(ROOT, "data", "manuali.json"), encoding="utf-8"))["features"]
    except (OSError, ValueError):
        pass
    fam = {}
    for f in feats:
        k = famiglia(f["properties"].get("name", ""))
        if not k or k in GENERICI or k.startswith("relazione") or len(k) < 4:
            continue
        fam.setdefault(k, []).append(f)
    out = []
    gruppo = {}
    for k, fl in fam.items():
        net = Net()
        owner = {}
        for f in fl:
            for line in f["geometry"]["coordinates"]:
                net.add_line(line)
                for lon, lat in line:
                    owner.setdefault(net.node(lat, lon), f["properties"].get("name", ""))
            gruppo.setdefault(f["properties"].get("name", ""), f["properties"].get("group", ""))
        if len(net.pos) < 3:
            continue
        net.bridge()
        # pezzi (componenti connesse) e loro lunghezza
        comp = [-1] * len(net.pos)
        pezzi = []
        for s in range(len(net.pos)):
            if comp[s] >= 0:
                continue
            cid = len(pezzi)
            comp[s] = cid
            st, nodes, km = [s], [s], 0.0
            while st:
                u = st.pop()
                for v, w in net.adj[u].items():
                    km += w / 2
                    if comp[v] < 0:
                        comp[v] = cid
                        st.append(v)
                        nodes.append(v)
            pezzi.append({"nodes": nodes, "km": km})
        big = [i for i, p in enumerate(pezzi) if p["km"] >= MIN_KM]
        if len(big) < 2:
            continue
        # distanza minima fra pezzi (su un campione di punti) e albero dei collegamenti più corti
        samp = {i: pezzi[i]["nodes"][::3] + pezzi[i]["nodes"][-1:] for i in big}
        grid = {}
        for i in big:
            for n in samp[i]:
                la, lo = net.pos[n]
                grid.setdefault((math.floor(la / 0.25), math.floor(lo / 0.25)), []).append((i, n))
        def nearest_pair(a, b):
            best = (1e9, None, None)
            pa = samp[a]
            for n in pa[::max(1, len(pa) // 400)]:
                la, lo = net.pos[n]
                for m in samp[b][::max(1, len(samp[b]) // 400)]:
                    d = hav(net.pos[n], net.pos[m])
                    if d < best[0]:
                        best = (d, n, m)
            return best
        edges = []
        for x in range(len(big)):
            for y in range(x + 1, len(big)):
                d, n, m = nearest_pair(big[x], big[y])
                if d <= MAX_SALTO:
                    edges.append((d, big[x], big[y], n, m))
        edges.sort()
        parent = {i: i for i in big}
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for d, a, b, n, m in edges:
            if find(a) == find(b):
                continue
            parent[find(a)] = find(b)
            if d < MIN_SALTO:
                continue
            # riprecisa i due capi del salto sui punti veri (non solo sul campione)
            out.append({"percorso": k, "km_salto": round(d, 1),
                        "da": {"ll": [round(net.pos[n][0], 4), round(net.pos[n][1], 4)], "pezzo": owner.get(n) or "",
                               "km_pezzo": round(pezzi[a]["km"])},
                        "a": {"ll": [round(net.pos[m][0], 4), round(net.pos[m][1], 4)], "pezzo": owner.get(m) or "",
                              "km_pezzo": round(pezzi[b]["km"])},
                        "km_percorso": round(sum(pezzi[i]["km"] for i in big)),
                        "gruppo": gruppo.get(owner.get(n) or owner.get(m) or "", "")})
    # un salto è vero solo se nessun altro percorso della rete collega i due capi (altrimenti è solo un pezzo
    # con un nome diverso, es. "Via Francigena Valle d'Aosta" dentro la "Via Francigena")
    rete = Net()
    for f in feats:
        for line in f["geometry"]["coordinates"]:
            rete.add_line(line)
    rete.bridge()
    for sl in out:
        a, _ = rete.nearest(*sl["da"]["ll"])
        b, _ = rete.nearest(*sl["a"]["ll"])
        km = rete.shortest(a, b, sl["km_salto"] * 2.5 + 8) if a is not None and b is not None else None
        sl["coperto"] = bool(km and km > 0)
        if sl["coperto"]:
            sl["km_coperto"] = round(km, 1)
    out = [sl for sl in out]
    from build_geojson import _cc_at, CC_AREA, LABELS
    for sl in out:
        cc = _cc_at(sl["da"]["ll"][1], sl["da"]["ll"][0]) or _cc_at(sl["a"]["ll"][1], sl["a"]["ll"][0])
        sl["paese"] = LABELS.get(CC_AREA.get(cc, "europa"), "Altri paesi") if cc else "Altri paesi"
    out.sort(key=lambda s: (s["coperto"], -s["km_percorso"], -s["km_salto"]))
    with open(os.path.join(ROOT, "data", "salti.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.date.today().isoformat(), "salti": out}, f, ensure_ascii=False, indent=0)
    veri = [s for s in out if not s["coperto"]]
    big_ones = [s for s in veri if s["km_percorso"] >= 50]
    print(f"Salti trovati: {len(out)}; {len(out) - len(veri)} coperti da altri percorsi; VERI: {len(veri)} in "
          f"{len({s['percorso'] for s in veri})} percorsi ({len(big_ones)} in percorsi oltre 50 km)\n")
    for s in big_ones[:80]:
        print(f"  {s['percorso'][:45]:45} buco di {s['km_salto']:6.1f} km  fra «{s['da']['pezzo'][:40]}» "
              f"e «{s['a']['pezzo'][:40]}»  {s['da']['ll']} → {s['a']['ll']}")


if __name__ == "__main__":
    main()
