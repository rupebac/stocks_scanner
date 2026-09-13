#!/usr/bin/env bash
# stocks_scanner service control
#
#   ./control.sh start     # launch the dashboard (venv auto-created on first run)
#   ./control.sh stop      # stop it
#   ./control.sh restart   # stop + start (pick up code/artifact changes)
#   ./control.sh status    # pid, health, served scan
#
#   PORT=9000 ./control.sh start    # custom port (default 8501)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8501}"
PIDFILE="data/dashboard.pid"
LOG="data/logs/dashboard.log"
PY=".venv/bin/python"

_is_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

_health() {
  curl -s --max-time 3 "http://localhost:${PORT}/_stcore/health" 2>/dev/null
}

_find_pid() {  # fallback when the pidfile is stale (pipefail-safe: empty on no match)
  pgrep -f "streamlit run dashboard/app.py.*--server.port ${PORT}" 2>/dev/null | head -1 || true
}

do_start() {
  if _is_running; then
    echo "[control] already running (pid $(cat "$PIDFILE")) -> http://localhost:${PORT}"
    exit 0
  fi
  local pid; pid=$(_find_pid || true)
  if [ -n "${pid:-}" ]; then
    echo "$pid" > "$PIDFILE"
    echo "[control] adopted orphaned dashboard (pid $pid)"
    exit 0
  fi

  if [ ! -x "$PY" ]; then
    echo "[control] first run: creating venv and installing requirements..."
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
  fi
  # load .env (FMP_API_KEY, SCANNER_UA) — safe for values with spaces/parens
  if [ -f .env ]; then
    while IFS='=' read -r k v; do
      if [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then export "$k=$v"; fi
    done < .env
  fi
  if [ -z "$(ls -A data/scans 2>/dev/null)" ]; then
    echo "[control] no scans on disk — running the first full scan (one-time, ~20-40 min)..."
    "$PY" -m scanner.scan
  fi

  mkdir -p data/logs
  nohup "$PY" -m streamlit run dashboard/app.py \
    --server.port "${PORT}" --server.headless true >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 1 30); do
    if [ "$(_health)" = "ok" ]; then
      echo "[control] started (pid $(cat "$PIDFILE")) -> http://localhost:${PORT}"
      echo "[control] serving scan: $(ls data/scans | sort | tail -1)"
      exit 0
    fi
    sleep 1
  done
  echo "[control] WARNING: process launched (pid $(cat "$PIDFILE")) but health check "
  echo "[control] did not pass in 30s — inspect ${LOG}" >&2
  exit 1
}

do_stop() {
  local pid=""
  _is_running && pid=$(cat "$PIDFILE")
  [ -z "$pid" ] && pid=$(_find_pid || true)
  if [ -z "${pid:-}" ]; then
    echo "[control] not running"
    rm -f "$PIDFILE"
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || { echo "[control] stopped (pid $pid)"; rm -f "$PIDFILE"; return 0; }
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "[control] force-stopped (pid $pid)"
}

do_status() {
  if _is_running; then
    echo "[control] running (pid $(cat "$PIDFILE")) | health: $(_health || echo down) | port ${PORT}"
    echo "[control] serving scan: $(ls data/scans | sort | tail -1) (artifacts: data/scans/$(ls data/scans | sort | tail -1))"
    echo "[control] log: ${LOG}"
  else
    echo "[control] not running"
  fi
}

case "${1:-}" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; do_start ;;
  status)  do_status ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
