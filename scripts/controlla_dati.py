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


def ids_of(text):
    return {f["properties"]["id"] for f in json.loads(text)["features"]}


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
