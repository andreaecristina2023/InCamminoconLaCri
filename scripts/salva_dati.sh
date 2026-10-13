#!/usr/bin/env bash
# Salva nel repository una cartella di dati, solo se il controllo di sicurezza passa.
# Uso: bash scripts/salva_dati.sh [--pulisci]
#      DATI_DIR=data/alloggi MESSAGGIO="Aggiorna ostelli" bash scripts/salva_dati.sh
# Salva SOLO la cartella indicata (di default data/): il resto del repository non viene toccato.
set -u
CHECK_ARGS="${1:-}"
DIR="${DATI_DIR:-data}"
MSG="${MESSAGGIO:-Aggiorna dati dei cammini}"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
find "$DIR" -name '*.tmp' -delete 2>/dev/null

for t in 1 2 3 4 5; do
  git add -A "$DIR"
  if git diff --cached --quiet; then echo "Nessuna modifica da salvare."; exit 0; fi
  if ! python3 scripts/controlla_dati.py $CHECK_ARGS; then
    git reset -q "$DIR"
    echo "ERRORE: controllo fallito, non salvo nulla. I dati restano anche nell'allegato della Action." >&2
    exit 1
  fi
  git commit -q -m "$MSG"
  if git push -q origin HEAD:"${GITHUB_REF_NAME}"; then echo "Salvato nel repository."; exit 0; fi
  echo "Push rifiutato (tentativo $t): il repository è cambiato, rifaccio il commit sopra l'ultima versione."
  rm -rf /tmp/nuovidati && mkdir -p /tmp/nuovidati && cp -r "$DIR/." /tmp/nuovidati/
  git fetch -q origin "${GITHUB_REF_NAME}"
  git reset -q --hard "origin/${GITHUB_REF_NAME}"
  rm -rf "$DIR" && mkdir -p "$DIR" && cp -r /tmp/nuovidati/. "$DIR/"
  sleep 5
done
echo "ERRORE: impossibile salvare nel repository." >&2
exit 1
