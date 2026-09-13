#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE_ROOT="${AILINUX_WORKSPACE_ROOT:-$HOME/workspace}"
LINK_ROOT="${AILINUX_LINK_ROOT:-$HOME}"
PUSH_REFS=0
LEGACY_LINKS=0
for arg in "$@"; do
  case "$arg" in
    --push-refs) PUSH_REFS=1 ;;
    --legacy-links) LEGACY_LINKS=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$WORKSPACE_ROOT"

projects() {
  cat <<'PROJECTS'
triforce|https://github.com/derleiti/triforce-dev-mcp.git
ai-coder|https://github.com/derleiti/ai-coder.git
ailinux-helper|https://github.com/derleiti/ailinux-helper.git
ailinux-ai-suite|https://github.com/derleiti/ailinux-loom.git
ailinux-app|https://github.com/derleiti/ailinux-app.git
ailinux-client|https://github.com/derleiti/ailinux-client.git
aiwindows-client|https://github.com/derleiti/aiwindows-client.git
AILinuX-ISO-Builder|https://github.com/derleiti/AILinuX-ISO-Builder.git
nova-telegram-bridge|https://github.com/derleiti/mistral-messenger-assistant.git
securemessenger|https://github.com/derleiti/securemessenger.git
trading-mcp|https://github.com/derleiti/ailinux-trading-mcp.git
copa|https://github.com/derleiti/copa.git
build_linux|https://github.com/derleiti/build_linux
PROJECTS
}

sync_one() {
  local name="$1" url="$2" repo="$WORKSPACE_ROOT/$1" link="$LINK_ROOT/$1"
  if [[ ! -d "$repo/.git" ]]; then
    echo "[$name] clone"
    git clone "$url" "$repo"
  fi

  if [[ "$LEGACY_LINKS" == 1 ]]; then
    if [[ -e "$link" && ! -L "$link" ]]; then
      if [[ "$(readlink -f "$link")" != "$(readlink -f "$repo")" ]]; then
        echo "[$name] WARN legacy path is a real directory; leaving it untouched" >&2
      fi
    else
      ln -sfn "workspace/$name" "$link"
    fi
  fi

  git -C "$repo" fetch --all --prune --quiet
  local branch
  branch="$(git -C "$repo" branch --show-current)"
  if [[ -z "$branch" ]]; then
    echo "[$name] WARN detached HEAD; fetched only" >&2
    return 0
  fi
  if ! git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    echo "[$name] WARN no origin/$branch; fetched only" >&2
    return 0
  fi
  if ! git -C "$repo" diff --quiet || ! git -C "$repo" diff --cached --quiet; then
    echo "[$name] WARN tracked changes present; refusing automatic merge" >&2
  else
    git -C "$repo" merge --ff-only "origin/$branch" >/dev/null
  fi
  if [[ "$PUSH_REFS" == 1 ]]; then
    git -C "$repo" push --all origin
    git -C "$repo" push --tags origin
  fi
  printf '[%s] %s %s\n' "$name" "$branch" "$(git -C "$repo" rev-parse --short HEAD)"
}

while IFS='|' read -r name url; do
  [[ -n "$name" ]] || continue
  sync_one "$name" "$url"
done < <(projects)
