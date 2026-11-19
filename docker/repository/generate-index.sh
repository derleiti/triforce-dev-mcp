#!/usr/bin/env bash
# ============================================================================
# AILinux Mirror Index Generator v3.0
# ============================================================================
# Generates a dynamic, searchable index.html for the mirror directory
#
# Environment Variables:
#   HOST_REPO_PATH  - Path to repo directory (default: ./repo)
#   BASE_URL        - Public base URL (default: https://repo.ailinux.me)
#   PUBLIC_PATH     - Public path segment (default: mirror)
# ============================================================================

if [ -z "${BASH_VERSION:-}" ]; then
  exec /usr/bin/env bash "$0" "$@"
fi

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
HOST_REPO_PATH="${HOST_REPO_PATH:-$SCRIPT_DIR/repo}"
HOST_MIRROR_PATH="${HOST_MIRROR_PATH:-$HOST_REPO_PATH/mirror}"
INDEX_FILE_ON_MIRROR="$HOST_MIRROR_PATH/index.html"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/log}"
UPDATE_LOGFILE="${UPDATE_LOGFILE:-$LOG_DIR/update-mirror.log}"
POSTMIRROR_LOGFILE="${POSTMIRROR_LOGFILE:-$LOG_DIR/postmirror.log}"
WRITE_ROOT_INDEX="${WRITE_ROOT_INDEX:-0}"

BASE_URL="${BASE_URL:-https://repo.ailinux.me}"
BASE_URL="${BASE_URL%/}"
PUBLIC_PATH="${PUBLIC_PATH:-mirror}"
PUBLIC_PATH="${PUBLIC_PATH#/}"
PUBLIC_PATH="${PUBLIC_PATH%/}"

if [[ -n "$PUBLIC_PATH" ]]; then
  PUBLIC_BASE="$BASE_URL/$PUBLIC_PATH"
else
  PUBLIC_BASE="$BASE_URL"
fi
PUBLIC_BASE="${PUBLIC_BASE%/}"

mkdir -p "$HOST_MIRROR_PATH"

# Gather repository information
declare -A REPO_CATEGORIES
declare -A REPO_DESCRIPTIONS

# Define categories and descriptions
REPO_CATEGORIES=(
  ["archive.ubuntu.com"]="ubuntu"
  ["security.ubuntu.com"]="ubuntu"
  ["archive.neon.kde.org"]="desktop"
  ["ppa.launchpadcontent.net"]="ppa"
  ["dl.google.com"]="gaming"
  ["dl.winehq.org"]="gaming"
  ["repo.steampowered.com"]="gaming"
  ["download.docker.com"]="dev"
  ["deb.nodesource.com"]="dev"
  ["packages.microsoft.com"]="dev"
  ["cli.github.com"]="dev"
  ["download.sublimetext.com"]="dev"
  ["pkgs.k8s.io"]="dev"
  ["apt.releases.hashicorp.com"]="dev"
  ["developer.download.nvidia.com"]="drivers"
  ["nvidia.github.io"]="ai"
  ["apt.repos.intel.com"]="ai"
  ["updates.signal.org"]="desktop"
)

REPO_DESCRIPTIONS=(
  ["archive.ubuntu.com"]="Ubuntu Main Repository"
  ["security.ubuntu.com"]="Ubuntu Security Updates"
  ["archive.neon.kde.org"]="KDE Neon"
  ["dl.google.com"]="Google Chrome"
  ["dl.winehq.org"]="WineHQ"
  ["repo.steampowered.com"]="Steam"
  ["download.docker.com"]="Docker CE"
  ["deb.nodesource.com"]="Node.js"
  ["packages.microsoft.com"]="Microsoft VS Code"
  ["cli.github.com"]="GitHub CLI"
  ["download.sublimetext.com"]="Sublime Text"
  ["pkgs.k8s.io"]="Kubernetes"
  ["apt.releases.hashicorp.com"]="HashiCorp Tools"
  ["developer.download.nvidia.com"]="NVIDIA CUDA"
  ["nvidia.github.io"]="NVIDIA Container Toolkit"
  ["apt.repos.intel.com"]="Intel oneAPI"
  ["updates.signal.org"]="Signal Desktop"
  ["ppa.launchpadcontent.net"]="Ubuntu PPAs"
)

