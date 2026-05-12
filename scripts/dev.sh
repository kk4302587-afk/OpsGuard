#!/bin/bash
# OpsGuard Development Mode - starts both backend and frontend
# Usage: bash scripts/dev.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Starting OpsGuard in development mode..."
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""

# Start backend
cd "$PROJECT_DIR/backend"
if [ -d "venv" ]; then
    source venv/bin/activate
fi
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Start frontend
cd "$PROJECT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

# Trap to kill both on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM

echo ""
echo "Press Ctrl+C to stop both servers"
wait
