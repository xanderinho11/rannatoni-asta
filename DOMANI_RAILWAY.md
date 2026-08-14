# Rannatoni v4 — messa online su Railway

## 1. GitHub
1. Crea un repository **privato** (es. `rannatoni-asta`).
2. Carica il contenuto di questa cartella nella root del repository.
3. `data/` è esclusa da `.gitignore` e non va caricata.

## 2. Railway
1. Railway → **New Project** → **Deploy from GitHub Repo**.
2. Seleziona il repository.
3. Railway userà il `Dockerfile` presente nel progetto.

## 3. Password Super Admin — obbligatoria
Nelle **Variables** crea:

```text
ADMIN_PASSWORD=UNA_PASSWORD_FORTE_SCELTA_DA_TE
```

La build Railway non parte con la password locale `asta2026`.

## 4. Volume persistente — obbligatorio
Aggiungi un Volume con mount path esatto:

```text
/app/data
```

Conterrà:
- `fantacalcio.db`
- `sessions.json`
- `rose_attuale.csv`
- `residui_attuale.csv` (senza PIN)
- backup automatici

## 5. Dominio
Servizio → **Settings → Networking → Generate Domain**.
Otterrai un URL HTTPS `*.up.railway.app`.

## 6. Collaudo
1. Apri `/health`.
2. Entra in `/admin`.
3. Carica Catalogo e Rose.
4. Genera i PIN temporanei e salvali.
5. Prova il primo accesso di un Rannatone: deve chiedere il cambio PIN obbligatorio.
6. Prova 2-3 utenti Pronti e verifica che una busta coinvolga solo loro.
7. Verifica apertura automatica al raggiungimento di tutte le risposte.
8. Prova **Apri buste ora** con un partecipante ancora in attesa.
9. Prova la modalità Spettatore e la sezione **Rose**.
10. Fai un redeploy e verifica che il Volume conservi il DB.

## Locale
Su Windows usa `backend/avvia.bat`. Password Super Admin locale: `asta2026`.