# Count directories and calculate size
REPO_COUNT=0
TOTAL_SIZE="0"
declare -a UBUNTU_REPOS=()
declare -a GAMING_REPOS=()
declare -a DEV_REPOS=()
declare -a DRIVERS_REPOS=()
declare -a AI_REPOS=()
declare -a DESKTOP_REPOS=()
declare -a PPA_REPOS=()
declare -a OTHER_REPOS=()

while IFS= read -r -d '' dir; do
  name=$(basename "$dir")
  [[ "$name" == "index.html" ]] && continue
  [[ -d "$dir" ]] || continue

  ((REPO_COUNT++)) || true

  # Categorize
  category="other"
  for pattern in "${!REPO_CATEGORIES[@]}"; do
    if [[ "$name" == *"$pattern"* ]]; then
      category="${REPO_CATEGORIES[$pattern]}"
      break
    fi
  done

  case "$category" in
    ubuntu)  UBUNTU_REPOS+=("$name") ;;
    gaming)  GAMING_REPOS+=("$name") ;;
    dev)     DEV_REPOS+=("$name") ;;
    drivers) DRIVERS_REPOS+=("$name") ;;
    ai)      AI_REPOS+=("$name") ;;
    desktop) DESKTOP_REPOS+=("$name") ;;
    ppa)     PPA_REPOS+=("$name") ;;
    *)       OTHER_REPOS+=("$name") ;;
  esac
done < <(find "$HOST_MIRROR_PATH" -maxdepth 1 -mindepth 1 -type d -print0 | sort -z)

MIRROR_ENTRY_COUNT="$REPO_COUNT"
if [[ -f "$HOST_MIRROR_PATH/mirror-repos.tsv" ]]; then
  MIRROR_ENTRY_COUNT=$(awk 'NR > 1 && NF {count++} END {print count+0}' "$HOST_MIRROR_PATH/mirror-repos.tsv")
fi

# Calculate approximate size
if command -v du >/dev/null 2>&1; then
  TOTAL_SIZE=$(du -sh "$HOST_MIRROR_PATH" 2>/dev/null | cut -f1 || echo "N/A")
fi

GENERATED_DATE=$(date '+%Y-%m-%d %H:%M:%S')
GENERATED_YEAR=$(date +%Y)

TMP_INDEX_FILE="$(mktemp)"
TMP_SUMMARY_FILE="$(mktemp)"
TMP_LOG_FILE="$(mktemp)"
trap 'rm -f "$TMP_INDEX_FILE" "$TMP_SUMMARY_FILE" "$TMP_LOG_FILE"' EXIT

# Generate HTML
cat > "$TMP_INDEX_FILE" << 'HTMLHEAD'
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>AILinux Mirror - APT Repository</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="AILinux APT Mirror - Ubuntu, Gaming, Development und AI Pakete">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📦</text></svg>">
<style>
:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --text-primary: #e6edf3;
  --text-secondary: #8b949e;
  --accent-cyan: #58a6ff;
  --accent-green: #3fb950;
  --accent-orange: #d29922;
  --accent-purple: #a371f7;
  --accent-red: #f85149;
  --border-color: #30363d;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg-primary);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  line-height: 1.6;
  min-height: 100vh;
}

.container { max-width: 1200px; margin: 0 auto; padding: 20px; }

header {
  background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-tertiary) 100%);
  border-bottom: 1px solid var(--border-color);
  padding: 30px 20px;
  margin-bottom: 30px;
}

header h1 {
  font-size: 2rem;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 12px;
}

header p { color: var(--text-secondary); }

