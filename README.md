# Pianifica il Cammino — rete di cammini

**In Cammino con La Cri · Viaggiatrice zaino in spalla**. Grafica: sfondo grigio, rosa (maglietta), grigio (zaino), giallo (frecce), blu del segnavia; simboli della conchiglia e della freccia gialla. La pellegrina in alto è un pittogramma originale (simbolo `#pilgrim` in `index.html`): per usare un tuo logo, sostituisci quel simbolo o l'elemento `.logo`.

Sito statico per pianificare itinerari sui Cammini di Santiago, anche combinando più cammini (es. Via Podiensis in Francia + Camino Francés in Spagna).

## Pubblicare su GitHub Pages

1. Carica `index.html`, `style.css`, `app.js` in un repository.
2. Settings → Pages → Branch `main`, cartella `/ (root)`.

Nessuna build: tutto gira nel browser.

## Dati dei cammini: `data/cammini.geojson`

Il sito legge i cammini da `data/cammini.geojson` nel repository: nessuna chiamata a OpenStreetMap dal browser, quindi niente più "Failed to fetch". Se il file manca, il sito ripiega sulle richieste dirette a Overpass.

Il file si genera con GitHub Actions (GitHub scarica i dati da OSM, non il tuo telefono):

1. Carica nel repository `scripts/build_geojson.py` e `.github/workflows/genera-geojson.yml` (stesse cartelle).
2. Repository → **Settings → Actions → General → Workflow permissions** → **Read and write permissions** → Save.
3. Scheda **Actions** → "Genera GeoJSON dei cammini" → **Run workflow**. Dopo qualche minuto compare `data/cammini.geojson`.
4. Si aggiorna da solo il 1° di ogni mese; per forzarlo ripeti il punto 3.

Da computer puoi anche eseguirlo in locale: `python3 scripts/build_geojson.py` (opzione `--broad` per la ricerca larga).

## Come si usa

1. **Rete**: cerca i cammini in OSM, spunta quelli che vuoi (pulsante "Seleziona i principali" per partire) e caricali. Vengono uniti in un unico grafo; piccoli vuoti tra tratti (≤150 m) sono collegati in automatico.
2. **Varianti**: per ogni cammino caricato scegli neutro / preferisci / evita. "Preferisci" lo fa seguire volentieri, "evita" lo esclude.
3. **Itinerario**: imposta partenza, arrivo e tappe intermedie ("Via") cercando una località o toccando la mappa. Il percorso più breve sulla rete viene ricalcolato a ogni modifica con "Calcola percorso". Le tappe intermedie diventano fermate obbligate.
4. **Tappe**: slider dei km al giorno; per ogni tappa distanza, salita/discesa, alloggi OSM, link di ricerca su Gronze. Esporta GPX/CSV o copia il link.

## Dati

- Tracciati, località, alloggi: OpenStreetMap (Overpass API), ODbL.
- Ricerca località: Nominatim. Quote: Open-Meteo (stimate, tendono a sottostimare i dislivelli).
- Nessun dato copiato da Gronze o Buen Camino (contenuti protetti, senza API pubblica).

## Limiti noti

- Dipende da come OSM modella i cammini: se due relazioni non si toccano (o distano più di 150 m) la rete non è connessa e il sito lo segnala.
- Le sotto-relazioni sono lette fino a un livello.
- Caricare molti cammini lunghi può richiedere un minuto o più; Overpass può rifiutare richieste pesanti, riprova dopo un po'.
- Gli alloggi sono quelli mappati in OSM: verifica sempre apertura e prezzi.
- Un backend (cache dei dati, itinerari salvati, prezzi) è il passo successivo, non è necessario per questa versione.
