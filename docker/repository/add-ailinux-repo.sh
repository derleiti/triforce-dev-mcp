#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# AILinux Repo Adder v7.0
# ============================================================================

DEFAULT_BASE="https://repo.ailinux.me/mirror"
BASE_URL="${AILINUX_REPO_BASE:-$DEFAULT_BASE}"
KEYRING_DIR="/usr/share/keyrings"
KEYRING_PATH="${KEYRING_DIR}/ailinux-archive-keyring.gpg"
LIST_PATH="/etc/apt/sources.list.d/ailinux-mirror.list"
MANIFEST_URL="${BASE_URL}/mirror-repos.tsv"
CURL_OPTS=(-4 --connect-timeout 10 --max-time 30 --retry 2 --retry-delay 1)

VERBOSE=0
DRY_RUN=0
SKIP_UPDATE=0
LIST_REPOS=0
INSTALLED_ONLY=0
SELECTED_IDS=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[*]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[+]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_debug() { [[ $VERBOSE -eq 1 ]] && echo -e "${CYAN}[DBG]${NC} $*" >&2 || true; }

usage() {
    cat <<'EOF'
AILinux Repo Adder v7.0

Usage:
  curl -fsSL https://repo.ailinux.me/mirror/add-ailinux-repo.sh | sudo bash
  curl -fsSL https://repo.ailinux.me/mirror/add-ailinux-repo.sh | sudo bash -s -- --select docker-resolute,vscode-stable
  sudo ./add-ailinux-repo.sh [OPTIONS]

Options:
  --select ID,...     Install only selected mirror entries
  --installed-only    Install mirror replacements only for upstream repos already configured locally
  --list-repos        List currently published mirror entries and exit
  --no-update         Skip apt-get update
  --dry-run           Show generated sources without writing files
  --verbose, -v       Enable debug output
  -h, --help          Show help

Notes:
  The published mirror manifest is generated from mirror.list and the metadata
  that actually exists on repo.ailinux.me. No separate third-party mode exists.
EOF
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        log_error "Required command not found: $1"
        exit 1
    }
}

contains_csv() {
    local needle="$1" csv="$2" item
    for item in ${csv//,/ }; do
        [[ "$item" == "$needle" ]] && return 0
    done
    return 1
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --select) shift; SELECTED_IDS="${1:-}" ;;
            --select=*) SELECTED_IDS="${1#--select=}" ;;
            --installed-only) INSTALLED_ONLY=1 ;;
            --list-repos) LIST_REPOS=1 ;;
            --no-update) SKIP_UPDATE=1 ;;
            --dry-run) DRY_RUN=1 ;;
            --verbose|-v) VERBOSE=1 ;;
            -h|--help) usage; exit 0 ;;
            *) log_error "Unknown option: $1"; usage; exit 1 ;;
        esac
        shift
    done
}

detect_codename() {
    local codename=""
    if command -v lsb_release >/dev/null 2>&1; then
        codename=$(lsb_release -cs 2>/dev/null || true)
    fi
    if [[ -z "$codename" && -r /etc/os-release ]]; then
        codename=$(bash -c '. /etc/os-release; printf "%s" "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"' 2>/dev/null || true)
    fi
    printf '%s\n' "${codename:-unknown}"
}

fetch_manifest() {
    curl "${CURL_OPTS[@]}" -fsSL "$MANIFEST_URL"
}

normalize_uri() {
    local uri="$1"
    uri="${uri#http://}"
    uri="${uri#https://}"
    uri="${uri%/}"
    printf '%s\n' "$uri"
}

collect_existing_source_uris() {
    local file line uri
    local -a files=()
    [[ -f /etc/apt/sources.list ]] && files+=(/etc/apt/sources.list)
    while IFS= read -r -d '' file; do
        [[ "$file" == "$LIST_PATH" ]] && continue
        files+=("$file")
    done < <(find /etc/apt/sources.list.d -maxdepth 1 -type f \( -name '*.list' -o -name '*.sources' \) -print0 2>/dev/null)

    for file in "${files[@]}"; do
        case "$file" in
            *.sources)
                while IFS= read -r line; do
                    [[ "$line" =~ ^[[:space:]]*URIs:[[:space:]]+(.+)$ ]] || continue
                    for uri in ${BASH_REMATCH[1]}; do normalize_uri "$uri"; done
                done < "$file"
                ;;
            *)
                while IFS= read -r line; do
                    [[ "$line" =~ ^[[:space:]]*deb[[:space:]] ]] || continue
                    line=$(sed -E 's/^[[:space:]]*deb[[:space:]]+(\[[^]]+\][[:space:]]+)?//' <<<"$line")
                    uri=${line%%[[:space:]]*}
                    [[ -n "$uri" ]] && normalize_uri "$uri"
                done < "$file"
                ;;
        esac
    done | sort -u
}

source_is_installed() {
    local upstream="$1" mirror_path="$2" normalized mirror_normalized existing
    normalized=$(normalize_uri "$upstream")
    mirror_normalized=$(normalize_uri "${BASE_URL}/${mirror_path}")
    while IFS= read -r existing; do
        [[ -n "$existing" ]] || continue
        if [[ "$existing" == "$normalized" || "$existing" == "$normalized/"* || "$normalized" == "$existing/"* ||               "$existing" == "$mirror_normalized" || "$existing" == "$mirror_normalized/"* || "$mirror_normalized" == "$existing/"* ]]; then
            return 0
        fi
    done <<<"${EXISTING_URIS:-}"
    return 1
}