.stats-bar {
  display: flex;
  gap: 30px;
  margin-top: 20px;
  flex-wrap: wrap;
}

.stat {
  background: var(--bg-primary);
  padding: 12px 20px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
}

.stat-value { font-size: 1.5rem; font-weight: 600; color: var(--accent-cyan); }
.stat-label { font-size: 0.85rem; color: var(--text-secondary); }

.search-box {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 25px;
}

.search-box input {
  width: 100%;
  padding: 12px 16px;
  font-size: 1rem;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-primary);
  outline: none;
}

.search-box input:focus { border-color: var(--accent-cyan); }
.search-box input::placeholder { color: var(--text-secondary); }

.quick-start {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 25px;
}

.quick-start h2 {
  font-size: 1.1rem;
  margin-bottom: 15px;
  color: var(--accent-green);
  display: flex;
  align-items: center;
  gap: 8px;
}

.code-block {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  padding: 15px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.9rem;
  overflow-x: auto;
  position: relative;
}

.code-block code { color: var(--accent-cyan); }

.copy-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
}

.copy-btn:hover { background: var(--border-color); color: var(--text-primary); }

.category {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  margin-bottom: 15px;
  overflow: hidden;
}

.category-header {
  padding: 15px 20px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  user-select: none;
  transition: background 0.2s;
}

.category-header:hover { background: var(--bg-tertiary); }

.category-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}

.category-count {
  background: var(--bg-tertiary);
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.category-icon { font-size: 1.3rem; }
.category-arrow { transition: transform 0.2s; color: var(--text-secondary); }
.category.open .category-arrow { transform: rotate(90deg); }

.category-content {
  display: none;
  border-top: 1px solid var(--border-color);
  padding: 15px 20px;
}

.category.open .category-content { display: block; }

.repo-list { list-style: none; }

.repo-item {
  padding: 10px 15px;
  border-radius: 6px;
  margin-bottom: 5px;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: background 0.2s;
}

.repo-item:hover { background: var(--bg-tertiary); }

.repo-item a {
  color: var(--accent-cyan);
  text-decoration: none;
  font-family: monospace;
}

.repo-item a:hover { text-decoration: underline; }

.repo-item .icon { font-size: 1.2rem; }

.repo-desc {
  color: var(--text-secondary);
  font-size: 0.85rem;
  margin-left: auto;
}

.tools-section {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 15px;
  margin-bottom: 25px;
}

.tool-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 15px;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: border-color 0.2s;
  text-decoration: none;
  color: var(--text-primary);
}

.tool-card:hover { border-color: var(--accent-cyan); }

.tool-card .icon { font-size: 1.5rem; }
.tool-card .desc { font-size: 0.8rem; color: var(--text-secondary); }

footer {
  text-align: center;
  padding: 30px 20px;
  color: var(--text-secondary);
  border-top: 1px solid var(--border-color);
  margin-top: 40px;
}

footer a { color: var(--accent-cyan); text-decoration: none; }
footer a:hover { text-decoration: underline; }

.hidden { display: none !important; }

@media (max-width: 768px) {
  .stats-bar { flex-direction: column; gap: 10px; }
  .stat { text-align: center; }
  header h1 { font-size: 1.5rem; }
}
</style>
</head>
<body>
HTMLHEAD

# Continue with dynamic content
cat >> "$TMP_INDEX_FILE" << HTMLHEADER
<header>
  <div class="container">
    <h1><span>📦</span> AILinux Mirror</h1>
    <p>APT Package Repository - Ubuntu, Gaming, Development &amp; AI</p>
    <div class="stats-bar">
      <div class="stat">
        <div class="stat-value">${MIRROR_ENTRY_COUNT}</div>
        <div class="stat-label">Published APT Entries</div>
      </div>
      <div class="stat">
        <div class="stat-value">${TOTAL_SIZE}</div>
        <div class="stat-label">Mirror Size</div>
      </div>
      <div class="stat">
        <div class="stat-value">${GENERATED_DATE}</div>
        <div class="stat-label">Last Update</div>
      </div>
    </div>
  </div>
