#!/usr/bin/env bash
# Start the existing voice worker and dashboard; keep their logs locally.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
ROOT="$PWD"
STATE="$ROOT/runs/launcher"
mkdir -p "$STATE"
chmod 700 "$STATE"
exec 9>"$STATE/start.lock"
flock 9

if [[ ! -x .venv/bin/python ]]; then
    command -v uv >/dev/null || { echo "Install uv, then run this launcher again."; exit 1; }
    uv sync --frozen
fi
PY="$ROOT/.venv/bin/python"
PORT=$("$PY" - <<'PY'
import os
from reprise.config import Settings, credential
Settings.from_env()
missing = [k for k in ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET') if not os.getenv(k)]
if not credential()[0]:
    missing.append('GOOGLE_API_KEY or GEMINI_API_KEY')
if missing:
    raise SystemExit('Configure .env or .env.local: ' + ', '.join(missing))
print(int(os.getenv('REPRISE_DEMO_PORT', '8844')))
PY
)
URL="http://127.0.0.1:$PORT/"

start_service() {
    local name="$1" pid="" log pattern candidate
    pattern="reprise\.cli $name"
    [[ "$name" != agent ]] || pattern='reprise\.(cli agent|agent start)'
    if [[ -f "$STATE/$name.pid" ]]; then
        read -r pid < "$STATE/$name.pid"
    fi
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null \
        && [[ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" == "$ROOT" ]] \
        && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Eq "$pattern"; then
        echo "$name is already running (PID $pid)."
        return
    fi
    for candidate in $(pgrep -f "$pattern" || true); do
        if [[ "$(readlink "/proc/$candidate/cwd" 2>/dev/null)" == "$ROOT" ]]; then
            echo "An unmanaged $name is already running (PID $candidate). Stop it before using this launcher."
            exit 1
        fi
    done
    if [[ "$name" == demo ]] && curl -fsS --max-time 2 "$URL" >/dev/null 2>&1; then
        echo "Port $PORT is already in use. Stop the existing dashboard before using this launcher."
        exit 1
    fi
    log="$STATE/$name-$(date +%Y%m%d-%H%M%S)-$$.log"
    nohup setsid "$PY" -m reprise.cli "$name" >> "$log" 2>&1 < /dev/null 9>&- &
    pid=$!
    printf '%s\n' "$pid" > "$STATE/$name.pid"
    printf '%s\n' "$log" > "$STATE/$name.logpath"
    chmod 600 "$STATE/$name.pid" "$STATE/$name.logpath" "$log"
    echo "Starting $name (PID $pid)."
}

start_service demo
start_service agent
echo "Waiting for dashboard and LiveKit worker…"
ready=false
for ((attempt=0; attempt<60; attempt++)); do
    read -r demo_pid < "$STATE/demo.pid"
    read -r agent_pid < "$STATE/agent.pid"
    if ! kill -0 "$demo_pid" 2>/dev/null || ! kill -0 "$agent_pid" 2>/dev/null; then
        echo "A service exited. Logs: $STATE"
        exit 1
    fi
    read -r agent_log < "$STATE/agent.logpath"
    if curl -fsS --max-time 1 "$URL" >/dev/null 2>&1 \
        && grep -q 'registered worker' "$agent_log"; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo "Startup did not finish within 60 seconds. Logs: $STATE"
    exit 1
fi
echo "Reprise ready: $URL"
echo "Logs: $STATE"
if [[ "${REPRISE_NO_BROWSER:-0}" != 1 ]] && command -v xdg-open >/dev/null; then
    nohup setsid xdg-open "$URL" >/dev/null 2>&1 < /dev/null 9>&- &
fi
