# Rannatoni v11

## Lobby e avvio ufficiale
- Prima dell'avvio ufficiale, la schermata Asta mostra una Lobby con tutti i Rannatoni.
- Chi ha già effettuato il login compare come `Pronto`; gli altri come `Deve accedere`.
- Il Super Admin ha un nuovo comando `▶️ Avvia asta`.
- RANDOM, apertura manuale e RANDOM automatico restano bloccati fino all'avvio ufficiale.
- Il Gestore può comunque avviare una simulazione dalla Lobby prima dell'avvio ufficiale.
- Dopo l'avvio, prima della prima estrazione viene mostrato `Asta avviata · In attesa della prossima estrazione`.
- Lo stato di avvio è persistente nel database e incluso nei backup.
- Caricare nuove Rose o eseguire un reset riporta l'asta allo stato di Lobby.

## Svincoli
- Durante la selezione degli svincoli gli aggiornamenti realtime di presenza non ricostruiscono più la lista.
- Scroll e selezioni restano quindi fermi mentre si scorre la rosa.
- La barra sticky di conferma introdotta in v10 resta sempre visibile.

## Storico
- Tap sulla card/parte risultato: apre il dettaglio dell'asta (offerte e svincoli).
- Tap solo su foto o nome del calciatore: apre la scheda statistiche.
- Stessa interazione anche in modalità Spettatore.

## Ricerca manuale
- Nella ricerca giocatore del Gestore è stata rimossa l'apertura delle statistiche.
- Il tap sulla riga serve esclusivamente ad aprire il giocatore all'asta.

## PWA
- Cache shell aggiornata a `v11`.
