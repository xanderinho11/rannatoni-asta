# v18.9 — Autore della pausa e riepilogo post asta

Un blocco tempo mostra il nome della squadra che lo ha richiesto a tutti i
partecipanti e agli spettatori. La pausa amministrativa mostra anche la
qualifica di Gestore. L'autore viene conservato nello stato persistito e
cancellato quando il timer riparte o si passa al giro successivo.

Il riepilogo veniva ricostruito a ogni aggiornamento dello stato, anche con
un risultato identico. Ora gli aggiornamenti di presenza e orologio conservano
la vista; il countdown si sincronizza sul posto. Quando cambiano i controlli
dell'estrazione automatica, la scheda risultato resta nel DOM e mantiene
immagini e tendine aperte. Un risultato diverso viene comunque ridisegnato.

Le viste condividono `frontend/auction-ui.js`. La cache PWA passa alla 18.9.
Rimane attivo il comportamento della 18.8: offerte consentite a timer fermo
e apertura delle buste quando tutti hanno risposto.

Validazione: 41 test Python complessivi, controlli JavaScript delle viste e
del rendering, compilazione e sintassi. Da confermare visivamente sul telefono;
nessuna pubblicazione effettuata dall'assistente.
