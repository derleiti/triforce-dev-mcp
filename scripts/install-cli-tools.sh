#!/usr/bin/env bash
# ============================================================
#  AILinux – AI CLI Installer  v2.0
#  Nodes: hetzner (10.10.0.1) · zombie-pc (10.10.0.2) · backup (10.10.0.3)
#
#  Warum v2:
#    v1 ist auf zombie-pc gestorben, weil in /home/zombie/.npmrc
#    "prefix=/root/.npm-global" stand -> jedes "npm i -g" als User
#    lief in EACCES auf /root/.npm-global. v1 hat die Zeile nur
#    auskommentiert (und das auch nur, wenn SUDO_USER gesetzt war),
#    statt sie auf den richtigen Wert zu setzen.
#
#  Was v2 anders macht:
#    1. prefix wird deterministisch GESETZT, nicht auskommentiert,
#       und zusaetzlich per npm_config_prefix erzwungen -> immun
#       gegen kaputte .npmrc-Reste.
#    2. Zielbenutzer wird robust ermittelt (SUDO_USER -> logname ->
#       UID 1000) statt still uebersprungen.
#    3. Node-Basis wird nach PAKETQUELLE geprueft, nicht nur nach
#       Versionsnummer. Ubuntu-npm 9.x wird erkannt und gemeldet.
#    4. Kein "set -e" mehr in der Install-Subshell: ein fehlender
#       Fremdinstaller killt nicht mehr den ganzen Lauf.
#    5. gemini wird tatsaechlich installiert (v1 hat es nur entfernt).
#    6. Alles idempotent, --dry-run, Logfile, Exit-Code nach Ergebnis.
#
#  Backup v1: ~/triforce/.backups/install-cli-tools-<timestamp>/
# ============================================================
set -Eeuo pipefail

VERSION="2.5"
TS="$(date +%Y%m%d-%H%M%S)"

# ---------- Flags ----------
DRY_RUN=0          # --dry-run        nichts aendern, nur zeigen
FIX_NODE=0         # --fix-node       Ubuntu-npm durch NodeSource ersetzen
PURGE_LEGACY=0     # --purge-legacy   Schatten-Symlinks in /usr/local/bin entfernen
WITH_EXTERNAL=1    # --no-external    antigravity/grok/vibe weglassen (Default: an)
WITH_ROOT=0        # --with-root       npm AI-CLIs zusaetzlich fuer root installieren
SKIP_APT=0         # --skip-apt       apt update/install ueberspringen
TARGET_USER=""     # --user NAME      Zielbenutzer erzwingen
ONLY=""            # --only TOOL      nur ein Tool

# ---------- Farben / Logging ----------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_GREEN=$'\033[1;32m'; C_CYAN=$'\033[1;36m'
  C_RED=$'\033[1;31m'; C_YEL=$'\033[1;33m'; C_DIM=$'\033[2m'
else
  C_RESET=""; C_GREEN=""; C_CYAN=""; C_RED=""; C_YEL=""; C_DIM=""
fi

LOG_DIR="/var/log/ailinux"
LOG_FILE="$LOG_DIR/install-cli-tools-$TS.log"

