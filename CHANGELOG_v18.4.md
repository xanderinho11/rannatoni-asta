# Rannatoni v18.4

## FVM minimo per RANDOM

- Le impostazioni includono una soglia minima FVM Mantra per le estrazioni casuali.
- `0` disattiva il filtro; con soglia `4` sono candidati i giocatori con FVM maggiore o uguale a 4.
- Il filtro vale per RANDOM manuale e automatico, senza limitare la ricerca e la chiamata manuale.
- I giocatori senza FVM sono esclusi quando la soglia è attiva.
- La soglia resta salvata e può essere modificata durante l'asta, con effetto dalla successiva estrazione.
- Se non restano candidati, il countdown non parte e l'interfaccia indica di abbassare la soglia o aggiornare i dati.

## Import FVM

- Il catalogo CSV standard importa il FVM Mantra dedicato.
- I file statistiche riconoscono `FVM`, `FVM/1000` e le intestazioni Mantra equivalenti.
- Un aggiornamento delle statistiche conserva il FVM del catalogo quando il file caricato non lo contiene.
