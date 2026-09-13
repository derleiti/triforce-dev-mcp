# Discuss with AI Fix — 2026-03-22

<!-- AILINUX_STATUS_START -->
## 2026-06-05 documentation refresh

- Production checkout: `/home/zombie/workspace/triforce`.
- Production branch: `nova-nextlevel-20260603`.
- Published HEAD: `16f43b8a`.
- Service: `triforce.service`.
- API health endpoint: `https://api.ailinux.me/health`.
- Default model route: `ollama/gemma4:12b`.
- Runtime spool: `/home/zombie/workspace/triforce/data/crawler_spool/` is ignored and should exist locally.

Validation checklist: clean branch status, successful Python compile, active service, and healthy API response.
<!-- AILINUX_STATUS_END -->

# Deployed directly to Docker containers (wordpress_fpm)
# Files: ailinux-nova-dark/assets/js/app.js + dist/app.js
# Also: ailinux-nova-dark-dev same files
# Bug #16: 3 HTML ID mismatches + missing fallback model list
# IDs fixed: ai-send→ai-discuss-send, ai-input→ai-discuss-input, ai-output→ai-discuss-chat
# Default model: llama4:latest → gpt-oss:120b-cloud
# 14 fallback models added for when /v1/models fails

# Theme Selector: Dark/Light + Wide Layout — 2026-03-22
# Deployed directly to Docker containers (wordpress_fpm)
# Files changed in both themes (ailinux-nova-dark + dev):
#   assets/js/color-mode.js — cookie persistence (was localStorage)
#   dist/colorMode.js — same
#   header.php — added width-toggle button
#   css/global-theme-fixes.css — [data-width="wide"] CSS rules
#
# Cookies: ailinux_theme (dark/light), ailinux_width (normal/wide)
# Expiry: 365 days, SameSite=Lax, path=/
# Width toggle: full-width content, wider header/footer, wider AI panel

# Discuss AI: Curated Model List with Groups — 2026-03-22
# Problem: /v1/models returns 612 models — unusable in dropdown
# Fix: Curated list with 5 groups, ~20 best models
# Groups: Empfohlen (5), Code (4), Reasoning (4), Schnell (4), Cloud (4)
# Fetches /v1/models, filters against CURATED list, uses optgroups
# Fallback: 14 hardcoded models if API fails
# Display: clean names without ollama/ prefix and :cloud suffix

## 2026-06-05 - Documentation and repository hygiene update

- Documented runtime-artifact boundaries for logs, Python caches, Docker repository mirror state, generated client package builds, and local patch backups.
- Clarified Git safety workflow: verify `pwd`, repository root, branch, and status before staging.
- Confirmed that `app/routes_sd3.py` and `app/routes_vision.py` remain active compatibility modules while imported by `app/main.py`.
- Validation target for docs-only hygiene work: `python3 -m compileall app -q`.