say()  { printf '\n%s=== %s ===%s\n' "$C_CYAN"  "$*" "$C_RESET"; }
log()  { printf '%s  ->%s %s\n'     "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s  !!%s %s\n'     "$C_YEL"   "$C_RESET" "$*"; }
err()  { printf '%s  XX%s %s\n'     "$C_RED"   "$C_RESET" "$*" >&2; }
dim()  { printf '%s     %s%s\n'     "$C_DIM"   "$*" "$C_RESET"; }

# ---------- Ergebnis-Sammlung ----------
declare -a RESULT_OK=() RESULT_FAIL=() RESULT_SKIP=()
ok()   { RESULT_OK+=("$1");   log "$1"; }
fail() { RESULT_FAIL+=("$1"); err "$1"; }
skip() { RESULT_SKIP+=("$1"); dim "uebersprungen: $1"; }

trap 'err "Unerwarteter Abbruch in Zeile $LINENO (Exit $?). Logfile: $LOG_FILE"' ERR

usage() {
  cat <<EOF
AILinux AI-CLI Installer v$VERSION

  sudo $0 [OPTIONEN]

  --dry-run         Nichts aendern, nur anzeigen was passieren wuerde
  --fix-node        NodeSource 22 nur als Fallback installieren, wenn Node fehlt/zu alt ist.
  --purge-legacy    Schatten-Symlinks in /usr/local/bin entfernen (mit Backup)
  --no-external     antigravity/grok/vibe weglassen (Default: werden installiert)
  --skip-apt        apt update/install ueberspringen
  --with-root       AI-CLIs zusaetzlich fuer root installieren (Default: aus)
  --user NAME       Zielbenutzer erzwingen statt automatisch zu ermitteln
  --only TOOL       Nur ein Tool: claude|gemini|opencode|copilot|codex|
                                  agy|grok|vibe
                    (agy = Antigravity CLI, so heisst dort das Binary)
  -h, --help        Diese Hilfe

Beispiele:
  sudo $0 --dry-run                 # erst schauen
  sudo $0                           # Standardlauf
  sudo $0 --fix-node --purge-legacy # zombie-pc geradeziehen
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)       DRY_RUN=1 ;;
    --fix-node)      FIX_NODE=1 ;;
    --purge-legacy)  PURGE_LEGACY=1 ;;
    --no-external)   WITH_EXTERNAL=0 ;;
    --with-external) WITH_EXTERNAL=1 ;;   # Kompatibilitaet zu v2.0
    --skip-apt)      SKIP_APT=1 ;;
    --with-root)      WITH_ROOT=1 ;;
    --user)          TARGET_USER="${2:-}"; shift ;;
    --only)          ONLY="${2:-}"; shift ;;
    -h|--help)       usage; exit 0 ;;
    *) err "Unbekannte Option: $1"; usage; exit 2 ;;
  esac
  shift
done

run() {  # run <beschreibung> <cmd...>
  local desc="$1"; shift
  if (( DRY_RUN )); then
    dim "[dry-run] $desc :: $*"
    return 0
  fi
  "$@"
}

# ============================================================
#  0 · Vorbedingungen
# ============================================================
need_root() {
  if [[ $EUID -ne 0 ]]; then
    err "Muss als root laufen:  sudo $0 $*"
    exit 1
  fi
}

# Zielbenutzer robust ermitteln. v1 hat hier still aufgegeben,
# wenn SUDO_USER leer war (z.B. Start aus einer root-Shell).
detect_target_user() {
  local u="" u_try=""
  if [[ -n "$TARGET_USER" ]]; then
    u="$TARGET_USER"
  elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    u="$SUDO_USER"
  elif u_try="$(logname 2>/dev/null)" && [[ -n "$u_try" && "$u_try" != "root" ]]; then
    u="$u_try"
  else
    # erster regulaerer Benutzer mit UID >= 1000
    u="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"
  fi
  [[ -n "$u" ]] || return 1
  getent passwd "$u" >/dev/null || return 1
  printf '%s' "$u"
}

home_of() { getent passwd "$1" | cut -d: -f6; }

# ============================================================
#  1 · Node/npm-Basis pruefen
#      Nicht "node -v | grep v22" wie in v1 - das sieht nur die
#      Zahl, nicht die Paketquelle. Genau daran ist zombie-pc
#      vorbeigerutscht: v22.22.1 aus Ubuntu universe bestand den
#      Test, brachte aber npm 9.2.0 statt 10.9.x mit.
# ============================================================
NODE_SRC="unbekannt"

inspect_node() {
  local user="${1:-${TARGET_USER:-}}"
  say "Node/npm-Basis pruefen"

  if ! command -v node >/dev/null 2>&1; then
    warn "node nicht installiert"
    return 1
  fi

  local node_ver node_pkg node_major
  node_ver="$(node -v 2>/dev/null || echo '?')"
  node_major="${node_ver#v}"; node_major="${node_major%%.*}"
  [[ "$node_major" =~ ^[0-9]+$ ]] || node_major=0
  node_pkg="$(dpkg-query -W -f='${Version}' nodejs 2>/dev/null || echo '')"

  if [[ "$node_pkg" == *nodesource* ]]; then NODE_SRC="nodesource"; elif [[ -n "$node_pkg" ]]; then NODE_SRC="distro"; fi
  dim "node          : $node_ver  (dpkg: ${node_pkg:-n/a})"
  dim "Paketquelle   : $NODE_SRC"

  if (( node_major < 20 )); then
    warn "Node.js $node_ver ist zu alt; mindestens Node 20 wird erwartet"
    return 1
  fi

  if [[ -n "$user" ]] && getent passwd "$user" >/dev/null; then
    local npm_ver=""
    npm_ver="$(npm_as "$user" 'npm -v' 2>/dev/null | tail -1 || true)"
    if [[ -n "$npm_ver" ]]; then
      dim "npm ($user)    : $npm_ver"
    else
      warn "npm fuer $user nicht gefunden; wird in der npm-Basis repariert"
    fi
  fi

  ok "Node-Basis brauchbar: $node_ver ($NODE_SRC)"
  return 0
}

