#!/usr/bin/env bash
# ============================================================================
# AILinux Master Update Script v3.0
# ============================================================================
# Main orchestrator for the mirror update pipeline
#
# Pipeline Steps:
#   1. Container Setup    - Start Docker container, fix permissions
#   2. Download           - apt-mirror downloads packages
#   3. Postmirror         - DEP-11 validation and cleanup
#   4. Compression Fix    - Regenerate .gz/.xz for hash consistency
#   5. Signing            - GPG sign all Release files
#   6. Key Export         - Export public key for clients
#   7. Index Generation   - Generate HTML index
#
# Usage:
#   ./update-mirror.sh [OPTIONS]
#
# Options:
#   --skip-download     Skip apt-mirror download step
#   --skip-dep11        Skip DEP-11 validation
#   --sign-only         Only run signing step
#   --dry-run           Show what would be done
#   -h, --help          Show this help
# ============================================================================

# Bash Guard
if [ -z "${BASH_VERSION:-}" ]; then exec /usr/bin/env bash "$0" "$@"; fi
set -u

# ==================== CONFIGURATION ====================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="${REPO_ROOT:-$SCRIPT_DIR}"
LOG_DIR="${REPO_ROOT}/log"
LOGFILE="${LOG_DIR}/update-mirror.log"
LOCKFILE="${LOG_DIR}/apt-mirror.update.lock"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
SERVICE="apt-mirror"

# Host scripts (prefer local repo root, fallback to project scripts path)
HEAL_PERMS_SCRIPT="${REPO_ROOT}/heal-perms.sh"
COMPRESS_FIX_SCRIPT="${REPO_ROOT}/fix-packages-compression.sh"
PUBLIC_KEY_SCRIPT="${REPO_ROOT}/export-public-key.sh"
SIGN_REPOS_SCRIPT="${REPO_ROOT}/sign-repos.sh"
INSTALLER_SOURCE="${REPO_ROOT}/add-ailinux-repo.sh"
MIRROR_MANIFEST_SCRIPT="${REPO_ROOT}/generate-mirror-manifest.py"

if [[ ! -f "$HEAL_PERMS_SCRIPT" && -f "/home/zombie/triforce/scripts/docker/repository/heal-perms.sh" ]]; then
  HEAL_PERMS_SCRIPT="/home/zombie/triforce/scripts/docker/repository/heal-perms.sh"
fi
if [[ ! -f "$COMPRESS_FIX_SCRIPT" && -f "/home/zombie/triforce/scripts/docker/repository/fix-packages-compression.sh" ]]; then
  COMPRESS_FIX_SCRIPT="/home/zombie/triforce/scripts/docker/repository/fix-packages-compression.sh"
fi
if [[ ! -f "$PUBLIC_KEY_SCRIPT" && -f "/home/zombie/triforce/scripts/docker/repository/export-public-key.sh" ]]; then
  PUBLIC_KEY_SCRIPT="/home/zombie/triforce/scripts/docker/repository/export-public-key.sh"
fi
if [[ ! -f "$SIGN_REPOS_SCRIPT" && -f "/home/zombie/triforce/scripts/docker/repository/sign-repos.sh" ]]; then
  SIGN_REPOS_SCRIPT="/home/zombie/triforce/scripts/docker/repository/sign-repos.sh"
fi

# Mirror paths - auto-detected from ./repo/mirror
MIRROR_ROOT="${REPO_ROOT}/repo/mirror"
GNUPG_HOME="${REPO_ROOT}/etc/gnupg"

# Signing key: shared signing-key.env if available, else built-in default.
# Keep the fallback in sync with signing-key.env (see that file for details).
[[ -r "${REPO_ROOT}/signing-key.env" ]] && . "${REPO_ROOT}/signing-key.env"
SIGNING_KEY_ID="${SIGNING_KEY_ID:-59FAE19560F5E25B}"

# Runtime options
SKIP_DOWNLOAD=0
SKIP_DEP11=0
SIGN_ONLY=0
DRY_RUN=0

# Timing
declare -A STEP_TIMES
TOTAL_START=0

# ==================== COLORS & LOGGING ====================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Timestamp function
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Logging functions
log()      { echo -e "[$(ts)] ${BLUE}[INFO]${NC}  $*" | tee -a "$LOGFILE"; }
log_ok()   { echo -e "[$(ts)] ${GREEN}[OK]${NC}    $*" | tee -a "$LOGFILE"; }
log_warn() { echo -e "[$(ts)] ${YELLOW}[WARN]${NC}  $*" | tee -a "$LOGFILE"; }
log_err()  { echo -e "[$(ts)] ${RED}[ERROR]${NC} $*" | tee -a "$LOGFILE" >&2; }
log_step() { echo -e "\n[$(ts)] ${CYAN}${BOLD}=== $* ===${NC}" | tee -a "$LOGFILE"; }

