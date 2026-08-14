# Rannatoni v6

- Regola di riacquisto: chi ha svincolato un giocatore deve offrire almeno il prezzo dello svincolo e non può riacquistarlo a un prezzo finale inferiore.
- Gli svincolati tornano sempre astabili e vengono rimossi dall’eventuale stato PASSATO.
- Nuova sezione **Svincolati** con ricerca, filtri per tutti i ruoli del giocatore e ordinamento dinamico per Quotazione/FVM/FantaMedia/Media voto/Presenze/Gol/Assist e altre statistiche disponibili nel Catalogo.
- I multiruolo compaiono in ciascun filtro ruolo posseduto.
- Intervallo RANDOM automatico aumentato da 5 a **10 secondi**.
- Ultimo risultato: riga compatta `Giocatore — 🏆 Squadra per Prezzo`, con indicazione 🎲 nelle vittorie casuali.
- **📦 Svincolati** e **💸 Offerte** diventano tendine separate.
- Gli svincolati usano ovunque la grafica delle Rose: foto, badge ruolo, nome, club e prezzo.
- Vincoli rosa: min 2 POR, max 5 POR, min 21 giocatori di movimento, max 35 totali.
- **Ho finito gli acquisti** ora mostra il riepilogo rosa, blocca l’uscita se i requisiti non sono rispettati e avvisa che il rientro è possibile solo tramite gestore.
- La chiusura di un acquisto con svincoli registra rosa, budget, storico e dati necessari al prezzo minimo in un’unica transazione SQLite.
- Cache PWA aggiornata alla v6.
