#!/usr/bin/env bash

# Bash-Guard
if [ -z "${BASH_VERSION:-}" ]; then
  exec /usr/bin/env bash "$0" "$@"
fi

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
if [[ -z "${REPO_PATH:-}" ]]; then
  for candidate in "$SCRIPT_DIR" "$SCRIPT_DIR/.." "/var/spool/apt-mirror" "/root/ailinux-repo"; do
    [[ -d "$candidate" ]] || continue
    if [[ -d "${candidate}/repo" ]] || [[ -d "${candidate}/mirror" && -d "${candidate}/var" ]]; then
      REPO_PATH="$candidate"
      break
    fi
  done
fi
REPO_PATH="${REPO_PATH:-/root/ailinux-repo}"
REPO_PATH="$(cd "$REPO_PATH" 2>/dev/null && pwd -P || echo "$REPO_PATH")"
REPO_PATH="${REPO_PATH%/}"
LOCKFILE="/var/run/postmirror.lock"
LOGFILE="/var/log/ailinux/postmirror.log"
DEFAULT_MIRROR_LIST="${REPO_PATH}/mirror.list"
if [[ ! -f "$DEFAULT_MIRROR_LIST" ]]; then
  DEFAULT_MIRROR_LIST="${REPO_PATH}/repo/mirror/mirror.list"
fi
MIRROR_LIST_PATH="${MIRROR_LIST_PATH:-${DEFAULT_MIRROR_LIST}}"
REQUIRE_I386=1
DEFAULT_SIGNING_KEY_PATH=""
for key_candidate in \
  "${REPO_PATH}/repo/mirror/ailinux-archive-key.gpg" \
  "${REPO_PATH}/mirror/ailinux-archive-key.gpg"
do
  if [[ -f "$key_candidate" ]]; then
    DEFAULT_SIGNING_KEY_PATH="$key_candidate"
    break
  fi
done
if [[ -z "$DEFAULT_SIGNING_KEY_PATH" ]]; then
  if [[ -d "${REPO_PATH}/repo/mirror" ]]; then
    DEFAULT_SIGNING_KEY_PATH="${REPO_PATH}/repo/mirror/ailinux-archive-key.gpg"
  else
    DEFAULT_SIGNING_KEY_PATH="${REPO_PATH}/mirror/ailinux-archive-key.gpg"
  fi
fi
SIGNING_KEY_PATH="${SIGNING_KEY_PATH:-$DEFAULT_SIGNING_KEY_PATH}"

# Signing key: shared signing-key.env if available, else built-in default.
# Keep the fallback in sync with signing-key.env (see that file for details).
for _keyenv in "${REPO_PATH}/signing-key.env" \
               "$(dirname "${BASH_SOURCE[0]}")/signing-key.env" \
               "/var/spool/apt-mirror/var/signing-key.env"; do
  [[ -r "$_keyenv" ]] && { . "$_keyenv"; break; }
done
SIGNING_KEY_ID="${SIGNING_KEY_ID:-59FAE19560F5E25B}"

DEFAULT_GNUPGHOME="${REPO_PATH}/etc/gnupg"
if [[ -z "${GNUPGHOME:-}" ]]; then
  if [[ -d "$DEFAULT_GNUPGHOME" ]]; then export GNUPGHOME="$DEFAULT_GNUPGHOME"; else export GNUPGHOME="/root/.gnupg"; fi
fi
# Hygiene: sichere GnuPG-Rechte
mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME" || true

SIGN_REPOS_SCRIPT=""
SIGN_REPOS_CANDIDATES=(
  "${REPO_PATH}/scripts/sign-repos.sh"
  "${REPO_PATH}/sign-repos.sh"
  "/var/spool/apt-mirror/var/sign-repos.sh"
  "/var/spool/apt-mirror/var/scripts/sign-repos.sh"
)
for candidate in "${SIGN_REPOS_CANDIDATES[@]}"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    SIGN_REPOS_SCRIPT="$candidate"
    break
  fi