# Header for log sections
log_header() {
    local msg="$1"
    local line
    line=$(printf '=%.0s' {1..60})
    echo -e "\n${BOLD}${line}${NC}" | tee -a "$LOGFILE"
    echo -e "${BOLD}  $msg${NC}" | tee -a "$LOGFILE"
    echo -e "${BOLD}${line}${NC}\n" | tee -a "$LOGFILE"
}

# ==================== HELPER FUNCTIONS ====================

usage() {
    cat <<'EOF'
AILinux Mirror Update Script v3.0

Usage:
  ./update-mirror.sh [OPTIONS]

Options:
  --skip-download     Skip apt-mirror download step (useful for re-signing)
  --skip-dep11        Skip DEP-11 validation (faster updates)
  --sign-only         Only run signing step (skip download, dep11, compression)
  --dry-run           Show what would be done without executing
  -h, --help          Show this help

Examples:
  # Full update pipeline
  ./update-mirror.sh

  # Re-sign without downloading
  ./update-mirror.sh --skip-download

  # Quick signing only
  ./update-mirror.sh --sign-only

Environment Variables:
  REPO_ROOT           Override repository root directory
  COMPOSE_FILE        Override docker-compose.yml path
EOF
}

# Timer functions
timer_start() {
    STEP_TIMES["$1"]=$(date +%s)
}

timer_end() {
    local step="$1"
    local start=${STEP_TIMES["$step"]:-$(date +%s)}
    local end
    end=$(date +%s)
    local duration=$((end - start))
    local mins=$((duration / 60))
    local secs=$((duration % 60))
    echo -e "${CYAN}   Duration: ${mins}m ${secs}s${NC}" | tee -a "$LOGFILE"
}

# Check if command exists
check_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        log_err "Required command not found: $1"
        return 1
    fi
}

