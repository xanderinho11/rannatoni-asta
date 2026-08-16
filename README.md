# Rannatoni v7 — locale + Railway

Web-app/PWA per l'asta di riparazione della lega **Rannatoni**. La v7 gira in locale e contiene già la configurazione per il deploy Railway.

## Avvio locale su Windows

1. Apri `backend`.
2. Avvia `avvia.bat`.
3. Sul PC apri `http://localhost:8000`.
4. Sui telefoni collegati alla stessa Wi‑Fi usa l'indirizzo `http://192.168.x.x:8000` mostrato nella finestra.

Super Admin locale: `http://localhost:8000/admin`  
Password locale predefinita: **`asta2026`**.

## Preparazione

1. Carica **Catalogo**.
2. Facoltativo: carica **Statistiche** FantaLab (CSV/XLSX), collegate per ID.
3. Carica **Rose**.
4. Configura username, residui e gestore.
5. Per gli account nuovi genera un **PIN temporaneo**.
6. Salva e distribuisci username + PIN temporaneo.
7. Premi **Entra nell'asta come gestore**.

### Privacy PIN

- Il PIN attuale **non è mai visibile al Super Admin**.
- I PIN sono salvati nel DB come hash PBKDF2, non in chiaro.
- Il Super Admin può solo fare **Reset PIN**, ricevendo un nuovo PIN temporaneo mostrato una sola volta.
- Al primo accesso il Rannatone deve obbligatoriamente scegliere il proprio PIN personale.
- Il PIN personale può essere cambiato in seguito dal Profilo.
- Cambio/reset PIN invalida le altre sessioni della squadra.

## Asta veloce

- Timer standard: **60 secondi**.
- Quando parte una busta, partecipano **solo i Rannatoni che risultano Pronti in quell'istante**.
- L'elenco viene congelato per quella busta: chi diventa Pronto dopo entrerà dalla successiva.
- Appena tutti i partecipanti di quella busta hanno risposto, le buste si **aprono automaticamente**.
- Se qualcuno è bloccato, il gestore può premere **Apri buste ora**: chi manca viene considerato PASSO.
- Se scadono i 60 secondi, apertura automatica e mancanti = PASSO.
- Il gestore, se partecipa alla busta, deve aver già inviato la propria risposta prima di forzare l'apertura.
- La stessa logica vale negli spareggi.
- Le offerte possono essere modificate fino all'apertura della busta.

## Spareggio e vittoria casuale

Se uno spareggio resta in parità senza rilanci, l'assegnazione casuale viene mostrata chiaramente come:

**🎲 VITTORIA CASUALE**

La stessa indicazione resta anche nello Storico.

## RANDOM automatico

Con **RANDOM automatico** attivo, dopo la chiusura completa dell'asta parte un countdown di 10 secondi. È sufficiente che ci sia **almeno un Rannatone Pronto**: la nuova busta coinvolge solo chi è Pronto in quel momento.

Il gestore può fermare la singola estrazione automatica dal countdown.

## Rose

Nel menu inferiore sono disponibili:

- **La mia rosa** — rosa personale;
- **Rose** — tutte le squadre della lega;
- **Svincolati** — tutti i calciatori liberi con ricerca, filtri ruolo e ordinamento per statistiche disponibili;
- **Storico**;
- **Lobby**.

La sezione **Rose** mostra per ogni squadra:

- giocatori occupati / 35;
- posti liberi;
- crediti residui;
- rosa completa toccando la squadra.

Le rose sono ordinate usando sempre il **primo ruolo del catalogo**:

**POR → DC → DD → DS → B → E → M → C → W → T → A → PC**

I multiruolo mantengono fino a 3 badge visibili. `DC`, `DD`, `DS` e `B` usano lo stesso identico verde; `E` usa lo stesso azzurro di `M/C`; `W` lo stesso colore di `T`; `PC` lo stesso colore di `A`.

## Spettatore

La modalità 👀 Spettatore non partecipa all'asta e non viene conteggiata nei Pronti. Può vedere:

