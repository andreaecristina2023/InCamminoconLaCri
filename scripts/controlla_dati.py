#!/usr/bin/env python3
"""
Controllo di sicurezza prima di salvare i dati nel repository.
Confronta i cammini dell'ultimo commit (HEAD:data/*.geojson) con quelli nella cartella data/:
se ne manca anche uno solo, esce con errore e il workflow NON salva.

    python3 scripts/controlla_dati.py            # controllo normale
    python3 scripts/controlla_dati.py --pulisci  # ammette che manchino cammini (pulizia voluta)
"""
import glob, json, os, subprocess, sys

root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
os.chdir(root)


sys.path.insert(0, os.path.join(root, "scripts"))
try:
    from build_geojson import ESCLUDI   # percorsi tolti apposta perché non sono cammini
except Exception:  # noqa: BLE001
    ESCLUDI = None


def ids_of(text):
    return {f["properties"]["id"] for f in json.loads(text)["features"]
            if not (ESCLUDI and ESCLUDI.search(f["properties"].get("name", "")))}


before = set()
names = subprocess.run(["git", "ls-tree", "--name-only", "HEAD", "data/"], capture_output=True, text=True).stdout.split()
for n in names:
    if n.endswith(".geojson"):
        raw = subprocess.run(["git", "show", f"HEAD:{n}"], capture_output=True, check=True).stdout
        before |= ids_of(raw.decode("utf-8"))

after = set()
for p in glob.glob("data/*.geojson"):
    try:
        after |= ids_of(open(p, encoding="utf-8").read())
    except Exception as e:  # noqa: BLE001
        sys.exit(f"BLOCCATO: {p} non è leggibile ({e})")

lost = before - after
print(f"Controllo dati: prima {len(before)} cammini, adesso {len(after)}, nuovi {len(after - before)}, mancanti {len(lost)}")
if lost and "--pulisci" not in sys.argv:
    print("BLOCCATO: mancherebbero questi cammini (id OSM): " + ", ".join(map(str, sorted(lost)[:50])), file=sys.stderr)
    sys.exit(1)
print("OK")

# ---------------------------------------------------------------- ostelli (data/alloggi)
# un file di area non deve sparire e non deve perdere più del 30% degli alloggi; la copia grezza
# non deve perdere riquadri
problems = []
for n in subprocess.run(["git", "ls-tree", "--name-only", "HEAD", "data/alloggi/"],
                        capture_output=True, text=True).stdout.split():
    if not n.endswith(".json") or n.endswith(("indice.json", "riepilogo.json")):
        continue
    old = json.loads(subprocess.run(["git", "show", f"HEAD:{n}"], capture_output=True, check=True).stdout.decode("utf-8"))
    if not os.path.exists(n):
        problems.append(f"{n} sparito")
        continue
    try:
        new = json.load(open(n, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        problems.append(f"{n} non leggibile ({e})")
        continue
    if n.endswith("_celle.json"):
        lost_cells = set(old) - set(new)
        if lost_cells:
            problems.append(f"{n}: persi {len(lost_cells)} riquadri")
    else:
        a, b = len(old.get("alloggi", [])), len(new.get("alloggi", []))
        print(f"Controllo ostelli {n}: prima {a}, adesso {b}")
        if a >= 20 and b < a * 0.7:
            problems.append(f"{n}: alloggi scesi da {a} a {b}")
if problems and "--pulisci" not in sys.argv:
    print("BLOCCATO: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)
print("OK ostelli")
