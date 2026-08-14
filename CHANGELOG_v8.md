# Rannatoni v8

## Statistiche FantaLab / Strategia opzionali

- Il file statistiche non fa parte dei requisiti per avviare l'asta.
- Catalogo e Rose restano gli unici file necessari alla creazione delle squadre e all'asta.
- Supporto XLSX multi-foglio (Por, Dc, B, Ds, Dd, E, M, C, W, T, A, Pc).
- Se il file contiene un ID, il collegamento usa l'ID.
- Se non contiene ID, come `Strategia 1.xlsx`, il collegamento usa nome breve + squadra.
- Gestione codici squadra FantaLab (es. ATA -> Atalanta, INT -> Inter, JUV -> Juventus).
- I giocatori senza corrispondenza restano perfettamente utilizzabili nell'asta e semplicemente non hanno statistiche.
- Negli ordinamenti statistici i giocatori senza dato restano in fondo.
- I multiruolo presenti in più fogli del file Strategia vengono deduplicati.
- Riconoscimento di Quo/Quotazione, MV, FMV/FantaMedia, Presenze, Gol, Assist, PMA, Titolarità, Gol subiti, Rigori parati e ulteriori colonne numeriche utili.
- Un nuovo upload statistiche sostituisce i dati statistici del precedente file, evitando valori vecchi sui giocatori non presenti nel nuovo file.
