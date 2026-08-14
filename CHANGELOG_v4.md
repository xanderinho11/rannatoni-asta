# Changelog Rannatoni v4

- Timer asta e spareggi portato a **60 secondi**.
- Partecipanti della singola busta = Rannatoni **Pronti al momento dell'apertura**.
- Apertura automatica non appena tutti i partecipanti della busta hanno risposto.
- Gestore può forzare **Apri buste ora** prima della scadenza; mancanti = PASSO.
- RANDOM automatico parte con almeno un Rannatone Pronto e coinvolge solo i Pronti.
- Vittoria da sorteggio dopo spareggio resa esplicita come **🎲 VITTORIA CASUALE** live e nello storico.
- Nuova sezione **Rose** per partecipanti e spettatori con 35 slot, posti liberi e residuo.
- Consultazione rosa completa di ogni squadra.
- PIN migrati a hash PBKDF2; nessun PIN attuale visibile al Super Admin.
- PIN temporaneo obbligatorio al primo accesso, poi scelta PIN personale.
- Cambio PIN dal Profilo.
- Reset PIN dal Super Admin con nuovo temporaneo one-shot e invalidazione sessioni precedenti.
- Export accessi/residui non contiene PIN né hash.
- Mantiene tema viola, safe area mobile, badge ruoli, svincolati separati dalle offerte e modalità spettatore.