- asta live e timer;
- offerte solo dopo l'apertura;
- risultati e svincolati;
- tutte le Rose con posti e residui;
- Storico.

## Svincolati e riacquisto

La sezione **Svincolati** mostra tutti i giocatori liberi. Un multiruolo compare in ogni filtro ruolo che possiede (es. `DS/DD/E` compare in DS, DD ed E). La ricerca per nome resta completa; gli ordinamenti mostrati durante l’asta sono limitati a Quotazione, PMA, Media voto, FMV, Presenze, Gol e Assist.

Se una squadra svincola un calciatore a `X`, può riacquistarlo ma la sua offerta minima personale è `X`. Se vince con le altre offerte più basse, il prezzo finale resta comunque almeno `X`.

## Vincoli rosa e fine mercato

- minimo **2 POR**, massimo **5 POR**;
- minimo **21 giocatori di movimento**;
- massimo **35 giocatori totali**.

**🏁 Ho finito gli acquisti** mostra un riepilogo della rosa e può essere confermato solo se i requisiti sono rispettati. Dopo la conferma il Rannatone non può rientrare autonomamente: può farlo solo il gestore con **Riattiva Rannatone**. **Disconnetti** invalida realmente la sessione.

## Affidabilità

- stato live, offerte e spareggi persistiti in SQLite;
- acquisto + svincoli transazionali;
- backup SQLite completi;
- CSV rose aggiornati dopo le operazioni definitive;
- un solo proprietario per giocatore;
- import atomici e foreign key attive;
- sessioni persistenti con scadenza;
- modalità Simulazione con ripristino dello stato reale.

## Railway

Leggi `DOMANI_RAILWAY.md`. Il progetto include già `Dockerfile` e `railway.toml`.

Online devi impostare:

- `ADMIN_PASSWORD` forte;
- Volume persistente montato su `/app/data`.

La build online si rifiuta di partire con la password locale `asta2026`.

## v5 - notifiche push

La v5 aggiunge Web Push reali per i Rannatoni partecipanti. Le chiavi VAPID vengono generate automaticamente al primo avvio e salvate nella cartella `data/`, quindi su Railway restano persistenti nel volume montato su `/app/data`.

Non serve configurare manualmente le chiavi. Facoltativamente si può impostare la variabile `VAPID_SUBJECT` con un contatto `mailto:` o URL.


## Novità v7

- Il nome mostrato dei calciatori usa la colonna breve/cognome del Catalogo; il nome completo resta ricercabile e visibile nella scheda.
- Upload separato **Statistiche giocatori** CSV/XLSX con merge tramite ID e riepilogo dei match.
- Svincolati ordinabili per le statistiche disponibili; chi non ha il dato resta sempre in fondo.
- Toccando un calciatore da Asta, Rose, Svincolati, Storico o ricerca manuale si apre la scheda statistiche senza uscire dalla schermata.
- Ricerca manuale con foto, badge ruolo e squadra.
- Risultato PASSATO semplificato: “Tutti i Rannatoni hanno passato”, senza tendina offerte.
- Barra filtri ruolo mobile con posizione conservata/centrata e padding finale per non tagliare PC/Tutti.
- Palette badge ruolo unificata in tutta l’app.

## Statistiche opzionali (v8)

Il file statistiche non è necessario per avviare l’asta. Catalogo e Rose sono indipendenti dalle statistiche. Sono supportati CSV/XLSX con ID e i file Strategia/FantaLab senza ID: in questo secondo caso il collegamento avviene tramite nome breve + squadra. I giocatori senza match rimangono normalmente astabili e visibili, semplicemente senza dati statistici.


## Novità v9

La scheda calciatore è stata semplificata per l’uso rapido da telefono: mostra Media voto, FMV, Quotazione, PMA e Presenze, con Gol/Assist in formato secondario quando disponibili. Gli altri dati del file Strategia restano importati ma non affollano l’interfaccia. I ruoli difensivi `DC`, `DD`, `DS` e `B` condividono ora esattamente lo stesso verde.