fix_node_base() {
  # AILinux 26.04 ships a current Node 22 in the distro mirror. Do not
  # downgrade a newer distro Node just to match a NodeSource origin.
  if command -v node >/dev/null 2>&1; then
    local nv major
    nv="$(node -v 2>/dev/null || echo v0)"
    major="${nv#v}"; major="${major%%.*}"; [[ "$major" =~ ^[0-9]+$ ]] || major=0
    if (( major >= 20 )); then
      ok "Node.js $nv beibehalten; kein Provider-Wechsel erforderlich"
      return 0
    fi
  fi

  if (( ! FIX_NODE )); then
    warn "Node fehlt/ist zu alt. Mit --fix-node kann NodeSource 22 eingerichtet werden."
    skip "Node-Basis-Reparatur (kein --fix-node)"
    return 0
  fi

  say "NodeSource 22 als Fallback einrichten"
  local ns_tmp
  ns_tmp="$(mktemp)"
  if ! curl --fail --silent --show-error --location --retry 3 --connect-timeout 15 --max-time 120 \
       https://deb.nodesource.com/setup_22.x -o "$ns_tmp"; then
    rm -f "$ns_tmp"
    fail "NodeSource setup konnte nicht heruntergeladen werden"
    return 1
  fi
  run "NodeSource-Repo einrichten" bash "$ns_tmp"
  rm -f "$ns_tmp"
  run "Node.js installieren" apt-get install -y nodejs
  hash -r 2>/dev/null || true
  command -v node >/dev/null 2>&1 || { fail "Node.js Installation fehlgeschlagen"; return 1; }
  ok "Node.js installiert: $(node -v)"
}

# ============================================================
#  2 · .npmrc reparieren
#      DAS ist der eigentliche Fix. v1 hat "prefix=" nur
#      auskommentiert; blieb ein falscher Wert stehen (oder wurde
#      der User uebersprungen), lief npm weiter ins fremde Verzeichnis.
# ============================================================
ensure_npmrc() {
  local user="$1" home_dir npmrc want
  home_dir="$(home_of "$user")"
  [[ -n "$home_dir" ]] || { fail ".npmrc: Home fuer '$user' nicht gefunden"; return 1; }

  npmrc="$home_dir/.npmrc"
  want="$home_dir/.npm-global"

  # Verzeichnis anlegen und dem User geben
  run "mkdir $want" mkdir -p "$want"
  run "chown $user $want" chown -R "$user:$user" "$want"

  if (( DRY_RUN )); then
    dim "[dry-run] $npmrc -> prefix=$want (aktuell: $(grep -m1 '^prefix=' "$npmrc" 2>/dev/null || echo 'nicht gesetzt'))"
    return 0
  fi

  local current=""
  [[ -f "$npmrc" ]] && current="$(grep -m1 '^prefix=' "$npmrc" 2>/dev/null || true)"

  if [[ "$current" == "prefix=$want" ]]; then
    dim "$npmrc: prefix bereits korrekt"
  else
    [[ -f "$npmrc" ]] && cp -a "$npmrc" "$npmrc.bak-$TS"
    touch "$npmrc"
    # vorhandene prefix-Zeilen entfernen (auch die von v1 auskommentierten)
    sed -i -E '/^[[:space:]]*#?[[:space:]]*(# DISABLED BY AILINux[^:]*: )?prefix=/d' "$npmrc"
    printf 'prefix=%s\n' "$want" >>"$npmrc"
    [[ -f "$npmrc.bak-$TS" ]] && dim "Backup: $npmrc.bak-$TS"
    ok "$npmrc: prefix -> $want ${current:+(war: ${current#prefix=})}"
  fi

  chown "$user:$user" "$npmrc"
  chmod 600 "$npmrc"
}