# Auto-detect actual APT repository roots in ./repo/mirror.
# A repository root is the directory directly containing dists/, or a flat
# repository containing Release + Packages metadata. Paths are reported
# relative to MIRROR_ROOT so nested mirrors and multiple PPAs stay distinct.
detect_mirror_repos() {
    local mirror_root="$1"
    local repo_dir release_file rel
    local -a repos=()
    declare -A seen=()

    if [[ ! -d "$mirror_root" ]]; then
        log_warn "Mirror root not found: $mirror_root"
        return 1
    fi

    while IFS= read -r -d '' repo_dir; do
        rel="${repo_dir#${mirror_root}/}"
        rel="${rel%/dists}"
        [[ -n "$rel" ]] && seen["$rel"]=1
    done < <(find "$mirror_root" -type d -name dists -print0 2>/dev/null)

    while IFS= read -r -d '' release_file; do
        repo_dir="$(dirname "$release_file")"
        if [[ -f "$repo_dir/Packages" || -f "$repo_dir/Packages.gz" || -f "$repo_dir/Packages.xz" ]]; then
            rel="${repo_dir#${mirror_root}/}"
            [[ -n "$rel" ]] && seen["$rel"]=1
        fi
    done < <(find "$mirror_root" -type f -name Release ! -path '*/dists/*' -print0 2>/dev/null)

    if [[ ${#seen[@]} -eq 0 ]]; then
        log_warn "No APT repositories found in $mirror_root"
        return 1
    fi

    while IFS= read -r rel; do
        [[ -n "$rel" ]] && repos+=("$rel")
    done < <(printf '%s\n' "${!seen[@]}" | sort)

    log "Found ${#repos[@]} actual mirror repository root(s):"
    for rel in "${repos[@]}"; do
        echo "   📦 $rel" | tee -a "$LOGFILE"
    done

    return 0
}

# List mirror folders for signing (auto-detection)
list_mirror_folders() {
    local mirror_root="$1"

    if [[ ! -d "$mirror_root" ]]; then
        return 1
    fi

    # List all top-level directories in mirror root
    find "$mirror_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
}

# Docker compose wrapper with error handling
dc() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

# Use 'run' for apt-mirror since it's in a profile
dc_run() {
    dc --profile mirror run --rm -T "$SERVICE" "$@"
}

dc_exec() {
    # For apt-mirror, use run instead of exec since it's a profiled service
    if [[ "$SERVICE" == "apt-mirror" ]]; then
        dc_run "$@"
    else
        dc exec -T "$SERVICE" "$@"
    fi
}

# Run step with timing and error handling
run_step() {
    local step_num="$1"
    local step_name="$2"
    shift 2

    log_step "STEP $step_num: $step_name"
    timer_start "$step_name"

    if [[ $DRY_RUN -eq 1 ]]; then
        log "[DRY-RUN] Would execute: $*"
        timer_end "$step_name"
        return 0
    fi

    if "$@"; then
        log_ok "$step_name completed successfully"
        timer_end "$step_name"
        return 0
    else
        local rc=$?
        log_err "$step_name failed with exit code $rc"
        timer_end "$step_name"
        return $rc
    fi
}

# ==================== PIPELINE STEPS ====================

step_heal_perms() {
    # Fix permissions at the start to ensure we can write to mirror files
    if [[ ! -f "$HEAL_PERMS_SCRIPT" ]]; then
        log_warn "heal-perms.sh not found: $HEAL_PERMS_SCRIPT"
        return 0
    fi

    log "Running permission healer (heal-perms.sh)..."
    if bash "$HEAL_PERMS_SCRIPT" 2>&1 | tee -a "$LOGFILE"; then
        log_ok "Permissions healed"
        return 0
    else
        log_warn "heal-perms.sh had issues (continuing anyway)"
        return 0
    fi
}

step_container_setup() {
    log "Checking Docker status..."

    # For apt-mirror, we use docker compose run (profiled service)
    # Just verify docker is working and image is built
    log "Ensuring apt-mirror image is built..."
    dc --profile mirror build --quiet "$SERVICE" 2>/dev/null || true

    # Create required directories on host
    log "Creating log directory..."
    mkdir -p "${REPO_ROOT}/log"
    mkdir -p "${REPO_ROOT}/repo/mirror"

    # Remove stale lock file on host
    rm -f "${REPO_ROOT}/data/var/apt-mirror.lock" 2>/dev/null || true

    log_ok "Container ready"
    return 0
}

step_download() {
    if [[ $SKIP_DOWNLOAD -eq 1 ]]; then
        log "Skipping download (--skip-download)"
        return 0
    fi

    log "Starting apt-mirror download..."
    log "This may take a while depending on repository updates..."

    # Run apt-mirror using docker compose run (for profiled service)
    if dc --profile mirror run --rm "$SERVICE" /usr/bin/apt-mirror /etc/apt/mirror.list 2>&1 | tee -a "$LOGFILE"; then
        log_ok "Download completed"
        return 0
    else
        log_warn "apt-mirror had warnings (continuing anyway)"
        return 0
    fi
}

step_postmirror() {
    if [[ $SKIP_DEP11 -eq 1 ]]; then
        log "Skipping postmirror/DEP-11 (--skip-dep11)"
        return 0
    fi

    log "Running postmirror cleanup and validation..."

    dc --profile mirror run --rm "$SERVICE" bash -lc \
        "export REPO_PATH='/var/spool/apt-mirror'; /var/spool/apt-mirror/var/postmirror.sh" 2>&1 | tee -a "$LOGFILE" || {
        log_warn "Postmirror had some issues (non-fatal)"
    }

    return 0
}

step_permissions() {
    log "Fixing file permissions for host access..."

    # Get the actual user (even if running with sudo)
    local current_user
    current_user="${SUDO_USER:-$(id -un)}"
    local current_group
    current_group="$(id -gn "$current_user" 2>/dev/null || id -gn)"

    # Docker creates files as root - we need to change ownership back to current user
    if [[ -d "${REPO_ROOT}/repo/mirror" ]]; then
        log "Changing ownership of mirror files to ${current_user}:${current_group}..."

        # Only chown what is actually mis-owned. The mirror is ~180k inodes and
        # almost nothing changes between runs, so an unconditional `chown -R`
        # rewrites every inode for no reason.
        if sudo find "${REPO_ROOT}/repo/mirror" \
             \( ! -user "$current_user" -o ! -group "$current_group" \) \
             -exec chown "${current_user}:${current_group}" {} + 2>/dev/null; then
            log_ok "Ownership changed to ${current_user}:${current_group}"
        else
            log_warn "Could not change ownership (may need manual: sudo chown -R ${current_user}:${current_group} ${REPO_ROOT}/repo/mirror)"
        fi

        # Fix permissions: directories 755, files 644.
        # Filter on the current mode and batch with `-exec ... +`; the old
        # `-exec ... \;` forked one chmod per path and cost ~3.5 min per run.
        log "Setting directory permissions to 755..."
        find "${REPO_ROOT}/repo/mirror" -type d ! -perm 755 -exec chmod 755 {} + 2>/dev/null || true

        log "Setting file permissions to 644..."
        find "${REPO_ROOT}/repo/mirror" -type f ! -perm 644 -exec chmod 644 {} + 2>/dev/null || true
    fi

    # Also fix GNUPG home ownership and permissions
    if [[ -d "$GNUPG_HOME" ]]; then
        log "Fixing GPG home ownership and permissions..."
        sudo chown -R "${current_user}:${current_group}" "$GNUPG_HOME" 2>/dev/null || true
        chmod 700 "$GNUPG_HOME" 2>/dev/null || true
        find "$GNUPG_HOME" -type f ! -perm 600 -exec chmod 600 {} + 2>/dev/null || true
    fi

    log_ok "Permissions updated"
    return 0
}

step_compression_fix() {
    if [[ ! -f "$COMPRESS_FIX_SCRIPT" ]]; then
        log_warn "Compression fix script not found: $COMPRESS_FIX_SCRIPT"
        return 0
    fi

    log "Regenerating compressed Packages files (.gz/.xz)..."

    if bash "$COMPRESS_FIX_SCRIPT" 2>&1 | tee -a "$LOGFILE"; then
        log_ok "Compression fix completed"
        return 0
    else
        log_warn "Compression fix had errors (continuing)"
        return 0
    fi
}

ensure_local_repo_tree() {
    local local_repo="${MIRROR_ROOT}/repo.ailinux.me"
    local staging_pool="${REPO_ROOT}/pool"
    local suite="${AILINUX_LOCAL_SUITE:-}"

    if [[ -z "$suite" && -r /etc/os-release ]]; then
        suite="$(. /etc/os-release; printf '%s' "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")"
    fi
    suite="${suite:-resolute}"

    log "Ensuring local AILinux repository tree for suite: $suite"
    mkdir -p \
        "$local_repo/pool" \
        "$local_repo/dists/$suite/main/binary-amd64" \
        "$local_repo/dists/$suite/main/binary-i386"

    if [[ ! -d "$staging_pool" ]]; then
        log_err "Local package staging pool missing: $staging_pool"
        return 1
    fi

    # The project-level pool is the recoverable source of truth. Do not use
    # --delete here: packages published directly into the live pool are kept.
    cp -a "$staging_pool/." "$local_repo/pool/"

    local package_count
    package_count=$(find "$local_repo/pool" -type f -name '*.deb' 2>/dev/null | wc -l | tr -d ' ')
    log_ok "Local AILinux repository tree ready (${package_count} staged package file(s))"
    return 0
}

step_generate_packages() {
    log "Generating Packages and Release metadata for local APT repos..."

    ensure_local_repo_tree || return 1

    local kernel_meta_script="${REPO_ROOT}/update-kernel-meta.sh"
    if [[ -x "$kernel_meta_script" ]]; then
        log "Updating AILinux kernel meta package before repository scan..."
        if REPO_ROOT="$REPO_ROOT" bash "$kernel_meta_script" 2>&1 | tee -a "$LOGFILE"; then
            log_ok "AILinux kernel meta package updated"
        else
            log_err "AILinux kernel meta package update failed"
            return 1
        fi
    else
        log_warn "Kernel meta generator not found or not executable: $kernel_meta_script"
    fi

    local repo_name repo_path_host repo_path_container cmd
    local updated=0
    local local_repos=()

    while IFS= read -r repo_name; do
        [[ -n "$repo_name" ]] || continue
        local_repos+=("$repo_name")
    done < <(find "$MIRROR_ROOT" -mindepth 1 -maxdepth 1 -type d -name "*.ailinux.me" -printf '%f\n' | sort)

    if [[ ${#local_repos[@]} -eq 0 ]]; then
        log_warn "No local *.ailinux.me repositories found in $MIRROR_ROOT"
        return 0
    fi

    for repo_name in "${local_repos[@]}"; do
      repo_path_host="${MIRROR_ROOT}/${repo_name}"
      repo_path_container="/var/spool/apt-mirror/mirror/${repo_name}"

      if [[ ! -d "$repo_path_host/pool" ]] || [[ ! -d "$repo_path_host/dists" ]]; then
        log "Skipping ${repo_name}: missing pool/ or dists/."
        continue
      fi

      log "Running dpkg-scanpackages + apt-ftparchive in container for ${repo_name}..."
      cmd="
        set -euo pipefail
        cd ${repo_path_container}
        shopt -s nullglob

        if ! command -v dpkg-scanpackages >/dev/null 2>&1; then
          echo 'dpkg-scanpackages not found in container' >&2
          exit 1
        fi
        if ! command -v apt-ftparchive >/dev/null 2>&1; then
          echo 'apt-ftparchive not found in container' >&2
          exit 1
        fi

        # Per-run cache for dpkg-scanpackages output, keyed by (pool_dir, arch).
        scan_cache=\$(mktemp -d)
        trap 'rm -rf \"\$scan_cache\"' EXIT

        write_binary_release() {
          local out_file=\"\$1\"
          local suite=\"\$2\"
          local component=\"\$3\"
          local arch=\"\$4\"
          cat > \"\$out_file\" <<EOF
Archive: \$suite
Component: \$component
Origin: AILinux
Label: AILinux
Architecture: \$arch
EOF
        }

        mapfile -t suites < <(find dists -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
        if [[ \${#suites[@]} -eq 0 ]]; then
          echo 'No distributions found under dists/' >&2
          exit 0
        fi

        for suite in \"\${suites[@]}\"; do
          suite_dir=\"dists/\$suite\"
          release_file=\"\$suite_dir/Release\"
          components=()
          architectures=()

          if [[ -f \"\$release_file\" ]]; then
            components_line=\$(awk -F': ' '\$1==\"Components\" { print \$2; exit }' \"\$release_file\")
            arch_line=\$(awk -F': ' '\$1==\"Architectures\" { print \$2; exit }' \"\$release_file\")
            [[ -n \"\$components_line\" ]] && read -r -a components <<< \"\$components_line\"
            [[ -n \"\$arch_line\" ]] && read -r -a architectures <<< \"\$arch_line\"
          fi

          if [[ \${#components[@]} -eq 0 ]]; then
            while IFS= read -r component; do
              [[ -n \"\$component\" ]] && components+=(\"\$component\")
            done < <(find \"\$suite_dir\" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
          fi
          if [[ \${#components[@]} -eq 0 ]]; then
            components=(main)
          fi

          if [[ \${#architectures[@]} -eq 0 ]]; then
            while IFS= read -r arch_dir; do
              [[ -n \"\$arch_dir\" ]] && architectures+=(\"\${arch_dir#binary-}\")
            done < <(find \"\$suite_dir\" -mindepth 2 -maxdepth 2 -type d -name 'binary-*' -printf '%f\n' | sort -u)
          fi
          if [[ \${#architectures[@]} -eq 0 ]]; then
            architectures=(amd64 i386)
          fi

          echo \"Generating metadata for suite \$suite (components: \${components[*]}, archs: \${architectures[*]})\"

          for component in \"\${components[@]}\"; do
            component_dir=\"\$suite_dir/\$component\"
            mkdir -p \"\$component_dir\"

            pool_dir=\"pool/\$component\"
            if [[ ! -d \"\$pool_dir\" ]]; then
              pool_dir=\"pool\"
            fi

            for arch in \"\${architectures[@]}\"; do
              case \"\$arch\" in
                source|all) continue ;;
              esac

              out_dir=\"\$component_dir/binary-\$arch\"
              mkdir -p \"\$out_dir\"

              # dpkg-scanpackages decompresses every .deb in the pool, so it is
              # by far the most expensive part of this step. Suites that share a
              # pool and an arch (e.g. noble+resolute on amd64) would otherwise
              # scan the same GBs repeatedly, so memoise the result per
              # (pool_dir, arch). The output is byte-identical either way.
              cache_key=\$(printf '%s|%s' \"\$pool_dir\" \"\$arch\" | md5sum | cut -d' ' -f1)
              cache_file=\"\$scan_cache/\$cache_key\"

              if [[ ! -f \"\$cache_file\" ]]; then
                if ! dpkg-scanpackages --multiversion -a \"\$arch\" \"\$pool_dir\" /dev/null > \"\$cache_file\" 2>/dev/null; then
                  : > \"\$cache_file\"
                fi
              fi
              cp \"\$cache_file\" \"\$out_dir/Packages\"

              gzip -9c \"\$out_dir/Packages\" > \"\$out_dir/Packages.gz\"
              xz -c \"\$out_dir/Packages\" > \"\$out_dir/Packages.xz\"
              write_binary_release \"\$out_dir/Release\" \"\$suite\" \"\$component\" \"\$arch\"
            done

            source_dir=\"\$component_dir/source\"
            mkdir -p \"\$source_dir\"
            : > \"\$source_dir/Packages\"
            gzip -9c \"\$source_dir/Packages\" > \"\$source_dir/Packages.gz\"
          done

          components_joined=\$(printf '%s ' \"\${components[@]}\" | sed 's/ \$//')
          architectures_joined=\$(printf '%s ' \"\${architectures[@]}\" | sed 's/ \$//')
          tmpconf=\$(mktemp)
          cat > \"\$tmpconf\" <<EOF
APT::FTPArchive::Release {
  Origin \"AILinux\";
  Label \"AILinux\";
  Suite \"\$suite\";
  Codename \"\$suite\";
  Components \"\$components_joined\";
  Architectures \"\$architectures_joined\";
};
EOF
          apt-ftparchive -c \"\$tmpconf\" release \"\$suite_dir\" > \"\$release_file\"
          rm -f \"\$tmpconf\"
        done
      "

      if dc_run bash -c "$cmd" 2>&1 | tee -a "$LOGFILE"; then
        log_ok "Generated Packages/Release metadata for ${repo_name}"
        updated=1
      else
        log_err "Failed to generate metadata for ${repo_name}"
        return 1
      fi
    done

    if [[ $updated -eq 0 ]]; then
      log_warn "No matching repositories were updated."
    fi
    return 0
}


step_verify_local_repo() {
    local local_repo="${MIRROR_ROOT}/repo.ailinux.me"
    local suite="${AILINUX_LOCAL_SUITE:-}"
    local public_base="${PUBLIC_MIRROR_BASE:-https://repo.ailinux.me/mirror}"
    local package_file arch index_file rel missing=0 checked=0
    local remote_tmp local_xz remote_url

    if [[ -z "$suite" && -r /etc/os-release ]]; then
        suite="$(. /etc/os-release; printf '%s' "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")"
    fi
    suite="${suite:-resolute}"

    log "Verifying that every local .deb is present in generated APT metadata..."

    while IFS= read -r package_file; do
        [[ -n "$package_file" ]] || continue
        arch="$(dpkg-deb -f "$package_file" Architecture 2>/dev/null || true)"
        rel="${package_file#${local_repo}/}"

        case "$arch" in
            all) index_file="$local_repo/dists/$suite/main/binary-amd64/Packages" ;;
            amd64|i386|arm64|armhf) index_file="$local_repo/dists/$suite/main/binary-$arch/Packages" ;;
            *)
                log_err "Unsupported or unreadable package architecture '$arch': $rel"
                missing=1
                continue
                ;;
        esac

        if [[ ! -s "$index_file" ]]; then
            log_err "Missing package index for $arch: $index_file"
            missing=1
            continue
        fi

        if ! grep -Fqx "Filename: $rel" "$index_file"; then
            log_err "Package is in pool but missing from Packages index: $rel"
            missing=1
        fi
        checked=$((checked + 1))
    done < <(find "$local_repo/pool" -type f -name '*.deb' -print | sort)

    if [[ $checked -eq 0 ]]; then
        log_err "No .deb packages found under $local_repo/pool"
        return 1
    fi
    if [[ $missing -ne 0 ]]; then
        return 1
    fi

    for package_file in \
        "$local_repo/dists/$suite/Release" \
        "$local_repo/dists/$suite/InRelease" \
        "$local_repo/dists/$suite/Release.gpg"; do
        if [[ ! -s "$package_file" ]]; then
            log_err "Required signed metadata missing or empty: $package_file"
            return 1
        fi
    done

    log_ok "Local metadata contains all $checked package file(s)"

    if command -v curl >/dev/null 2>&1; then
        local_xz="$local_repo/dists/$suite/main/binary-amd64/Packages.xz"
        remote_url="$public_base/repo.ailinux.me/dists/$suite/main/binary-amd64/Packages.xz?verify=$(date +%s)"
        remote_tmp="$(mktemp)"
        if ! curl -fsSL --connect-timeout 10 --max-time 30 "$remote_url" -o "$remote_tmp"; then
            rm -f "$remote_tmp"
            log_err "Public repository verification failed: could not fetch $remote_url"
            return 1
        fi
        if ! cmp -s "$local_xz" "$remote_tmp"; then
            rm -f "$remote_tmp"
            log_err "Public Packages.xz differs from freshly generated local metadata"
            return 1
        fi
        rm -f "$remote_tmp"
        log_ok "Public repo.ailinux.me serves the freshly generated Packages.xz"
    else
        log_warn "curl not installed; skipped public repository byte-for-byte verification"
    fi

    return 0
}


step_signing() {
    log "Signing repositories with GPG..."
    log "Using AILinux GPG Key: $SIGNING_KEY_ID"

    if [[ ! -d "$MIRROR_ROOT" ]]; then
        log_err "Mirror root not found: $MIRROR_ROOT"
        return 1
    fi

    # Show detected repositories
    detect_mirror_repos "$MIRROR_ROOT"

    # Try host signing first if GNUPGHOME with key exists
    if [[ -d "$GNUPG_HOME" ]] && [[ -f "$SIGN_REPOS_SCRIPT" ]]; then
        log "Attempting host-based signing with GNUPGHOME=$GNUPG_HOME"

        if GNUPGHOME="$GNUPG_HOME" bash "$SIGN_REPOS_SCRIPT" "$MIRROR_ROOT" 2>&1 | tee -a "$LOGFILE"; then
            log_ok "Host signing completed"
            return 0
        else
            log_warn "Host signing failed, falling back to container..."
        fi
    fi

    # Fallback: Run signing in container (has access to GPG key)
    log "Running signing in Docker container..."
    dc --profile mirror run --rm "$SERVICE" bash -lc \
        "export REPO_PATH='/var/spool/apt-mirror'; bash /var/spool/apt-mirror/var/sign-repos.sh /var/spool/apt-mirror/mirror" 2>&1 | tee -a "$LOGFILE"

    local rc=$?
    if [[ $rc -eq 0 ]]; then
        log_ok "Container signing completed"
    else
        log_err "Signing failed"
    fi
    return $rc
}

step_export_key() {
    if [[ ! -f "$PUBLIC_KEY_SCRIPT" ]]; then
        log_warn "Public key export script not found: $PUBLIC_KEY_SCRIPT"
        return 0
    fi

    log "Exporting public GPG key..."
    log "Using GNUPGHOME: $GNUPG_HOME"

    # Export with correct GNUPGHOME
    if GNUPGHOME="$GNUPG_HOME" bash "$PUBLIC_KEY_SCRIPT" 2>&1 | tee -a "$LOGFILE"; then
        log_ok "Public key exported to $MIRROR_ROOT/ailinux-archive-key.gpg"
        return 0
    else
        log_warn "Key export had issues"
        return 0
    fi
}

step_generate_index() {
    if [[ -x "$MIRROR_MANIFEST_SCRIPT" ]]; then
        log "Generating mirror manifest from mirror.list and actual local metadata..."
        if python3 "$MIRROR_MANIFEST_SCRIPT" \
            --mirror-list "${REPO_ROOT}/mirror.list" \
            --mirror-root "$MIRROR_ROOT" \
            --output "$MIRROR_ROOT/mirror-repos.tsv" \
            --json-output "$MIRROR_ROOT/mirror-repos.json" 2>&1 | tee -a "$LOGFILE"; then
            log_ok "Mirror manifest generated"
        else
            log_err "Mirror manifest generation failed"
            return 1
        fi
    else
        log_err "Mirror manifest generator missing: $MIRROR_MANIFEST_SCRIPT"
        return 1
    fi

    if [[ -f "$INSTALLER_SOURCE" ]]; then
        mkdir -p "$MIRROR_ROOT"
        cp "$INSTALLER_SOURCE" "${MIRROR_ROOT}/add-ailinux-repo.sh"
        chmod 755 "${MIRROR_ROOT}/add-ailinux-repo.sh"
        log_ok "Published installer script to ${MIRROR_ROOT}/add-ailinux-repo.sh"
    else
        log_warn "Installer source not found: $INSTALLER_SOURCE"
    fi

    log "Generating HTML index..."

    # Run generate-index.sh on host since it accesses the mirror directory
    if [[ -f "${REPO_ROOT}/generate-index.sh" ]]; then
        if bash "${REPO_ROOT}/generate-index.sh" 2>&1 | tee -a "$LOGFILE"; then
            log_ok "HTML index generated"
            return 0
        else
            log_warn "Index generation had issues"
            return 0
        fi
    else
        log_warn "generate-index.sh not found or not executable"
        return 0
    fi
}

# ==================== MAIN ====================

main() {
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-download) SKIP_DOWNLOAD=1; shift ;;
            --skip-dep11)    SKIP_DEP11=1; shift ;;
            --sign-only)     SIGN_ONLY=1; SKIP_DOWNLOAD=1; SKIP_DEP11=1; shift ;;
            --dry-run)       DRY_RUN=1; shift ;;
            -h|--help)       usage; exit 0 ;;
            *) log_err "Unknown argument: $1"; usage; exit 1 ;;
        esac
    done

    # Setup logging
    mkdir -p "$LOG_DIR"

    # Rotate log if too large (>10MB)
    if [[ -f "$LOGFILE" ]] && [[ $(stat -c%s "$LOGFILE" 2>/dev/null || echo 0) -gt 10485760 ]]; then
        mv "$LOGFILE" "${LOGFILE}.old"
    fi

    # Check lock
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then
        log_err "Another update is already running (lock: $LOCKFILE)"
        exit 1
    fi
    trap 'rm -f "$LOCKFILE"' EXIT

    # Check requirements
    check_cmd docker || exit 1

    # Start
    TOTAL_START=$(date +%s)

    log_header "AILinux Mirror Update Pipeline v3.0"
    log "Started at: $(ts)"
    log "Log file: $LOGFILE"
    log "Repository root: $REPO_ROOT"
    log "Mirror root: $MIRROR_ROOT"
    log "GPG Home: $GNUPG_HOME"
    log "Signing Key: $SIGNING_KEY_ID"
    [[ $DRY_RUN -eq 1 ]] && log_warn "DRY-RUN MODE - No changes will be made"
    [[ $SIGN_ONLY -eq 1 ]] && log "Mode: Sign-only"
    [[ $SKIP_DOWNLOAD -eq 1 ]] && log "Skipping: Download"
    [[ $SKIP_DEP11 -eq 1 ]] && log "Skipping: DEP-11 validation"

    # Auto-detect actual repository roots, not just top-level host folders.
    log ""
    log "Auto-detected APT repository roots in $MIRROR_ROOT:"
    detect_mirror_repos "$MIRROR_ROOT" || true
    echo ""

    # Pipeline execution
    local failed=0

    # Step 0: Heal permissions first (fix any root-owned files from previous Docker runs)
    run_step 0 "Heal Permissions" step_heal_perms || failed=1

    if [[ $failed -eq 0 ]]; then
        run_step 1 "Container Setup" step_container_setup || failed=1
    fi

    if [[ $failed -eq 0 ]]; then
        run_step 2 "Download (apt-mirror)" step_download || failed=1
    fi

    if [[ $failed -eq 0 ]]; then
        run_step 3 "Postmirror (DEP-11)" step_postmirror || failed=1
    fi

    if [[ $failed -eq 0 ]]; then
        run_step 4 "Fix Permissions" step_permissions || failed=1
    fi

    if [[ $failed -eq 0 && $SIGN_ONLY -eq 0 ]]; then
        run_step 5 "Compression Fix" step_compression_fix || failed=1
    fi

    if [[ $failed -eq 0 && $SIGN_ONLY -eq 0 ]]; then
        run_step 6 "Generate Packages" step_generate_packages || failed=1
    fi

    if [[ $failed -eq 0 ]]; then
        run_step 7 "GPG Signing" step_signing || failed=1
    fi

    if [[ $failed -eq 0 ]]; then
        run_step 8 "Verify Local/Public Repository" step_verify_local_repo || failed=1
    fi

    if [[ $failed -eq 0 ]]; then
        run_step 9 "Export Public Key" step_export_key || failed=1
    fi

    if [[ $failed -eq 0 ]]; then
        run_step 10 "Generate Index" step_generate_index || failed=1
    fi

    # Summary
    echo "" | tee -a "$LOGFILE"
    local total_end
    total_end=$(date +%s)
    local total_duration=$((total_end - TOTAL_START))
    local total_mins=$((total_duration / 60))
    local total_secs=$((total_duration % 60))

    log_header "Pipeline Complete"

    if [[ $failed -eq 0 ]]; then
        log_ok "All steps completed successfully!"
        log "Total time: ${total_mins}m ${total_secs}s"
        echo "" | tee -a "$LOGFILE"
        echo "Mirror is ready at: ${REPO_ROOT}/repo/mirror" | tee -a "$LOGFILE"
        echo "Public URL: https://repo.ailinux.me/mirror/" | tee -a "$LOGFILE"
        exit 0
    else
        log_err "Pipeline completed with errors"
        log "Total time: ${total_mins}m ${total_secs}s"
        log "Check log file for details: $LOGFILE"
        exit 1
    fi
}
main "$@"