repo_matches_os() {
    local target_codename="$1" current_codename="$2"
    [[ -z "$target_codename" || "$target_codename" == "*" ]] && return 0
    [[ "$target_codename" == "$current_codename" ]]
}

install_keyring() {
    if [[ $DRY_RUN -eq 1 ]]; then
        log_info "[DRY-RUN] Would install ${BASE_URL}/ailinux-archive-key.gpg -> ${KEYRING_PATH}"
        return 0
    fi
    mkdir -p "$KEYRING_DIR"
    curl "${CURL_OPTS[@]}" -fsSL "${BASE_URL}/ailinux-archive-key.gpg" -o "$KEYRING_PATH"
    chmod 0644 "$KEYRING_PATH"
    log_ok "Keyring installed: $KEYRING_PATH"
}

build_sources() {
    local manifest="$1" current_codename="$2"
    local id label category path suite components arches upstream target_codename
    local discovered=0 selected=0 skipped_os=0 skipped_not_installed=0
    local line_content=""

    while IFS=$'\t' read -r id label category path suite components arches upstream target_codename; do
        [[ "$id" == "id" ]] && continue
        [[ -n "$id" ]] || continue
        ((discovered++)) || true

        if [[ -n "$SELECTED_IDS" ]] && ! contains_csv "$id" "$SELECTED_IDS"; then
            continue
        fi
        if ! repo_matches_os "$target_codename" "$current_codename"; then
            log_debug "Skipping $id: target=$target_codename current=$current_codename"
            ((skipped_os++)) || true
            continue
        fi
        if [[ $INSTALLED_ONLY -eq 1 && "$upstream" != local://* ]] && ! source_is_installed "$upstream" "$path"; then
            log_debug "Skipping $id: upstream not currently configured ($upstream)"
            ((skipped_not_installed++)) || true
            continue
        fi

        ((selected++)) || true
        line_content+=$'\n'"# ${label} [${id}]"
        if [[ "$suite" == "/" ]]; then
            line_content+=$'\n'"deb [arch=${arches} signed-by=${KEYRING_PATH}] ${BASE_URL}/${path} /"
        else
            [[ "$components" == "-" ]] && components=""
            line_content+=$'\n'"deb [arch=${arches} signed-by=${KEYRING_PATH}] ${BASE_URL}/${path} ${suite} ${components}"
        fi
    done <<<"$manifest"

    log_info "Published entries: $discovered; selected: $selected; OS-skipped: $skipped_os; installed-only skipped: $skipped_not_installed" >&2
    printf '%s\n' "$line_content"
}

main() {
    parse_args "$@"

    echo -e "\n${BOLD}╔════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║  AILinux Repo Adder v7.0                   ║${NC}"
    echo -e "${BOLD}║  mirror-only, manifest driven              ║${NC}"
    echo -e "${BOLD}╚════════════════════════════════════════════╝${NC}\n"

    if [[ $DRY_RUN -eq 0 && $LIST_REPOS -eq 0 && $EUID -ne 0 ]]; then
        log_error "This script must be run as root (sudo). Use --dry-run or --list-repos to preview."
        exit 1
    fi

    need_cmd curl
    need_cmd apt-get

    local manifest current_codename sources
    current_codename=$(detect_codename)
    log_info "Detected OS codename: $current_codename"
    log_info "Fetching mirror manifest: $MANIFEST_URL"
    manifest=$(fetch_manifest) || {
        log_error "Could not fetch published mirror manifest"
        exit 1
    }

    if [[ $LIST_REPOS -eq 1 ]]; then
        printf '%s\n' "$manifest"
        exit 0
    fi

    if [[ $INSTALLED_ONLY -eq 1 ]]; then
        EXISTING_URIS=$(collect_existing_source_uris)
        export EXISTING_URIS
        log_info "Installed-only mode: replacing only detected upstream repositories"
    fi

    install_keyring
    sources=$(build_sources "$manifest" "$current_codename")

    if [[ -z "${sources//[[:space:]]/}" ]]; then
        log_error "No matching mirror repositories selected"
        exit 1
    fi

    local header
    header=$(cat <<EOF
# ============================================
# AILinux Mirror Repositories
# Auto-generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Base URL: $BASE_URL
# System: $current_codename
# Source manifest: $MANIFEST_URL
# ============================================
EOF
)

    if [[ $DRY_RUN -eq 1 ]]; then
        printf '%s\n%s\n' "$header" "$sources"
    else
        printf '%s\n%s\n' "$header" "$sources" > "$LIST_PATH"
        chmod 0644 "$LIST_PATH"
        log_ok "Mirror sources written to $LIST_PATH"
    fi

    if [[ $SKIP_UPDATE -eq 0 && $DRY_RUN -eq 0 ]]; then
        log_info "Running apt-get update..."
        apt-get update
    fi

    log_ok "Done. Only AILinux mirror repositories are managed by this script."
}

main "$@"