</header>

<main class="container">
  <div class="search-box">
    <input type="text" id="searchInput" placeholder="🔍 Repository suchen... (z.B. ubuntu, docker, wine)">
  </div>

  <div class="quick-start">
    <h2><span>⚡</span> Quick Install</h2>
    <p style="color: var(--text-secondary); margin-bottom: 10px; font-size: 0.9rem;">Install all mirrored repositories valid for this OS:</p>
    <div class="code-block">
      <button class="copy-btn" type="button">Copy</button>
      <code>curl -fsSL "${PUBLIC_BASE}/add-ailinux-repo.sh" | sudo bash</code>
    </div>
    <p style="color: var(--text-secondary); margin: 12px 0 10px; font-size: 0.9rem;">Replace only upstream repositories already configured on this machine:</p>
    <div class="code-block">
      <button class="copy-btn" type="button">Copy</button>
      <code>curl -fsSL "${PUBLIC_BASE}/add-ailinux-repo.sh" | sudo bash -s -- --installed-only</code>
    </div>
  </div>

  <div class="tools-section">
    <a class="tool-card" href="${PUBLIC_BASE}/ailinux-archive-key.gpg">
      <span class="icon">🔐</span>
      <div>
        <div class="tool-title">GPG Key</div>
        <div class="desc">Repository Signing Key</div>
      </div>
    </a>
    <a class="tool-card" href="${PUBLIC_BASE}/add-ailinux-repo.sh">
      <span class="icon">⚙️</span>
      <div>
        <div class="tool-title">Install Script</div>
        <div class="desc">Automatisches Setup</div>
      </div>
    </a>
    <a class="tool-card" href="${PUBLIC_BASE}/summary.html">
      <span class="icon">📊</span>
      <div>
        <div class="tool-title">Status</div>
        <div class="desc">Mirror Health Report</div>
      </div>
    </a>
    <a class="tool-card" href="${PUBLIC_BASE}/log.html">
      <span class="icon">📜</span>
      <div>
        <div class="tool-title">Logs</div>
        <div class="desc">Sync Protokoll</div>
      </div>
    </a>
  </div>

  <div id="repoCategories">
HTMLHEADER