done

log(){ echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
[[ -n "$SIGN_REPOS_SCRIPT" ]] || { log "FEHLER: sign-repos.sh nicht gefunden/ausführbar."; exit 1; }
trap 'rm -f "$LOCKFILE"' EXIT

detect_mirror_root(){
  local c=()
  [ -n "${MIRROR_ROOT:-}" ] && c+=("$MIRROR_ROOT")
  c+=("${REPO_PATH}/repo/mirror" "${REPO_PATH}/mirror" "/var/spool/apt-mirror/mirror")
  for p in "${c[@]}"; do [[ -d "$p" ]] && { MIRROR_ROOT="$p"; log "Mirror-Root: $MIRROR_ROOT"; return 0; }; done
  return 1
}
has_dists(){ find "$1" -type d -name dists -print -quit >/dev/null; }

get_signing_keygrip(){ gpg --batch --with-colons --with-keygrip --list-keys "$SIGNING_KEY_ID" 2>/dev/null | awk -F: '$1=="grp"{print $10;exit}'; }

ensure_signing_key(){
  local kg; kg=$(get_signing_keygrip)
  if [[ -n "$kg" && -f "${GNUPGHOME}/private-keys-v1.d/${kg}.key" ]]; then
    log "Signier-Key OK ($SIGNING_KEY_ID, keygrip $kg)."
    return 0
  fi

  local imported=0
  local secret_candidates=(
    "${SIGNING_SECRET_PATH:-}"
    "${REPO_PATH}/etc/gnupg/secret.asc"
    "$(dirname "$SIGN_REPOS_SCRIPT")/../etc/gnupg/secret.asc"
    "/var/spool/apt-mirror/etc/gnupg/secret.asc"
  )
  for f in "${secret_candidates[@]}"; do
    [[ -n "${f:-}" && -f "$f" ]] || continue
    log "Importiere Secret-Key aus $f"
    if gpg --batch --yes --pinentry-mode loopback --import "$f" >/dev/null 2>&1; then
      imported=1
      break
    else
      log "WARN: Secret-Key Import aus $f fehlgeschlagen."
    fi
  done

  if [[ $imported -eq 0 ]]; then
    local public_candidates=(
      "$SIGNING_KEY_PATH"
      "$REPO_PATH/repo/mirror/ailinux-archive-key.gpg"
      "$REPO_PATH/mirror/ailinux-archive-key.gpg"
      "/var/spool/apt-mirror/mirror/ailinux-archive-key.gpg"
    )
    for f in "${public_candidates[@]}"; do
      [[ -f "$f" ]] || continue
      log "Importiere öffentlichen Schlüssel aus $f"
      if gpg --batch --yes --import "$f" >/dev/null 2>&1; then
        imported=1
        break
      else
        log "WARN: Öffentlicher Schlüssel konnte nicht importiert werden ($f)."
      fi
    done
  fi

  kg=$(get_signing_keygrip)
  if [[ -n "$kg" && -f "${GNUPGHOME}/private-keys-v1.d/${kg}.key" ]]; then
    log "Signier-Key bereit."
    return 0
  fi

  log "FEHLER: Secret-Key $SIGNING_KEY_ID fehlt im $GNUPGHOME."
  exit 1
}

export_public_key(){
  # Export public key for client distribution
  local output_file="$SIGNING_KEY_PATH"
  log "Exportiere öffentlichen Schlüssel nach $output_file"

  # Create temporary file
  local tmp_key; tmp_key=$(mktemp)
  trap "rm -f $tmp_key" RETURN

  # Export public key
  if gpg --export "$SIGNING_KEY_ID" > "$tmp_key"; then
    # Verify exported key
    if gpg --no-default-keyring --keyring "$tmp_key" --list-keys "$SIGNING_KEY_ID" >/dev/null 2>&1; then
      install -Dm0644 "$tmp_key" "$output_file"
      log "✓ Öffentlicher Schlüssel exportiert: $output_file"
    else
      log "WARN: Exportierter Schlüssel ist ungültig"
    fi
  else
    log "WARN: Fehler beim Exportieren des öffentlichen Schlüssels"
  fi
}

parse_sha256_entries(){
  awk '
    BEGIN{section=0} /^SHA256:/{section=1;next}
    section==1 && /^[[:space:]]/{gsub(/^[[:space:]]+/,"");split($0,p,/[[:space:]]+/); if(length(p[1])==64 && p[2]~/^[0-9]+$/ && p[3]!="") printf "%s %s %s\n",p[1],p[2],p[3]; next}
    section==1{exit}
  ' "$1"
}
download_file(){
  local url="$1" dest="$2" max_retries=2 retry=0
  while [[ $retry -lt $max_retries ]]; do
    if { timeout 30 curl -fsSL --connect-timeout 10 "$url" -o "$dest" || timeout 30 wget -qO "$dest" --timeout=10 "$url"; } 2>/dev/null; then
      return 0
    fi
    ((retry++))
    [[ $retry -lt $max_retries ]] && sleep 2
  done
  return 1
}

validate_dep11_blob(){
  local file="$1" min_size="${2:-100}"

  [[ -f "$file" ]] || return 1

  local size; size=$(stat -c '%s' "$file" 2>/dev/null || echo 0)
  [[ $size -ge $min_size ]] || return 1

  if [[ "$file" == *.gz ]]; then
    gunzip -t "$file" 2>/dev/null || return 1
  elif [[ "$file" == *.tar ]]; then
    tar -tf "$file" >/dev/null 2>&1 || return 1
  elif [[ "$file" == *.bz2 ]]; then
    bzip2 -t "$file" 2>/dev/null || return 1
  elif [[ "$file" == *.xz ]]; then
    if command -v xz >/dev/null 2>&1; then
      xz -t "$file" 2>/dev/null || return 1
    fi
  fi

  return 0
}

refetch_upstream_file(){
  local rel="$1" abs="$2" path="$3"
  local normalized_path="${path#./}"
  local url="https://${rel%/}/${normalized_path}"
  local tmp status=0

  tmp=$(mktemp) || return 1
  trap "rm -f '$tmp'" RETURN

  if download_file "$url" "$tmp"; then
    local fsize; fsize=$(stat -c '%s' "$tmp" 2>/dev/null || echo 0)
    if [[ $fsize -le 100 ]]; then
      log "   -> WARN: Heruntergeladene Datei zu klein ($fsize bytes): $url"
      status=1
    elif validate_dep11_blob "$tmp" 100; then
      install -D -m0644 "$tmp" "$abs/$normalized_path"
      log "   -> Nachgeladen: $url ($fsize bytes)"
      status=0
    else
      log "   -> WARN: Unbekanntes Format oder beschädigte Datei: $url"
      status=1
    fi
  else
    log "   -> WARN: Download fehlgeschlagen (Timeout/Netzwerk): $url"
    status=1
  fi

  trap - RETURN
  return "$status"
}

verify_dep11_payloads(){
  [[ -n "${MIRROR_ROOT:-}" ]] || { log "WARN: kein MIRROR_ROOT – Dep11-Prüfung übersprungen."; return 0; }

  local CLEANED=0 FAILED=0 SKIPPED=0
  log "Starte DEP-11 Validierung…"

  while IFS= read -r -d '' relf; do
    local dir_abs="$(dirname "$relf")"
    local rel="${relf#${MIRROR_ROOT}/}"
    local dir_rel="$(dirname "$rel")"

    # Zähler pro Release
    local rel_cleaned=0 rel_failed=0 rel_skipped=0

    while read -r hash size relp; do
      [[ "$relp" == *dep11/* ]] || continue

      local normalized_relp="${relp#./}"
      local tgt="$dir_abs/$normalized_relp"
      local action="skip"

      # Datei fehlend?
      if [[ ! -f "$tgt" ]]; then
        log "   DEP-11 fehlt: ${dir_rel}/${normalized_relp} – Versuche Nachladen…"
        if refetch_upstream_file "$dir_rel" "$dir_abs" "$normalized_relp"; then
          ((rel_cleaned++))
          action="refetch_ok"
        else
          log "   ⚠ Konnte DEP-11 nicht nachladen (wird ignoriert): ${dir_rel}/${normalized_relp}"
          ((rel_failed++))
          action="refetch_fail"
        fi
        continue
      fi

      # Dateigröße prüfen
      local asz; asz=$(stat -c '%s' "$tgt" 2>/dev/null || echo 0)
      if [[ "$asz" == "0" ]]; then
        log "   DEP-11 ist leer (0 bytes): ${dir_rel}/${normalized_relp} – Versuche Nachladen…"
        if refetch_upstream_file "$dir_rel" "$dir_abs" "$normalized_relp"; then
          ((rel_cleaned++))
          action="refetch_empty_ok"
        else
          log "   ⚠ Konnte leere DEP-11 nicht ersetzen (wird ignoriert): ${dir_rel}/${normalized_relp}"
          ((rel_failed++))
          action="refetch_empty_fail"
        fi
        continue
      fi

      # Hash-Validierung (NUR Warnung bei Fehler, nicht blockierend)
      local needs_refresh=0
      if [[ "$asz" != "$size" ]]; then
        log "   ⚠ DEP-11 Größe stimmt nicht überein: ${dir_rel}/${normalized_relp} (lokal: $asz, erwartet: $size)"
        needs_refresh=1
      elif ! echo "$hash  $tgt" | sha256sum --check --status >/dev/null 2>&1; then
        log "   ⚠ DEP-11 Hash stimmt nicht überein: ${dir_rel}/${normalized_relp}"
        needs_refresh=1
      fi

      if [[ $needs_refresh -eq 1 ]]; then
        log "   Versuche Nachladen fehlerhafter DEP-11: ${dir_rel}/${normalized_relp}"
        rm -f "$tgt"
        if refetch_upstream_file "$dir_rel" "$dir_abs" "$normalized_relp"; then
          ((rel_cleaned++))
          action="refresh_ok"
        else
          log "   ⚠ Konnte DEP-11 nicht aktualisieren – ENTFERNE korrupte Datei: ${dir_rel}/${normalized_relp}"
          # Wichtig: Auch entfernen wenn nicht reparierbar, damit Clients nicht fehlerhaften Hash sehen
          rm -f "$tgt"
          ((rel_failed++))
          action="refresh_fail_removed"
        fi
      fi

    done < <(parse_sha256_entries "$relf") || true

    # Statistik pro Release
    if [[ $rel_cleaned -gt 0 ]] || [[ $rel_failed -gt 0 ]]; then
      log "   ${dir_rel}: $rel_cleaned repariert, $rel_failed ignoriert"
      ((CLEANED += rel_cleaned))
      ((FAILED += rel_failed))
    else
      ((SKIPPED++))
    fi

  done < <(find "$MIRROR_ROOT" -type f -name Release -print0) || true

  # Abschlussbericht
  if [[ $CLEANED -gt 0 ]] || [[ $FAILED -gt 0 ]]; then
    log "✓ DEP-11 Validierung abgeschlossen: $CLEANED repariert, $FAILED entfernt (unreparierbar), $SKIPPED übersprungen"

    # Wenn Dateien entfernt wurden, muss das Repository neu signiert werden
    # damit die Release-Datei die fehlenden DEP-11 Dateien nicht mehr referenziert
    if [[ $FAILED -gt 0 ]]; then
      log "⚠ $FAILED unreparierbare DEP-11 Dateien wurden entfernt"
      log "Repository wird im nächsten Schritt neu signiert (sign-repos.sh)"
    fi
  else
    log "✓ Alle DEP-11 Dateien sind intakt."
  fi

  # Wichtig: NICHT abbrechen, auch wenn DEP-11 fehlgeschlagen
  return 0
}

ensure_neon_pins(){
  local bases=()
  [[ -n "${MIRROR_ROOT:-}" ]] && bases+=("$MIRROR_ROOT")
  bases+=("${REPO_PATH}/repo/mirror" "${REPO_PATH}/mirror" "/var/spool/apt-mirror/mirror")

  for base in "${bases[@]}"; do
    local dists_root="${base}/archive.neon.kde.org/user/dists"
    [[ -d "$dists_root" ]] || continue

    for suite_dir in "$dists_root"/*/; do
      [[ -d "$suite_dir" ]] || continue
      local pins_dir="${suite_dir%/}/main/neon"
      local pins_file="${pins_dir}/pins"
      local legacy_pins="${suite_dir%/}/neon-pins"

      if [[ -f "$legacy_pins" ]]; then
        rm -f "$legacy_pins"
        log "✓ KDE neon: altes neon-pins entfernt (${legacy_pins})"
      fi

      if [[ ! -f "$pins_file" ]]; then
        mkdir -p "$pins_dir"
        cat > "$pins_file" <<'EOF'
Package: *
Pin: release o=KDE neon
Pin-Priority: 1001
EOF
        chmod 0644 "$pins_file"
        log "✓ KDE neon: pins erstellt (${pins_file})"
      fi
    done
  done
}

cleanup_ailinux_stable_alias(){
  local bases=()
  [[ -n "${MIRROR_ROOT:-}" ]] && bases+=("$MIRROR_ROOT")
  bases+=("${REPO_PATH}/repo/mirror" "${REPO_PATH}/mirror")

  for base in "${bases[@]}"; do
    local stable_link="${base}/archive.ailinux.me/dists/stable"
    [[ -L "$stable_link" ]] || continue
    local target
    target=$(readlink "$stable_link" 2>/dev/null || echo "")
    if [[ "$target" == "noble" || "$target" == */noble ]]; then
      rm -f "$stable_link"
      log "✓ Entfernt: archive.ailinux.me stable -> noble Symlink (${stable_link})"
    fi
  done
}


# apt-mirror updates uncompressed Packages before it invokes postmirror.
# Never sign Release metadata while an old Packages.xz or Packages.gz remains.
# Repair only stale compressed indexes; keep already consistent files unchanged.
reconcile_compressed_indexes(){
  local plain ext compressed staged fixed=0 checked=0
  [[ -d "$MIRROR_ROOT" ]] || { log "ERROR: missing MIRROR_ROOT for compression validation"; return 1; }

  while IFS= read -r -d '' plain; do
    for ext in gz xz; do
      compressed="$plain.$ext"
      [[ -f "$compressed" ]] || continue
      ((checked += 1))

      if [[ "$ext" == gz ]]; then
        if (set -o pipefail; gzip -cd -- "$compressed" | cmp -s - "$plain"); then
          continue
        fi
      else
        if (set -o pipefail; xz -dc -- "$compressed" | cmp -s - "$plain"); then
          continue
        fi
      fi

      staged="$(mktemp "$compressed.repair.XXXXXX")" || return 1
      if [[ "$ext" == gz ]]; then
        if ! gzip -n -9 -c -- "$plain" > "$staged"; then
          rm -f "$staged"
          log "ERROR: failed to regenerate $compressed"
          return 1
        fi
        if ! (set -o pipefail; gzip -cd -- "$staged" | cmp -s - "$plain"); then
          rm -f "$staged"
          log "ERROR: verification failed for $compressed"
          return 1
        fi
      else
        if ! xz -9 -c -- "$plain" > "$staged"; then
          rm -f "$staged"
          log "ERROR: failed to regenerate $compressed"
          return 1
        fi
        if ! (set -o pipefail; xz -dc -- "$staged" | cmp -s - "$plain"); then
          rm -f "$staged"
          log "ERROR: verification failed for $compressed"
          return 1
        fi
      fi

      chmod 0644 "$staged"
      mv -f -- "$staged" "$compressed"
      log "Rebuilt stale index: $compressed"
      ((fixed += 1))
    done
  done < <(find "$MIRROR_ROOT" -type f \( -name Packages -o -name Sources \) -print0)

  log "Compression validation complete: $checked checked, $fixed rebuilt."
  return 0
}

main(){
  if [[ -e "$LOCKFILE" ]]; then log "FEHLER: Lock existiert: $LOCKFILE"; exit 1; fi
  mkdir -p "$(dirname "$LOGFILE")"
  touch "$LOCKFILE"

  ensure_signing_key
  export_public_key
  detect_mirror_root || log "WARN: Mirror-Root nicht festgestellt."
  ensure_neon_pins
  cleanup_ailinux_stable_alias

  # Mandatory before signing: never advertise new plain Packages alongside
  # old .xz/.gz files, even when apt-mirror is run directly outside update-mirror.
  reconcile_compressed_indexes

  # Sign only after compressed indexes match, before slow DEP-11 validation.
  # This ensures mirrors get signed even if DEP-11 validation is slow/stuck
  [[ -x "$SIGN_REPOS_SCRIPT" ]] || { [[ -x "$ALT_SIGN_REPOS_SCRIPT" ]] && SIGN_REPOS_SCRIPT="$ALT_SIGN_REPOS_SCRIPT"; }
  [[ -x "$SIGN_REPOS_SCRIPT" ]] || { log "FEHLER: sign-repos.sh nicht gefunden/ausführbar."; exit 1; }

  declare -a roots
  if [[ -n "${MIRROR_ROOT:-}" ]]; then
    while IFS= read -r -d '' d; do roots+=("$(dirname "$d")"); done < <(find "$MIRROR_ROOT" -type d -name dists -print0)
  fi
  for extra in "${REPO_PATH}/repo/mirror" "/var/spool/apt-mirror/mirror"; do
    [[ -d "$extra" ]] && while IFS= read -r -d '' d; do roots+=("$(dirname "$d")"); done < <(find "$extra" -type d -name dists -print0)
  done
  mapfile -t roots < <(printf "%s\n" "${roots[@]-}" | sort -u)

  if [[ ${#roots[@]} -eq 0 ]]; then
    log "WARN: Keine dists/* gefunden – nichts zu signieren."
  else
    log "Signiere folgende Repositories:"
   printf '  - %s\n' "${roots[@]}"

    # Check for i386 architecture availability only when required
    if [[ "${REQUIRE_I386:-0}" -eq 1 ]]; then
      if dpkg --print-foreign-architectures 2>/dev/null | grep -q "^i386$"; then
        log "✓ i386 Architektur ist aktiviert (für 32-bit Pakete)"
      else
        log "⚠️  WARNUNG: i386 Architektur ist NICHT aktiviert!"
        log "   → apt-mirror kann keine i386 Pakete herunterladen"
        log "   → Lösung: Container mit Multiarch-Unterstützung aktualisieren (dpkg --add-architecture i386)"
      fi
    else
      log "Mirrorlist verlangt keine i386 Architektur – überspringe Multiarch-Verifikation."
    fi

    for r in "${roots[@]}"; do
      "$SIGN_REPOS_SCRIPT" "$r"
    done

    log "Signierung erfolgreich."
  fi

  # DEP-11 validation runs AFTER signing (non-critical, can be slow)
  verify_dep11_payloads

  log "Mirror-Lauf erfolgreich beendet."
}

main | tee -a "$LOGFILE"
