# v18.6 — Quotazione Mantra e fuori campionato

Le estrazioni RANDOM usano la quotazione attuale Mantra del listone al posto
del FVM. Con soglia 4, un giocatore con quotazione 4 e' incluso; uno con
quotazione 3 resta escluso, salvo uno svincolo confermato nella sessione.
I giocatori fuori campionato sono sempre esclusi dai disponibili, anche
dopo lo svincolo, e rimangono nelle rose e nello storico con un asterisco.

- Nuova impostazione persistente `random_min_quotation`, inizialmente 0.
  Il valore del vecchio filtro FVM non viene riutilizzato.
- Quotazione Mantra importata dalla colonna 8 del CSV FantaAsta; FVM
  conservato separatamente. Le quotazioni generiche non alimentano il filtro.
- Indicatore `out_of_league` dalla colonna 17 e migrazione SQLite additiva.
- Blocco dei fuori campionato nella ricerca, negli svincolati, nel RANDOM,
  nell'apertura manuale e nelle operazioni di acquisto del database.
- Asterischi nelle rose proprie/altrui, negli svincoli della rosa, nei risultati,
  nelle schede e nello storico, anche per gli spettatori. I nomi restano intatti.
- Le statistiche aggiuntive preservano quotazione Mantra e stato nel campionato.
- Aggiornamento delle viste dopo il caricamento del catalogo; il caricamento
  e' protetto dal lock e consentito tra le buste per non alterare aste aperte.
- Conferma dell'import con calciatori, fuori campionato e quotazioni trovate.
- Cache dell'app aggiornata alla versione 18.6.

Occorre ricaricare il Catalogo dopo il deploy per acquisire i nuovi dati.
Non occorre ricaricare le Rose. Dettagli in `LEGGIMI_PATCH_v18.6.txt`.

Verifica: 30 test automatici, callback con database temporaneo e LISTONE.csv
reale, sintassi Python/JavaScript e funzioni UI con DOM simulato. Nessuna
prova completa FastAPI/browser o pubblicazione del sito nell'ambiente locale.
