#!/usr/bin/env bash
set -Eeuo pipefail

TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/triforce}"
BIN_DIR="${TRIFORCE_BIN_DIR:-$TRIFORCE_DIR/bin}"
TARGET_USER="${TRIFORCE_USER:-zombie}"
TARGET_HOME="${TRIFORCE_USER_HOME:-$(getent passwd "$TARGET_USER" | cut -d: -f6)}"
NPM_GLOBAL="${TRIFORCE_NPM_BIN:-$TARGET_HOME/.npm-global/bin}"

[[ -d "$TRIFORCE_DIR" ]] || { echo "TriForce directory not found: $TRIFORCE_DIR" >&2; exit 1; }
[[ -n "$TARGET_HOME" ]] || { echo "Home not found for $TARGET_USER" >&2; exit 1; }
mkdir -p "$BIN_DIR"

make_wrapper() {
  local name="$1" config_home="$2" extra_env="$3"
  local binary="$NPM_GLOBAL/$name"
  [[ -x "$binary" ]] || binary="$(command -v "$name" 2>/dev/null || true)"
  if [[ -z "$binary" || ! -x "$binary" ]]; then
    echo "SKIP: $name binary not found" >&2
    return 0
  fi

  cat > "$BIN_DIR/${name}-triforce" <<WRAPPER
#!/usr/bin/env bash
set -Eeuo pipefail
export HOME="$config_home"
$extra_env
exec "$binary" "\$@"
WRAPPER
  chmod 0755 "$BIN_DIR/${name}-triforce"
  echo "OK: $BIN_DIR/${name}-triforce -> $binary"
}

RUNTIME_BASE="${TRIFORCE_CLI_CONFIG_DIR:-/var/tristar/cli-config}"
install -d -o "$TARGET_USER" -g "$TARGET_USER" -m 0750 \
  "$RUNTIME_BASE/claude" "$RUNTIME_BASE/codex" "$RUNTIME_BASE/gemini" "$RUNTIME_BASE/opencode"

# literal $HOME is intentional in generated wrapper environment
# shellcheck disable=SC2016
make_wrapper claude "$RUNTIME_BASE/claude" 'export CLAUDE_CONFIG_DIR="$HOME"'
# literal $HOME is intentional in generated wrapper environment
# shellcheck disable=SC2016
make_wrapper codex "$RUNTIME_BASE/codex" 'export CODEX_HOME="$HOME"'
# literal $HOME is intentional in generated wrapper environment
# shellcheck disable=SC2016
make_wrapper gemini "$RUNTIME_BASE/gemini" 'export GEMINI_CONFIG_DIR="$HOME"'
make_wrapper opencode "$RUNTIME_BASE/opencode" ''
