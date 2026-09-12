#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
CONFIG_FILE="${TRIFORCE_CONFIG_FILE:-$ROOT_DIR/config/triforce.env}"
config_value() { awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$CONFIG_FILE" 2>/dev/null || true; }
BIND_HOST="${TRIFORCE_BIND_HOST:-$(config_value TRIFORCE_BIND_HOST)}"
API_PORT="${TRIFORCE_API_PORT:-$(config_value TRIFORCE_API_PORT)}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-9000}"
[[ "$BIND_HOST" == "0.0.0.0" ]] && BIND_HOST="127.0.0.1"
BASE_URL="${BASE_URL:-http://${BIND_HOST}:${API_PORT}}"
TIMEOUT="${TIMEOUT:-120}"
[[ $# -ge 1 ]] || { echo "Usage: $0 task" >&2; exit 1; }
TASK="$*"
call_agent() {
  local agent="$1" msg="$2"
  local payload
  payload=$(python3 -c 'import json,sys; print(json.dumps({"message":sys.argv[1],"timeout":int(sys.argv[2])}))' "$msg" "$TIMEOUT")
  curl -fsS --max-time "$((TIMEOUT + 15))" -X POST "$BASE_URL/v1/tristar/cli-agents/${agent}/call" -H "Content-Type: application/json" -d "$payload"
}
clean_json() {
  python3 -c 'import json,sys,re; data=json.load(sys.stdin); resp=data.get("response",""); lines=[line for line in resp.splitlines() if line.strip() and not line.strip().startswith("[fallback-from:") and not re.fullmatch(r"\d[\d,.]*", line.strip())]; data["response"]="\n".join(lines).strip(); print(json.dumps(data, indent=2, ensure_ascii=False))'
}
json_field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1],""))' "$1"; }
echo "==> Versuch: gemini-mcp" >&2
GEMINI_JSON=$(call_agent gemini-mcp "$TASK")
GEMINI_RESPONSE=$(printf "%s" "$GEMINI_JSON" | json_field response)
GEMINI_STATUS=$(printf "%s" "$GEMINI_JSON" | json_field status)
if [[ "$GEMINI_STATUS" != "ok" ]] || printf "%s" "$GEMINI_RESPONSE" | grep -qiE "resource_exhausted|model_capacity_exhausted|no capacity available|status 429"; then
  echo "==> Gemini status=${GEMINI_STATUS:-unknown}, Fallback auf codex-mcp" >&2
  call_agent codex-mcp "[fallback-from:gemini-mcp] $TASK" | clean_json
else
  printf "%s" "$GEMINI_JSON" | clean_json
fi
