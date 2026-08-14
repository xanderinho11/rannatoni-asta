#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-asta2026}"

if [ ! -d "venv" ]; then
  echo "Primo avvio: preparo l'ambiente Python..."
  python3 -m venv venv
  source venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
else
  source venv/bin/activate
fi

echo ""
echo "============================================================"
echo "  RANNATONI - ASTA DI RIPARAZIONE - SERVER LOCALE"
echo "  PC: http://localhost:8000"
echo "  Admin: http://localhost:8000/admin"
echo "  Password Super Admin locale: $ADMIN_PASSWORD"
if command -v hostname >/dev/null 2>&1; then
  for ip in $(hostname -I 2>/dev/null || true); do echo "  Telefono: http://$ip:8000"; done
fi
echo "  Dati persistenti: nessun reset automatico."
echo "============================================================"
echo ""
uvicorn main:app --host 0.0.0.0 --port 8000