ensure_path_rc() {
  local user="$1" home_dir line found=0
  home_dir="$(home_of "$user")"
  # Beide Verzeichnisse: .npm-global/bin (npm-Tools) und .local/bin
  # (vibe, grok, agy, uv).
  # Guard per case: verhindert, dass der Eintrag bei jedem Shell-Start
  # erneut vorne angehaengt wird. Ubuntus .profile setzt ~/.local/bin
  # bereits selbst - ohne Guard stand es dreimal im PATH.
  # literal shell snippet written into user rc files
  # shellcheck disable=SC2016
  line='for d in "$HOME/.npm-global/bin" "$HOME/.local/bin"; do case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac; done; export PATH'

  run "mkdir $home_dir/.local/bin" mkdir -p "$home_dir/.local/bin"
  run "chown .local/bin" chown "$user:$user" "$home_dir/.local" "$home_dir/.local/bin"

  # .profile nur als Fallback, wenn weder .bashrc noch .zshrc da sind -
  # sonst laeuft der Block bei Login-Shells doppelt.
  local rcs=("$home_dir/.bashrc" "$home_dir/.zshrc")
  [[ -f "$home_dir/.bashrc" || -f "$home_dir/.zshrc" ]] || rcs+=("$home_dir/.profile")

  for rc in "${rcs[@]}"; do
    [[ -f "$rc" ]] || continue
    found=1
    write_path_block "$user" "$rc" "$line"
  done

  if (( ! found )); then
    run "Lege $home_dir/.profile an" touch "$home_dir/.profile"
    write_path_block "$user" "$home_dir/.profile" "$line"
  fi
}

# Schreibt den PATH-Block idempotent zwischen Markern. Ein bereits
# vorhandener Block (auch die einzeilige Variante aus v2.0/v2.1) wird
# ersetzt, nicht ein zweites Mal angehaengt.
write_path_block() {
  local user="$1" rc="$2" line="$3"
  local begin="# >>> AILinux CLI-Tools >>>"
  local end="# <<< AILinux CLI-Tools <<<"

  if (( DRY_RUN )); then
    dim "[dry-run] PATH-Block in $rc setzen"
    return 0
  fi

  if grep -qF "$begin" "$rc" 2>/dev/null && \
     grep -qF "$line" "$rc" 2>/dev/null; then
    dim "$(basename "$rc"): PATH-Block aktuell"
    return 0
  fi

  cp -a "$rc" "$rc.bak-$TS"

  # 1. sauberen Marker-Block entfernen
  # 2. Altlast aus v2.0/v2.1 entfernen: Kommentarzeile + die Zeile danach
  sed -i -e "/^${begin}\$/,/^${end}\$/d" \
         -e '/^# AILinux CLI-Tools$/,+1d' "$rc"
  # evtl. zurueckgebliebene alte export-Zeilen
  # sed expression intentionally contains literal $HOME
  # shellcheck disable=SC2016
  sed -i '/^export PATH="\$HOME\/\.npm-global\/bin:/d' "$rc"

  { printf '\n%s\n%s\n%s\n' "$begin" "$line" "$end"; } >>"$rc"
  chown "$user:$user" "$rc"
  ok "$(basename "$rc"): PATH-Block gesetzt (Backup: $(basename "$rc").bak-$TS)"
}

# ============================================================
#  3 · Tool-Installation
# ============================================================
# Paket|Binary|Versions-Flag
# Paket|Binary|Versions-Flag|erlaubte Install-Scripts (npm 11/12)
NPM_TOOLS=(
  "@anthropic-ai/claude-code|claude|--version|@anthropic-ai/claude-code"
  "@google/gemini-cli|gemini|--version|"
  "opencode-ai|opencode|--version|opencode-ai"
  "@github/copilot|copilot|--version|@github/keytar,node-pty"
  "@openai/codex|codex|--version|"
)
# Fremdinstaller (download then execute). Alle drei installieren nach $HOME/.local/bin
# und wollen ausdruecklich OHNE root laufen -> nur fuer den Zielbenutzer,
# nicht fuer root (dort landeten sie in /root/.local/bin und waeren nutzlos).
# Format: Binary|URL|Zusatz-Binary (optional, nur fuer die Verifikation)
EXTERNAL_TOOLS=(
  "agy|https://antigravity.google/cli/install.sh|"     # Antigravity CLI heisst 'agy'
  "grok|https://x.ai/cli/install.sh|"
  "vibe|https://mistral.ai/vibe/install.sh|vibe-acp"   # Mistral Vibe, via uv
)

