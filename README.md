# Pianifica il Cammino — rete di cammini

**In Cammino con La Cri · Viaggiatrice zaino in spalla**. Grafica: sfondo grigio, rosa (maglietta), grigio (zaino), giallo (frecce), blu del segnavia; simboli della conchiglia e della freccia gialla. La pellegrina in alto è un pittogramma originale (simbolo `#pilgrim` in `index.html`): per usare un tuo logo, sostituisci quel simbolo o l'elemento `.logo`.

Sito statico per pianificare itinerari sui Cammini di Santiago, anche combinando più cammini (es. Via Podiensis in Francia + Camino Francés in Spagna).

## Pubblicare su GitHub Pages

1. Carica `index.html`, `style.css`, `app.js` in un repository.
2. Settings → Pages → Branch `main`, cartella `/ (root)`.

Nessuna build: tutto gira nel browser.

## Dati dei cammini: cartella `data/`

Il sito legge i cammini da file statici nel repository, uno per area (i nomi coincidono con le caselle dell'area nel sito): `spagna`, `portogallo`, `francia`, `italia`, `svizzera`, `inghilterra`, `germania`, `austria`, `francigena`, `romea`, `europa` (altri paesi), più `indice.json` e `candidati.json`. Il paese di un cammino è quello in cui cade la maggior parte del suo percorso.

I file si generano con GitHub Actions: scheda **Actions** → "Genera GeoJSON dei cammini" → **Run workflow**. Si aggiorna da solo il 1° di ogni mese.

**Sicurezza dei dati**
- Lo script non toglie mai un cammino già scaricato. Se un download fallisce, resta la versione precedente.
- Una ricerca su OSM fallita o parziale aggiunge cammini, ma non ne toglie.
- Prima di ogni salvataggio, `scripts/controlla_dati.py` confronta i dati con l'ultimo commit. Se manca anche un solo cammino, il salvataggio viene bloccato.
- Due esecuzioni della Action non possono girare insieme.

**Opzioni di "Run workflow"**
- *ID relazioni*: aggiunge relazioni OSM a mano.
- *Commit da cui recuperare*: rimette i cammini presenti in un vecchio commit (es. `b329b1c`).
- *Rifai la ricerca*: cerca su OSM i cammini nuovi.
- *Pulisci* (raro): toglie i cammini spariti da OSM, al massimo il 15%.

Da computer: `python3 scripts/build_geojson.py` (opzioni `--refresh`, `--recupera COMMIT`, `--offline`, `--ids`, `--max-minutes N`, `--aggiorna-giorni N`).

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

## Ostelli e località (`data/alloggi/`)

La Action **“Genera ostelli e località”** (scheda *Actions* → *Run workflow*) scarica da OpenStreetMap le località e gli
alloggi entro 1,5 km dai tracciati e li salva in `data/alloggi/`: un file per area, `riepilogo.json` (per stato,
regione e paese) e `_celle.json` (copia grezza per riquadri, serve a riprendere). Gira anche da sola il 3 di ogni mese,
dopo i cammini; le due Action non girano mai insieme.

- La prima volta ci mette qualche ora (≈2.200 riquadri): lavora a lotti e salva dopo ogni lotto, quindi se si ferma basta rilanciarla.
- Tipi di alloggio: accoglienza pellegrini (albergue, gîte, ostello del pellegrino…), ostello, rifugio, pensione/B&B, hotel, campeggio.
- Stato e regione di ogni paese vengono da `scripts/regioni-europa.json` (Natural Earth, rigenerabile con `scripts/prepara_regioni.py`).
- Controllo di sicurezza: un file di area non può sparire né perdere più del 30% degli alloggi, altrimenti non si salva.
- La pagina `ostelli.html` mostra i conteggi; il pianificatore usa questi file per le tappe e passa a Overpass solo se mancano.

## Catalogo per nome (`data/catalogo.json`, `data/panoramica.json`, `data/cammini/`)

Il sito non carica più i file per stato. All'apertura scarica solo il **catalogo** (elenco dei cammini per nome,
famiglia, paesi e km) e la **panoramica** (tutti i cammini semplificati, circa 1,5 MB). Quando si sceglie un cammino
scarica solo il suo file in `data/cammini/<nome>.json`, con tutti i tratti, di qualunque paese.

- Lo genera `scripts/build_catalogo.py` dai file per stato (che restano l'archivio dei dati), senza scaricare nulla;
  la Action dei cammini lo rifà a ogni aggiornamento.
- I tratti con lo stesso nome (tappe, varianti numerate, Camino/Camiño/Caminho) diventano un solo cammino; i cammini
  fatti di relazioni con nomi diversi (es. Portoghese Interior = Caminho do Interior + Xabrés por Verín) sono in `COMPOSTI`.
- `scripts/verifica_cammini.py` controlla i buchi nei cammini principali (risultato in `data/verifica.json`).
- Famiglia **Trekking a lunga percorrenza**: ricerca dedicata (`TREK` in `build_geojson.py`: Sentiero Italia, Alte Vie,
  Via Alpina, TMB, GR 5/10/11/20, West Highland Way, Pennine Way, Kerry Way, Rota Vicentina, Rennsteig…), file
  `data/trekking.geojson`; quelli da includere sempre sono in `SEMPRE_TREK`.

## Tratti aggiunti a mano (`scripts/tratti_manuali.json`)

Per i pezzi di cammino che su OpenStreetMap non esistono come percorso (es. Portoghese Interior Coimbra - Farminhão):
si indicano le tappe e `scripts/build_manuali.py` calcola il tracciato con BRouter su strade e sentieri OSM (approssimato,
segnalato nel sito con ⚠), oppure usa un GPX messo in `data/manuali/`. Il tratto viene unito al cammino indicato in `unisci_a`.
