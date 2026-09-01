# Rannatoni v18.5

## Residui automatici e correggibili

- Il caricamento delle Rose calcola, per ogni squadra, crediti iniziali salvati meno somma dei prezzi nel file. Esempio: 500 − 478 = 22.
- Il budget iniziale è visibile anche in modalità Riparazione; il valore predefinito resta 500.
- I residui si possono correggere nella configurazione delle squadre, anche con valori negativi. Le correzioni persistono fino a un nuovo caricamento delle Rose.
- L'import conserva username, PIN e ruolo di Gestore delle squadre con lo stesso nome. Il solo aggiornamento del codice non cambia i residui già salvati.
- Prezzi mancanti, non numerici, negativi o frazionari e giocatori duplicati bloccano l'import prima di modificare rose e crediti.

## Risultato dei caricamenti

- Conferma visibile sotto ogni caricamento con nome file, calciatori importati, squadre trovate e giocatori assegnati.
- Le statistiche indicano quanti giocatori sono stati collegati e segnalano le voci senza corrispondenza.
- Stato di caricamento visibile e blocco dei doppi invii. I totali dei dati presenti restano visibili riaprendo il pannello.
- Dopo l'import delle Rose la configurazione squadre si apre per controllare i residui; eventuali risultati negativi sono segnalati.

## Svincolati nel RANDOM

- I giocatori svincolati in un'assegnazione confermata nella sessione corrente ignorano la soglia FVM, anche se il valore manca.
- Sono comunque esclusi i giocatori già riacquistati e quelli per cui tutti hanno passato dopo lo svincolo.
- L'eccezione usa lo storico persistente, esclude gli eventi annullati e termina alla sessione successiva. Nessuna migrazione del database.
- Riepiloghi aggiornati con il numero di svincolati ammessi sotto soglia o senza FVM.

## Verifiche

- 20 test automatici su import FVM, filtro RANDOM, ciclo degli svincoli, calcolo crediti, correzione manuale, persistenza ed errori di importazione.
- Verificati i callback di caricamento e aggiornamento impostazioni con database temporaneo e quelli del pannello con risposte del backend.
- Sintassi Python e JavaScript verificata. L'app completa e la verifica visiva nel browser non sono state eseguite nell'ambiente di preparazione.