# npm im Kontext des Zielbenutzers ausfuehren.
# npm_config_prefix als Env-Variable schlaegt jede .npmrc - damit ist
# der Lauf auch dann korrekt, wenn die Datei doch wieder verbogen wird.
npm_as() {
  local user="$1"; shift
  local home_dir; home_dir="$(home_of "$user")"
  # .local/bin muss mit rein: dort landen vibe/grok/antigravity (uv bzw.
  # eigene Installer), und uv selbst liegt ebenfalls dort.
  sudo -u "$user" -H env \
      HOME="$home_dir" \
      npm_config_prefix="$home_dir/.npm-global" \
      PATH="$home_dir/.npm-global/bin:$home_dir/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      bash -c "cd '$home_dir' && $*"
}

install_npm_tool() {
  local user="$1" pkg="$2" bin="$3" allow_scripts="${4:-}"

  if [[ -n "$ONLY" && "$bin" != "$ONLY" ]]; then return 0; fi
  if (( DRY_RUN )); then
    dim "[dry-run] $user: npm install -g $pkg${allow_scripts:+ (allow-scripts=$allow_scripts)}"
    return 0
  fi

  local allow_arg=""
  [[ -n "$allow_scripts" ]] && allow_arg="--allow-scripts=$allow_scripts"
  if npm_as "$user" "npm install -g --no-fund --no-audit $allow_arg '$pkg'" \
       >>"$LOG_FILE" 2>&1; then
    ok "$user: $pkg installiert"
  else
    fail "$user: $pkg fehlgeschlagen (Details: $LOG_FILE)"
  fi
}

# Ensure a user-local npm that is compatible with the installed Node release.
# npm 12.0.x requires Node >=22.22.2, while AILinux 26.04 currently ships
# Node 22.22.1. npm 11.19.1 supports Node >=22.9.0 and is therefore the safe
# compatibility baseline until the distro Node moves forward.
ensure_user_npm() {
  local user="$1" home_dir node_ver npm_ver node_major node_minor node_patch npm_major
  home_dir="$(home_of "$user")"
  node_ver="$(node -v 2>/dev/null || echo v0.0.0)"
  IFS=. read -r node_major node_minor node_patch <<<"${node_ver#v}"
  node_major=${node_major:-0}; node_minor=${node_minor:-0}; node_patch=${node_patch:-0}

  npm_ver="$(npm_as "$user" 'npm -v' 2>/dev/null | tail -1 || true)"
  if [[ -z "$npm_ver" ]]; then
    warn "npm fuer $user fehlt; installiere distro npm als Bootstrap"
    run "npm Bootstrap installieren" apt-get install -y npm
    npm_ver="$(npm_as "$user" 'npm -v' 2>/dev/null | tail -1 || true)"
  fi
  [[ -n "$npm_ver" ]] || { fail "npm fuer $user konnte nicht bereitgestellt werden"; return 1; }
  npm_major="${npm_ver%%.*}"; [[ "$npm_major" =~ ^[0-9]+$ ]] || npm_major=0

  if (( npm_major >= 12 )) && (( node_major == 22 )) && \
     { (( node_minor < 22 )) || { (( node_minor == 22 )) && (( node_patch < 2 )); }; }; then
    warn "npm $npm_ver ist mit Node $node_ver nicht kompatibel; setze npm 11.19.1"
    npm_as "$user" "npm install -g --no-fund --no-audit npm@11.19.1" >>"$LOG_FILE" 2>&1 \
      || { fail "$user: npm 11.19.1 konnte nicht installiert werden"; return 1; }
    npm_ver="$(npm_as "$user" 'npm -v' 2>/dev/null | tail -1 || true)"
  fi

  ok "$user: npm $npm_ver auf Node $node_ver"
}

