# Rannatoni 18.10

Il totale «astati» includeva i calciatori fuori campionato. Ora il bacino
iniziale esclude i giocatori con asterisco e il totale salvato nelle versioni
precedenti viene corretto al primo aggiornamento dello stato.

Le rose iniziali sono ricostruite percorrendo a ritroso gli acquisti validi:
svincoli, riacquisti e annullamenti non fanno sottrarre due volte i giocatori
fuori campionato. Importazione del catalogo, caricamento delle rose e asta da
zero usano lo stesso calcolo. I conteggi seguono il flag del catalogo attuale;
rose e storico conservano anche i giocatori successivamente usciti dal campionato.

Verifica: 44 test unittest, di cui 8 nuovi sul contatore, e 5 test esistenti
sui blocchi del timer superati. La patch aggiorna la versione 18.9 senza
richiedere una nuova importazione dei dati.
