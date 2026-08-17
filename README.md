# Rannatoni v13

Web-app/PWA per l'**asta di riparazione** della lega Rannatoni, con FastAPI, WebSocket e SQLite.

## Avvio locale

1. Apri la cartella `backend`.
2. Su Windows avvia `avvia.bat`; su Linux/macOS usa `avvia.sh`.
3. Apri `http://localhost:8000`.
4. Super Admin: `http://localhost:8000/admin`.

In locale la password Super Admin predefinita è `asta2026`. Su Railway va impostata con `ADMIN_PASSWORD`.

## Preparazione asta

1. Carica **Catalogo**.
2. Facoltativo: carica **Statistiche** FantaLab/Strategia (CSV/XLSX).
3. Carica **Rose**.
4. Inserisci gli **username** delle squadre.
5. Premi **Genera PIN per tutti**: vengono creati PIN temporanei casuali di 4 cifre.
6. Imposta residui e seleziona la squadra che **gestisce l'asta**.
7. Salva la configurazione e distribuisci username + PIN temporaneo.
8. Il Super Admin preme **Avvia asta**.

Al primo login ogni Rannatone deve cambiare il PIN temporaneo con un PIN personale. Il Super Admin non può vedere il PIN personale; può solo resettarlo generando un nuovo PIN temporaneo.

## Partecipazione

- Il login equivale automaticamente a essere **in asta**.
- Non esiste più il pulsante “Sono pronto”.
- Chi chiude l'app o va offline resta comunque nel mercato.
- Si esce dal mercato solo con **Ho finito gli acquisti**, che conclude il mercato e disconnette l'utente.
- Solo Gestore/Super Admin può riattivare un Rannatone che ha concluso.

Prima dell'avvio ufficiale viene mostrata la **Lobby** con chi ha già effettuato l'accesso e chi deve ancora entrare.

## Asta

- Timer standard: 60 secondi.
- Le buste sono segrete fino all'apertura.
- Quando tutti gli aventi diritto hanno risposto, l'apertura è automatica.
- Il Gestore può forzare l'apertura; i mancanti vengono considerati PASSO.
- Le offerte possono essere modificate fino all'apertura.
- Il valore digitato nel campo offerta resta preservato durante gli aggiornamenti realtime.
- RANDOM automatico: dopo un'asta conclusa può partire la successiva dopo 10 secondi.

## Svincoli

- Barra di conferma sticky sempre visibile durante la scelta degli svincoli.
- Posizione di scroll e selezioni restano preservate durante gli aggiornamenti realtime.
- Un giocatore **acquistato nella sessione d'asta corrente non può essere svincolato nella stessa sessione**, con controllo sia UI sia backend.
- Se una squadra riacquista un giocatore che aveva svincolato, la sua offerta minima personale è almeno il prezzo di svincolo.

## Rose

Ordine visuale basato sul primo ruolo del catalogo:

`POR → DC → DD → DS → B → E → M → C → W → T → A → PC`

I giocatori mostrano foto, squadra reale e fino a tre badge ruolo.

## Svincolati e statistiche

La ricerca Svincolati combina nome, filtro ruolo e ordinamento. I soli ordinamenti disponibili sono:

- Nome
- Media voto
- Fantamedia
- Quotazione

I popup del calciatore mostrano foto, ruoli, nome, squadra e solo:

- Media voto
- Fantamedia
- Quotazione

Le altre statistiche possono restare importate nel database, ma non vengono mostrate nell'interfaccia operativa.

## Chat Rannatoni

- Chat generale realtime salvata in SQLite.
- Nome squadra + badge `👑 Gestore` per chi gestisce l'asta.
- Il Gestore può eliminare messaggi degli utenti.
- Su telefono la chat si apre come pannello.
- Su desktop largo la chat resta visibile sulla destra mentre si segue l'asta.
- All'inizio di ogni busta compare solo il messaggio automatico: `🎲 È iniziata l’asta per NomeGiocatore`.

## Storico

- Tap su foto/nome del giocatore: scheda statistiche.
- Tap sulla card/risultato: dettaglio asta con offerte e svincoli.
- Vittorie casuali indicate chiaramente con `🎲 VITTORIA CASUALE`.

## Backup e reset

I backup completi includono database e CSV correnti. Il **Resetta completamente Rannatoni** del Super Admin cancella:

- Catalogo e statistiche
- Rose, squadre, username e PIN
- Storico aste, offerte e svincoli
- Chat
- Stato della sessione
- Sessioni utenti
- Tutti i backup e i CSV correnti

La password `ADMIN_PASSWORD`, il codice dell'app e il volume Railway non vengono rimossi.

## Railway

Il progetto include `Dockerfile` e `railway.toml`. In produzione il volume persistente resta montato su `/app/data` e la porta pubblica usata dal progetto è 8080.