install_external() {
  local user="$1" name="$2" url="$3"
  if (( ! WITH_EXTERNAL )); then skip "$name (--no-external)"; return 0; fi
  if [[ -n "$ONLY" && "$name" != "$ONLY" ]]; then return 0; fi
  # Diese Installer wollen ausdruecklich ohne root laufen.
  if [[ "$user" == "root" ]]; then return 0; fi
  if (( DRY_RUN )); then dim "[dry-run] $user: download $url, then bash installer"; return 0; fi

  local home_dir tmp
  home_dir="$(home_of "$user")"
  tmp="$(mktemp --tmpdir="$home_dir" ".ailinux-${name}-installer.XXXXXX.sh")"
  chown "$user:$user" "$tmp"
  if ! sudo -u "$user" -H curl --fail --silent --show-error --location --retry 3 \
       --connect-timeout 15 --max-time 300 "$url" -o "$tmp" 2>>"$LOG_FILE"; then
    rm -f "$tmp"
    fail "$user: $name Download fehlgeschlagen (Details: $LOG_FILE)"
    return 0
  fi
  if [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    fail "$user: $name Installer ist leer"
    return 0
  fi
  if npm_as "$user" "bash '$tmp'" >>"$LOG_FILE" 2>&1; then
    ok "$user: $name installiert"
  else
    fail "$user: $name fehlgeschlagen (Details: $LOG_FILE)"
  fi
  rm -f "$tmp"
}

setup_user() {
  local user="$1" home_dir
  home_dir="$(home_of "$user")"
  [[ -n "$home_dir" ]] || { fail "Home fuer '$user' nicht gefunden"; return 0; }

  say "Tools fuer: $user  ($home_dir)"

  ensure_npmrc  "$user"
  ensure_path_rc "$user"

  for entry in "${NPM_TOOLS[@]}"; do
    IFS='|' read -r pkg bin _flag allow_scripts <<<"$entry"
    install_npm_tool "$user" "$pkg" "$bin" "$allow_scripts"
  done

  for entry in "${EXTERNAL_TOOLS[@]}"; do
    IFS='|' read -r name url _extra <<<"$entry"
    install_external "$user" "$name" "$url"
  done
}

# ============================================================
#  4 · Altlasten
# ============================================================
check_legacy() {
  say "Schatten-Installationen in /usr/local/bin pruefen"
  local found=() bin target

  for entry in "${NPM_TOOLS[@]}"; do
    IFS='|' read -r _pkg bin _flag _allow_scripts <<<"$entry"
    if [[ -L "/usr/local/bin/$bin" ]]; then
      target="$(readlink -f "/usr/local/bin/$bin" 2>/dev/null || echo '?')"
      if [[ "$target" == *node_modules* || "$target" == '?' ]]; then
        found+=("$bin")
        dim "/usr/local/bin/$bin -> $target"
      fi
    elif [[ -f "/usr/local/bin/$bin" ]]; then
      found+=("$bin"); dim "/usr/local/bin/$bin (echte Datei)"
    fi
  done

  if (( ${#found[@]} == 0 )); then
    ok "Keine Schatten-Binaries in /usr/local/bin"
    return 0
  fi

  warn "${#found[@]} Binary(s) in /usr/local/bin ueberschatten die User-Installation."
  dim  "PATH-Reihenfolge: /usr/local/bin kommt vor \$HOME/.npm-global/bin."

  if (( ! PURGE_LEGACY )); then
    warn "Nicht entfernt. Mit --purge-legacy werden sie (mit Backup) geloescht."
    skip "Legacy-Cleanup (kein --purge-legacy)"
    return 0
  fi

  local bdir="/var/backups/ailinux-legacy-$TS"
  run "Backup-Verzeichnis $bdir" mkdir -p "$bdir"
  for bin in "${found[@]}"; do
    if (( DRY_RUN )); then dim "[dry-run] entferne /usr/local/bin/$bin"; continue; fi
    { readlink -f "/usr/local/bin/$bin" 2>/dev/null || echo "(kein Symlink)"; } \
      >"$bdir/$bin.target"
    cp -a "/usr/local/bin/$bin" "$bdir/$bin" 2>/dev/null || true
    rm -f "/usr/local/bin/$bin"
    ok "entfernt: /usr/local/bin/$bin (Backup: $bdir/$bin)"
  done
}

cleanup_stale() {
  say "Alte/falsche Paketnamen entfernen"
  # Nur die historisch falschen Namen. Die korrekten Pakete bleiben unangetastet.
  if command -v snap >/dev/null 2>&1 && snap list 2>/dev/null | grep -qw gemini; then
    run "snap remove gemini" snap remove gemini || warn "snap remove gemini fehlgeschlagen (ignoriert)"
  fi
  if (( ! DRY_RUN )); then
    npm uninstall -g gemini gemini-cli >>"$LOG_FILE" 2>&1 || true
  fi
  ok "Cleanup durchgelaufen"
}

# ============================================================
#  5 · Prerequisites
# ============================================================
install_prereqs() {
  if (( SKIP_APT )); then skip "apt (--skip-apt)"; return 0; fi
  say "System-Prerequisites"
  run "apt update" apt-get update -y >>"$LOG_FILE" 2>&1 || warn "apt update mit Warnungen"
  # coreutils fuer sha256sum (Vibe-Installer prueft Checksummen),
  # python3 als Basis fuer uv/vibe.
  run "apt install" apt-get install -y --no-install-recommends \
      curl git ca-certificates build-essential python3 coreutils >>"$LOG_FILE" 2>&1 \
    || warn "apt install mit Warnungen (Details: $LOG_FILE)"
  ok "Prerequisites geprueft"
}

# ============================================================
#  6 · sudoers secure_path
# ============================================================
fix_sudo_path() {
  local user="$1" home_dir file tmp
  home_dir="$(home_of "$user")"
  file="/etc/sudoers.d/ailinux-npm"
  tmp="$(mktemp)"

  say "sudo secure_path ergaenzen"

  printf 'Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:%s/.npm-global/bin:%s/.local/bin"\n' \
    "$home_dir" "$home_dir" >"$tmp"

  if (( DRY_RUN )); then
    dim "[dry-run] $file <- $(cat "$tmp")"; rm -f "$tmp"; return 0
  fi

  # Syntaxpruefung VOR dem Aktivieren - eine kaputte sudoers-Datei
  # sperrt sonst sudo komplett aus.
  if ! visudo -cf "$tmp" >>"$LOG_FILE" 2>&1; then
    rm -f "$tmp"
    fail "sudoers-Snippet ist syntaktisch ungueltig - nicht aktiviert"
    return 0
  fi

  [[ -f "$file" ]] && cp -a "$file" "/var/backups/$(basename "$file").bak-$TS" 2>/dev/null || true
  install -m 0440 -o root -g root "$tmp" "$file"
  rm -f "$tmp"
  ok "$file geschrieben und mit visudo validiert"
}

# ============================================================
#  7 · Verifikation
# ============================================================
verify() {
  local user="$1" bin flag out
  say "Verifikation als '$user'"
  if (( DRY_RUN )); then dim "[dry-run] uebersprungen"; return 0; fi

  local vfail=0
  for entry in "${NPM_TOOLS[@]}"; do
    IFS='|' read -r _pkg bin flag _allow_scripts <<<"$entry"
    [[ -n "$ONLY" && "$bin" != "$ONLY" ]] && continue
    # Existenz und Versionsausgabe getrennt pruefen. Manche agentischen CLIs
    # initialisieren bei --version mehr als erwartet oder liefern je nach
    # Release keine Ausgabe. Das darf ein vorhandenes Binary nicht als
    # "NICHT GEFUNDEN" markieren.
    local bin_path=""
    bin_path="$(npm_as "$user" "command -v '$bin'" 2>/dev/null | tail -1 || true)"
    if [[ -n "$bin_path" ]]; then
      local vout rc
      set +e
      vout="$(npm_as "$user" "timeout 15s '$bin' '$flag' </dev/null" 2>&1)"
      rc=$?
      set -e
      out="$(printf '%s\n' "$vout" | tail -1)"
      if (( rc == 0 )); then
        printf '  %s%-12s%s %s\n' "$C_GREEN" "$bin" "$C_RESET" "${out:-vorhanden ($bin_path)}"
      else
        printf '  %s%-12s%s FEHLER (rc=%d): %s\n' "$C_RED" "$bin" "$C_RESET" "$rc" "${out:-keine Ausgabe}"
        vfail=1
      fi
    else
      printf '  %s%-12s%s NICHT GEFUNDEN\n' "$C_RED" "$bin" "$C_RESET"
      vfail=1
    fi
  done

  if (( WITH_EXTERNAL )); then
    for entry in "${EXTERNAL_TOOLS[@]}"; do
      IFS='|' read -r name _url extra <<<"$entry"
      for b in "$name" ${extra:+$extra}; do
        [[ -n "$ONLY" && "$name" != "$ONLY" ]] && continue
        # --version ist bei diesen Installern nicht garantiert; erst
        # Existenz pruefen, Version nur als Zugabe.
        if npm_as "$user" "command -v $b >/dev/null" >/dev/null 2>&1; then
          out="$(npm_as "$user" "$b --version 2>/dev/null || true" 2>/dev/null | tail -1)"
          printf '  %s%-12s%s %s\n' "$C_GREEN" "$b" "$C_RESET" "${out:-vorhanden}"
        else
          printf '  %s%-12s%s nicht gefunden (optional)\n' "$C_YEL" "$b" "$C_RESET"
        fi
      done
    done
  fi

  # Der Kern-Check: schreibt npm auch wirklich ins richtige Verzeichnis?
  local prefix; prefix="$(npm_as "$user" 'npm config get prefix' 2>&1 | tail -1)"
  local want; want="$(home_of "$user")/.npm-global"
  if [[ "$prefix" == "$want" ]]; then
    ok "npm prefix korrekt: $prefix"
  else
    fail "npm prefix falsch: '$prefix' (erwartet '$want')"
    vfail=1
  fi

  return $vfail
}

summary() {
  say "Zusammenfassung"
  printf '  %sOK        %s %d\n' "$C_GREEN" "$C_RESET" "${#RESULT_OK[@]}"
  printf '  %sSKIP      %s %d\n' "$C_YEL"   "$C_RESET" "${#RESULT_SKIP[@]}"
  printf '  %sFEHLER    %s %d\n' "$C_RED"   "$C_RESET" "${#RESULT_FAIL[@]}"
  if (( ${#RESULT_FAIL[@]} )); then
    printf '\n  Fehlgeschlagen:\n'
    printf '    - %s\n' "${RESULT_FAIL[@]}"
  fi
  printf '\n  Logfile : %s\n' "$LOG_FILE"
  # help text intentionally shows literal $SHELL
  # shellcheck disable=SC2016
  printf '  Neustart der Shell noetig:  exec "$SHELL" -l\n\n'
}

# ============================================================
#  MAIN
# ============================================================
main() {
  need_root "$@"

  mkdir -p "$LOG_DIR" 2>/dev/null || LOG_FILE="/tmp/install-cli-tools-$TS.log"
  : >"$LOG_FILE"

  say "AILinux AI-CLI Installer v$VERSION auf $(hostname)"
  dim "Datum   : $(date -Is)"
  dim "Logfile : $LOG_FILE"
  (( DRY_RUN )) && warn "DRY-RUN: es wird nichts veraendert"

  local user
  if ! user="$(detect_target_user)"; then
    err "Kein Zielbenutzer ermittelbar. Bitte mit --user NAME angeben."
    exit 1
  fi
  dim "Zielbenutzer: $user  (Home: $(home_of "$user"))"

  if ! inspect_node "$user"; then
    fix_node_base
  fi

  install_prereqs
  cleanup_stale

  ensure_user_npm "$user"
  setup_user "$user"

  if (( WITH_ROOT )) && [[ "$user" != "root" ]]; then
    if command -v npm >/dev/null 2>&1; then
      ensure_user_npm root
      setup_user root
    else
      warn "--with-root angefordert, aber systemweites npm fehlt; Root-Installation uebersprungen"
      skip "Root AI-CLI Installation (npm fehlt)"
    fi
  fi

  check_legacy
  fix_sudo_path "$user"

  local vrc=0
  verify "$user" || vrc=1

  summary

  if (( ${#RESULT_FAIL[@]} > 0 || vrc )); then
    err "Lauf mit Fehlern beendet."
    exit 1
  fi
  say "FERTIG - alles sauber"
  exit 0
}

main "$@"