# Function to output category
output_category() {
  local cat_id="$1"
  local cat_icon="$2"
  local cat_name="$3"
  local cat_color="$4"
  shift 4
  local repos=("$@")

  [[ ${#repos[@]} -eq 0 ]] && return

  cat >> "$TMP_INDEX_FILE" << CATHEAD
    <div class="category open" data-category="${cat_id}">
      <div class="category-header">
        <div class="category-title">
          <span class="category-icon">${cat_icon}</span>
          <span>${cat_name}</span>
          <span class="category-count">${#repos[@]}</span>
        </div>
        <span class="category-arrow">▶</span>
      </div>
      <div class="category-content">
        <ul class="repo-list">
CATHEAD

  for repo in "${repos[@]}"; do
    local desc=""
    for pattern in "${!REPO_DESCRIPTIONS[@]}"; do
      if [[ "$repo" == *"$pattern"* ]]; then
        desc="${REPO_DESCRIPTIONS[$pattern]}"
        break
      fi
    done
    cat >> "$TMP_INDEX_FILE" << REPOITEM
          <li class="repo-item" data-name="${repo}">
            <span class="icon">📁</span>
            <a href="${PUBLIC_BASE}/${repo}/">${repo}/</a>
            <span class="repo-desc">${desc}</span>
          </li>
REPOITEM
  done

  cat >> "$TMP_INDEX_FILE" << CATFOOT
        </ul>
      </div>
    </div>
CATFOOT
}

# Output all categories
output_category "ubuntu" "🐧" "Ubuntu & Security" "orange" "${UBUNTU_REPOS[@]}"
output_category "gaming" "🎮" "Gaming & Multimedia" "purple" "${GAMING_REPOS[@]}"
output_category "dev" "💻" "Development Tools" "cyan" "${DEV_REPOS[@]}"
output_category "drivers" "🔧" "Drivers" "green" "${DRIVERS_REPOS[@]}"
output_category "ai" "🤖" "AI & Machine Learning" "red" "${AI_REPOS[@]}"
output_category "desktop" "🖥️" "Desktop Applications" "blue" "${DESKTOP_REPOS[@]}"
output_category "ppa" "📦" "Ubuntu PPAs" "yellow" "${PPA_REPOS[@]}"
output_category "other" "📂" "Other" "gray" "${OTHER_REPOS[@]}"


# Add install commands for every published manifest entry.
if [[ -f "$HOST_MIRROR_PATH/mirror-repos.tsv" ]]; then
  cat >> "$TMP_INDEX_FILE" <<'MANIFEST_HEAD'
  <section class="quick-start">
    <h2><span>🧩</span> Install individual mirrors</h2>
    <p style="color: var(--text-secondary); margin-bottom: 12px; font-size: 0.9rem;">Each command installs one published mirror entry. IDs come from mirror-repos.tsv.</p>
MANIFEST_HEAD
  while IFS=$'	' read -r repo_id label category path suite components arches upstream target_codename; do
    [[ "$repo_id" == "id" || -z "$repo_id" ]] && continue
    cat >> "$TMP_INDEX_FILE" <<MANIFEST_ROW
    <div style="margin: 12px 0 18px;">
      <div style="font-weight:600; margin-bottom:6px;">${label} <code>${repo_id}</code></div>
      <div class="code-block">
        <button class="copy-btn" type="button">Copy</button>
        <code>curl -fsSL "${PUBLIC_BASE}/add-ailinux-repo.sh" | sudo bash -s -- --select ${repo_id}</code>
      </div>
    </div>
MANIFEST_ROW
  done < "$HOST_MIRROR_PATH/mirror-repos.tsv"
  cat >> "$TMP_INDEX_FILE" <<'MANIFEST_FOOT'
  </section>
MANIFEST_FOOT
fi

# Footer and scripts
cat >> "$TMP_INDEX_FILE" << HTMLFOOTER
  </div>
</main>

<footer>
  <p>AILinux Repository &copy; ${GENERATED_YEAR} - <a href="https://ailinux.me">ailinux.me</a></p>
  <p style="margin-top: 8px; font-size: 0.8rem;">
    Base URL: <code>${PUBLIC_BASE}</code>
  </p>
</footer>
<script src="index.js" defer></script>
</body>
</html>
HTMLFOOTER

html_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

strip_ansi() {
  sed -E $'s/\x1B\\[[0-9;]*[[:alpha:]]//g'
}

write_log_snippet() {
  local title="$1"
  local logfile="$2"
  local lines="$3"

  echo "<section>"
  echo "<h2>${title}</h2>"
  echo "<pre><code>"
  if [[ -f "$logfile" ]]; then
    tail -n "$lines" "$logfile" | strip_ansi | html_escape
  else
    printf 'Log file not found: %s\n' "$logfile" | html_escape
  fi
  echo "</code></pre>"
  echo "</section>"
}

build_summary_page() {
  local inrelease_count release_sig_count amd64_packages i386_packages key_status
  local update_log_size postmirror_log_size update_log_mtime postmirror_log_mtime

  inrelease_count=$(find "$HOST_MIRROR_PATH" -maxdepth 6 -type f -name "InRelease" 2>/dev/null | wc -l | tr -d ' ')
  release_sig_count=$(find "$HOST_MIRROR_PATH" -maxdepth 6 -type f -name "Release.gpg" 2>/dev/null | wc -l | tr -d ' ')

  amd64_packages=0
  i386_packages=0
  if [[ -f "$HOST_MIRROR_PATH/repo.ailinux.me/dists/noble/main/binary-amd64/Packages" ]]; then
    amd64_packages=$(grep -c '^Package:' "$HOST_MIRROR_PATH/repo.ailinux.me/dists/noble/main/binary-amd64/Packages" || true)
  fi
  if [[ -f "$HOST_MIRROR_PATH/repo.ailinux.me/dists/noble/main/binary-i386/Packages" ]]; then
    i386_packages=$(grep -c '^Package:' "$HOST_MIRROR_PATH/repo.ailinux.me/dists/noble/main/binary-i386/Packages" || true)
  fi

  if [[ -f "$HOST_MIRROR_PATH/ailinux-archive-key.gpg" ]]; then
    key_status="present"
  else
    key_status="missing"
  fi

  if [[ -f "$UPDATE_LOGFILE" ]]; then
    update_log_size=$(du -h "$UPDATE_LOGFILE" 2>/dev/null | awk '{print $1}')
    update_log_mtime=$(date -r "$UPDATE_LOGFILE" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "n/a")
  else
    update_log_size="missing"
    update_log_mtime="n/a"
  fi

  if [[ -f "$POSTMIRROR_LOGFILE" ]]; then
    postmirror_log_size=$(du -h "$POSTMIRROR_LOGFILE" 2>/dev/null | awk '{print $1}')
    postmirror_log_mtime=$(date -r "$POSTMIRROR_LOGFILE" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "n/a")
  else
    postmirror_log_size="missing"
    postmirror_log_mtime="n/a"
  fi

  cat > "$TMP_SUMMARY_FILE" <<SUMMARY_HEAD
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>AILinux Mirror Summary</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body { background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 24px; }
h1, h2 { margin: 0 0 12px; }
h1 { font-size: 1.8rem; }
h2 { font-size: 1.15rem; margin-top: 22px; color: #58a6ff; }
.meta { color: #8b949e; margin-bottom: 18px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 16px 0 20px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px 14px; }
.label { color: #8b949e; font-size: 0.85rem; }
.value { font-size: 1.2rem; font-weight: 600; margin-top: 4px; }
pre { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; overflow: auto; max-height: 360px; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<main class="wrap">
  <h1>AILinux Mirror Summary</h1>
  <p class="meta">Generated: ${GENERATED_DATE}</p>
  <p class="meta"><a href="${PUBLIC_BASE}/index.html">Index</a> · <a href="${PUBLIC_BASE}/log.html">Logs</a></p>

  <section class="grid">
    <div class="card"><div class="label">Published APT Entries</div><div class="value">${MIRROR_ENTRY_COUNT}</div></div>
    <div class="card"><div class="label">Mirror Host Folders</div><div class="value">${REPO_COUNT}</div></div>
    <div class="card"><div class="label">Mirror Size</div><div class="value">${TOTAL_SIZE}</div></div>
    <div class="card"><div class="label">InRelease Files</div><div class="value">${inrelease_count}</div></div>
    <div class="card"><div class="label">Release.gpg Files</div><div class="value">${release_sig_count}</div></div>
    <div class="card"><div class="label">repo.ailinux.me amd64 Packages</div><div class="value">${amd64_packages}</div></div>
    <div class="card"><div class="label">repo.ailinux.me i386 Packages</div><div class="value">${i386_packages}</div></div>
    <div class="card"><div class="label">Archive Key</div><div class="value">${key_status}</div></div>
    <div class="card"><div class="label">update-mirror.log</div><div class="value">${update_log_size} (${update_log_mtime})</div></div>
    <div class="card"><div class="label">postmirror.log</div><div class="value">${postmirror_log_size} (${postmirror_log_mtime})</div></div>
  </section>
SUMMARY_HEAD

  write_log_snippet "Tail: update-mirror.log (120 lines)" "$UPDATE_LOGFILE" 120 >> "$TMP_SUMMARY_FILE"
  write_log_snippet "Tail: postmirror.log (120 lines)" "$POSTMIRROR_LOGFILE" 120 >> "$TMP_SUMMARY_FILE"

  cat >> "$TMP_SUMMARY_FILE" <<'SUMMARY_FOOT'
</main>
</body>
</html>
SUMMARY_FOOT
}

build_log_page() {
  cat > "$TMP_LOG_FILE" <<LOG_HEAD
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>AILinux Mirror Logs</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body { background: #0d1117; color: #e6edf3; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 0; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 24px; }
h1, h2 { margin: 0 0 12px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
h2 { margin-top: 20px; color: #58a6ff; font-size: 1.1rem; }
p { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #8b949e; }
pre { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; overflow: auto; max-height: 520px; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<main class="wrap">
  <h1>AILinux Mirror Logs</h1>
  <p>Generated: ${GENERATED_DATE} · <a href="${PUBLIC_BASE}/summary.html">Summary</a> · <a href="${PUBLIC_BASE}/index.html">Index</a></p>
LOG_HEAD

  write_log_snippet "update-mirror.log (tail 400)" "$UPDATE_LOGFILE" 400 >> "$TMP_LOG_FILE"
  write_log_snippet "postmirror.log (tail 400)" "$POSTMIRROR_LOGFILE" 400 >> "$TMP_LOG_FILE"

  cat >> "$TMP_LOG_FILE" <<'LOG_FOOT'
</main>
</body>
</html>
LOG_FOOT
}

# External JS for interactions (CSP-friendly)
cat > "$HOST_MIRROR_PATH/index.js" << 'JSSCRIPT'
function toggleCategory(header) {
  header.parentElement.classList.toggle('open');
}

function filterRepos() {
  const query = document.getElementById('searchInput').value.toLowerCase();
  const items = document.querySelectorAll('.repo-item');
  const categories = document.querySelectorAll('.category');

  items.forEach(item => {
    const name = item.dataset.name.toLowerCase();
    const desc = item.querySelector('.repo-desc')?.textContent.toLowerCase() || '';
    const match = name.includes(query) || desc.includes(query);
    item.classList.toggle('hidden', !match && query.length > 0);
  });

  // Hide empty categories
  categories.forEach(cat => {
    const visibleItems = cat.querySelectorAll('.repo-item:not(.hidden)');
    cat.classList.toggle('hidden', visibleItems.length === 0 && query.length > 0);
    if (query.length > 0 && visibleItems.length > 0) {
      cat.classList.add('open');
    }
  });
}

function copyCode(btn) {
  const code = btn.parentElement.querySelector('code').textContent;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = 'Copied!';
      setTimeout(() => btn.textContent = 'Copy', 2000);
    });
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = code;
  textarea.style.position = 'fixed';
  textarea.style.top = '-1000px';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    document.execCommand('copy');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  } catch (err) {
    btn.textContent = 'Copy failed';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  }
  document.body.removeChild(textarea);
}

document.addEventListener('DOMContentLoaded', () => {
  const searchInput = document.getElementById('searchInput');
  if (searchInput) {
    searchInput.addEventListener('keyup', filterRepos);
  }
  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', () => copyCode(btn));
  });
  document.querySelectorAll('.category-header').forEach(header => {
    header.addEventListener('click', () => toggleCategory(header));
  });
  document.querySelectorAll('.category').forEach(c => c.classList.add('open'));
});
JSSCRIPT

# Build shared target list from index targets
declare -a INDEX_TARGETS
INDEX_TARGETS+=("$INDEX_FILE_ON_MIRROR")

if [[ "${WRITE_ROOT_INDEX}" == "1" ]]; then
  INDEX_TARGETS+=("$HOST_REPO_PATH/index.html")
fi

if [[ -n "${EXTRA_INDEX_TARGETS:-}" ]]; then
  while IFS= read -r extra_target; do
    [[ -n "$extra_target" ]] || continue
    INDEX_TARGETS+=("$extra_target")
  done <<< "${EXTRA_INDEX_TARGETS}"
fi

deploy_file_to_index_targets() {
  local source_file="$1"
  local basename_target="$2"
  local log_label="$3"
  local target target_dir target_path
  declare -A emitted=()

  for target in "${INDEX_TARGETS[@]}"; do
    [[ -n "$target" ]] || continue
    target_dir="$(dirname "$target")"
    target_path="$target_dir/$basename_target"
    if [[ -n "${emitted[$target_path]+x}" ]]; then
      continue
    fi
    emitted["$target_path"]=1
    mkdir -p "$target_dir"
    cp "$source_file" "$target_path"
    chmod 644 "$target_path"
    echo "[generate-index] ${log_label}: $target_path erstellt."
  done
}

deploy_optional_file_to_index_targets() {
  local source_file="$1"
  local basename_target="$2"
  local log_label="$3"

  if [[ ! -f "$source_file" ]]; then
    echo "[generate-index] ${log_label}: Quelle fehlt, übersprungen (${source_file})."
    return 0
  fi

  deploy_file_to_index_targets "$source_file" "$basename_target" "$log_label"
}

# Copy JS alongside index.html targets
declare -a JS_TARGETS
JS_TARGETS+=("$HOST_MIRROR_PATH/index.js")
if [[ "${WRITE_ROOT_INDEX}" == "1" ]]; then
  JS_TARGETS+=("$HOST_REPO_PATH/index.js")
fi
if [[ -n "${EXTRA_INDEX_TARGETS:-}" ]]; then
  while IFS= read -r extra_target; do
    [[ -n "$extra_target" ]] || continue
    JS_TARGETS+=("$(dirname "$extra_target")/index.js")
  done <<< "${EXTRA_INDEX_TARGETS}"
fi
declare -A JS_GENERATED
for js_target in "${JS_TARGETS[@]}"; do
  [[ -n "$js_target" ]] || continue
  if [[ -n "${JS_GENERATED[$js_target]+x}" ]]; then
    continue
  fi
  if [[ "$js_target" == "$HOST_MIRROR_PATH/index.js" ]]; then
    JS_GENERATED["$js_target"]=1
    continue
  fi
  JS_GENERATED["$js_target"]=1
  mkdir -p "$(dirname "$js_target")"
  cp "$HOST_MIRROR_PATH/index.js" "$js_target"
  chmod 644 "$js_target"
done

# Build summary/log pages and deploy all HTML outputs
build_summary_page
build_log_page

deploy_file_to_index_targets "$TMP_INDEX_FILE" "index.html" "Index"
deploy_file_to_index_targets "$TMP_SUMMARY_FILE" "summary.html" "Summary"
deploy_file_to_index_targets "$TMP_LOG_FILE" "log.html" "Log"

# Legacy compatibility names used by older links
deploy_file_to_index_targets "$TMP_SUMMARY_FILE" "mirror-summary.html" "Legacy summary"
deploy_file_to_index_targets "$TMP_LOG_FILE" "live-log.html" "Legacy log"

# Publish the underlying raw log files alongside the HTML views
deploy_optional_file_to_index_targets "$UPDATE_LOGFILE" "update-mirror.log" "Raw update log"
deploy_optional_file_to_index_targets "$POSTMIRROR_LOGFILE" "postmirror.log" "Raw postmirror log"

echo "[generate-index] ${REPO_COUNT} Repositories katalogisiert, Gesamtgröße: ${TOTAL_SIZE}"

# Reconcile this host with mirrors only when explicitly requested.
# Index generation must never add unrelated APT sources as a side effect.
if [[ "${REINSTALL_LOCAL_MIRRORS:-0}" == "1" ]]; then
  ADD_REPO_SCRIPT="${HOST_MIRROR_PATH}/add-ailinux-repo.sh"
  if [[ -f "$ADD_REPO_SCRIPT" ]]; then
    echo "[generate-index] Reconciling currently installed upstream repos with AILinux mirrors ..."
    bash "$ADD_REPO_SCRIPT" --installed-only --no-update || echo "[generate-index] WARNING: add-ailinux-repo.sh exited with $?"
  fi
fi
