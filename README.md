# Rannatoni v4 — locale + Railway

Web-app/PWA per l'asta di riparazione della lega **Rannatoni**. La v4 gira in locale e contiene già la configurazione per il deploy Railway.

## Avvio locale su Windows

1. Apri `backend`.
2. Avvia `avvia.bat`.
3. Sul PC apri `http://localhost:8000`.
4. Sui telefoni collegati alla stessa Wi‑Fi usa l'indirizzo `http://192.168.x.x:8000` mostrato nella finestra.

Super Admin locale: `http://localhost:8000/admin`  
Password locale predefinita: **`asta2026`**.

## Preparazione

1. Carica **Catalogo**.
2. Carica **Rose**.
3. Configura username, residui e gestore.
4. Per gli account nuovi genera un **PIN temporaneo**.
5. Salva e distribuisci username + PIN temporaneo.
6. Premi **Entra nell'asta come gestore**.

### Privacy PIN

- Il PIN attuale **non è mai visibile al Super Admin**.
- I PIN sono salvati nel DB come hash PBKDF2, non in chiaro.
- Il Super Admin può solo fare **Reset PIN**, ricevendo un nuovo PIN temporaneo mostrato una sola volta.
- Al primo accesso il Rannatone deve obbligatoriamente scegliere il proprio PIN personale.
- Il PIN personale può essere cambiato in seguito dal Profilo.
- Cambio/reset PIN invalida le altre sessioni della squadra.

## Asta veloce v4

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

Con **RANDOM automatico** attivo, dopo la chiusura completa dell'asta parte un countdown di 5 secondi. È sufficiente che ci sia **almeno un Rannatone Pronto**: la nuova busta coinvolge solo chi è Pronto in quel momento.

Il gestore può fermare la singola estrazione automatica dal countdown.

## Rose

Nel menu inferiore sono disponibili:

- **La mia rosa** — rosa personale;
- **Rose** — tutte le squadre della lega;
- **Storico**;
- **Lobby**.

La sezione **Rose** mostra per ogni squadra:

- giocatori occupati / 35;
- posti liberi;
- crediti residui;
- rosa completa toccando la squadra.

Le rose sono ordinate usando sempre il **primo ruolo del catalogo**:

**POR → DC → DD → DS → B → E → M → C → W → T → A → PC**

I multiruolo mantengono fino a 3 badge visibili. `W` usa lo stesso colore di `T`; `PC` lo stesso colore di `A`.

## Spettatore

La modalità 👀 Spettatore non partecipa all'asta e non viene conteggiata nei Pronti. Può vedere:

- asta live e timer;
- offerte solo dopo l'apertura;
- risultati e svincolati;
- tutte le Rose con posti e residui;
- Storico.

## Fine mercato

**🏁 Ho finito gli acquisti** esclude la squadra dalle aste future. Il gestore può riattivarla. **Disconnetti** invalida realmente la sessione.

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
