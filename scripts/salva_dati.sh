#!/usr/bin/env bash
# Salva la cartella data/ nel repository, solo se il controllo di sicurezza passa.
# Uso: bash scripts/salva_dati.sh [--pulisci]
set -u
CHECK_ARGS="${1:-}"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
rm -f data/*.tmp

for t in 1 2 3 4 5; do
  git add -A data
  if git diff --cached --quiet; then echo "Nessuna modifica da salvare."; exit 0; fi
  if ! python3 scripts/controlla_dati.py $CHECK_ARGS; then
    git reset -q data
    echo "ERRORE: controllo fallito, non salvo nulla. I dati restano anche nell'allegato della Action." >&2
    exit 1
  fi
  git commit -q -m "Aggiorna dati dei cammini"
  if git push -q origin HEAD:"${GITHUB_REF_NAME}"; then echo "Salvato nel repository."; exit 0; fi
  echo "Push rifiutato (tentativo $t): il repository è cambiato, rifaccio il commit sopra l'ultima versione."
  rm -rf /tmp/nuovidati && cp -r data /tmp/nuovidati
  git fetch -q origin "${GITHUB_REF_NAME}"
  git reset -q --hard "origin/${GITHUB_REF_NAME}"
  rm -rf data && cp -r /tmp/nuovidati data
  sleep 5
done
echo "ERRORE: impossibile salvare nel repository." >&2
exit 1
