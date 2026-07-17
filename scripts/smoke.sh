#!/usr/bin/env bash
# ResolveAI · full-stack smoke test (M15).
#
# Verifies a running stack end-to-end: API liveness, DB/MCP readiness, the web
# app, and a real chat round-trip through the SSE endpoint. Pass --up to build
# and start the stack first (and --down to tear it down at the end).
#
#   ./scripts/smoke.sh                 # smoke an already-running stack
#   ./scripts/smoke.sh --up            # bring the stack up, then smoke
#   ./scripts/smoke.sh --up --down     # up → smoke → down
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
WEB_URL="${WEB_URL:-http://localhost:3000}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.full.yml}"
UP=0
DOWN=0
for arg in "$@"; do
  case "$arg" in
    --up) UP=1 ;;
    --down) DOWN=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }

cleanup() {
  if [[ "$DOWN" == "1" ]]; then
    echo "→ tearing down stack"
    docker compose -f "$COMPOSE_FILE" down
  fi
}
trap cleanup EXIT

if [[ "$UP" == "1" ]]; then
  echo "→ building + starting stack ($COMPOSE_FILE)"
  docker compose -f "$COMPOSE_FILE" up --build -d
fi

# Poll a URL until it returns any HTTP status (i.e. is reachable), up to N tries.
wait_for() {
  local name="$1" url="$2" tries="${3:-60}"
  echo "→ waiting for $name ($url)"
  for ((i = 1; i <= tries; i++)); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      green "  ✓ $name is up (after ${i}s)"
      return 0
    fi
    sleep 1
  done
  red "  ✗ $name did not come up within ${tries}s"
  return 1
}

fail=0

# 1) API liveness (cheap, never touches deps).
wait_for "api /healthz" "$API_URL/healthz" 90 || fail=1

# 2) API readiness (DB reachable + MCP tools discovered). Reported, not fatal —
#    MCP stdio spawns can lag; the stack is still usable for the chat path.
echo "→ api /readyz"
ready="$(curl -fsS "$API_URL/readyz" 2>/dev/null || curl -s "$API_URL/readyz" 2>/dev/null || echo '{"status":"unreachable"}')"
echo "  $ready"
case "$ready" in
  *'"status":"ok"'*) green "  ✓ api ready" ;;
  *) red "  ! api degraded/unreachable (continuing)" ;;
esac

# 3) Web app serves HTML.
wait_for "web" "$WEB_URL/" 90 || fail=1

# 4) Chat round-trip through the SSE endpoint. A billing query avoids the KB so
#    it works even without a seeded knowledge base. Assert the stream opens and
#    emits at least one SSE frame.
echo "→ chat round-trip (POST $API_URL/api/v1/chat)"
chat_out="$(curl -sN --max-time 30 \
  -H 'Content-Type: application/json' \
  -d '{"message":"I was double charged on my last invoice, please refund it.","customer_id":"smoke-customer"}' \
  "$API_URL/api/v1/chat" 2>/dev/null | head -c 4000 || true)"
if echo "$chat_out" | grep -qE '^(event|data):'; then
  green "  ✓ chat stream produced SSE frames"
  echo "$chat_out" | grep -E '^event:' | head -5 | sed 's/^/    /'
else
  red "  ✗ chat stream produced no SSE frames"
  fail=1
fi

echo
if [[ "$fail" == "0" ]]; then
  green "SMOKE PASSED"
else
  red "SMOKE FAILED"
fi
exit "$fail"
