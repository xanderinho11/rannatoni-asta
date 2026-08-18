# Rannatoni v15

## Novità

- Aggiunta **Presenze** al popup statistiche del calciatore.
- Ordinamenti Svincolati invariati: Nome, Media voto, Fantamedia, Quotazione.
- Nuovo reveal per gli spareggi che terminano ancora in parità:
  - messaggio iniziale “Nessuno vuole alzare la propria offerta…”;
  - annuncio dell’assegnazione casuale;
  - alternanza dei soli nomi delle squadre rimaste in parità con rallentamento;
  - reveal finale della squadra vincitrice.
- Il backend espone esplicitamente `random_candidates` nel risultato casuale; il vincitore resta deciso lato server.
- La card del reveal ha altezza fissa e gli aggiornamenti realtime dello stesso risultato non causano rerender della schermata Asta.
- Rimossa la vecchia dicitura tecnica `VITTORIA CASUALE · spareggio chiuso in parità` dall’Ultimo risultato.
- Nello Storico resta il badge `🎲 Casuale`, senza il box tecnico ridondante.
- Cache PWA aggiornata a v15.

## Non incluso

La verifica 1:1 dell’export Rose con Fantacalcio resta separata: serve un file Rose originale esportato direttamente da Fantacalcio per il confronto definitivo.
