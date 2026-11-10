#!/usr/bin/env bash
# Start MatOpt with one command: installs on first run, starts the backend and
# frontend, waits until they answer, and opens the browser. Ctrl-C stops both.
# Set MATOPT_NO_BROWSER=1 to skip opening the browser.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT=8000
FRONTEND_PORT=5173
APP_URL="http://localhost:$FRONTEND_PORT"

fail() { echo "MatOpt: $1" >&2; exit 1; }

cd "$ROOT"
[ -f backend/.env ] || cp backend/.env.example backend/.env
if [ -z "${DEEPSEEK_API_KEY:-}" ] && ! grep -Eq '^DEEPSEEK_API_KEY=[^[:space:]#]' backend/.env; then
  fail "add your key to backend/.env (DEEPSEEK_API_KEY=...), then run this again."
fi

if [ ! -x backend/.venv/bin/python ] || [ ! -d frontend/node_modules ]; then
  echo "First run: installing dependencies (a few minutes)..."
  ./scripts/setup.sh
elif [ frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]; then
  echo "Frontend dependencies changed: updating them..."
  (cd frontend && npm ci)
fi

# Linux runs agent code under bubblewrap, so find out now rather than at the first tool call.
sandbox_mode="${SANDBOX_MODE:-$(sed -n 's/^SANDBOX_MODE=\([a-z-]*\).*/\1/p' backend/.env | tail -1)}"
if [ "$(uname -s)" = "Linux" ] && [ "$sandbox_mode" != "off" ] && [ "$sandbox_mode" != "external" ]; then
  command -v bwrap >/dev/null 2>&1 \
    || fail "install bubblewrap (sudo apt install bubblewrap), or set SANDBOX_MODE=off in backend/.env."
  bwrap --unshare-user --ro-bind / / true 2>/dev/null \
    || fail "bubblewrap cannot create a sandbox here. On Ubuntu run: sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0"
fi

for port in "$BACKEND_PORT" "$FRONTEND_PORT" 5174; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $port is already in use. Stop that program, then run this again (lsof -nP -iTCP:$port -sTCP:LISTEN shows it)."
  fi
done

BACKEND_PID=""
FRONTEND_PID=""
stop() {
  trap - INT TERM EXIT
  echo
  echo "Stopping MatOpt..."
  for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
  done
  wait 2>/dev/null || true
}
trap stop EXIT
trap 'exit 0' INT TERM

# This overrides an older backend/.env that still says HOST=0.0.0.0.
(cd backend && HOST=127.0.0.1 PORT="$BACKEND_PORT" exec .venv/bin/python main.py) &
BACKEND_PID=$!
(cd frontend && exec node_modules/.bin/vite --port "$FRONTEND_PORT" --strictPort) &
FRONTEND_PID=$!

wait_for() {
  local url="$1" name="$2" pid="$3"
  for _ in $(seq 1 120); do
    if curl -fs -o /dev/null -m 2 "$url"; then return 0; fi
    kill -0 "$pid" 2>/dev/null || fail "the $name stopped during startup. See the messages above."
    sleep 1
  done
  fail "the $name did not answer within two minutes. See the messages above."
}

wait_for "http://127.0.0.1:$BACKEND_PORT/health" backend "$BACKEND_PID"
wait_for "$APP_URL" frontend "$FRONTEND_PID"

echo
echo "MatOpt is ready at $APP_URL  (Ctrl-C to stop)"
echo
if [ -n "${MATOPT_NO_BROWSER:-}" ]; then :
elif command -v open >/dev/null 2>&1; then open "$APP_URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$APP_URL" >/dev/null 2>&1 || true
fi

while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done
fail "a service stopped unexpectedly. See the messages above."
