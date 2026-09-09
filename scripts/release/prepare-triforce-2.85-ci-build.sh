#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.14}"
BACKEND_RUNTIME="${BACKEND_RUNTIME:-$RUNNER_TEMP/triforce-2.85-backend-runtime}"
GUI_VENV="${GUI_VENV:-$RUNNER_TEMP/triforce-2.85-gui-build-venv}"
GUI_OUT="${GUI_OUT:-$RUNNER_TEMP/triforce-2.85-nuitka}"

"$PYTHON_BIN" --version
rm -rf "$BACKEND_RUNTIME" "$GUI_VENV" "$GUI_OUT"
"$PYTHON_BIN" -m venv "$BACKEND_RUNTIME"
"$BACKEND_RUNTIME/bin/python" -m pip install --upgrade pip
"$BACKEND_RUNTIME/bin/pip" install -r "$ROOT/requirements-lock-2.85-beta1.txt"
"$BACKEND_RUNTIME/bin/pip" check
PYTHONPATH="$ROOT" "$BACKEND_RUNTIME/bin/python" - <<'PY'
from google import genai
from app.config import VERSION
print("BACKEND_RUNTIME_OK", VERSION, getattr(genai, "__version__", "unknown"))
PY

"$PYTHON_BIN" -m venv "$GUI_VENV"
"$GUI_VENV/bin/python" -m pip install --upgrade pip
"$GUI_VENV/bin/pip" install 'PyQt6==6.11.0' 'nuitka==4.2.1' ordered-set zstandard patchelf
QT_QPA_PLATFORM=offscreen PYTHONPATH="$ROOT" "$GUI_VENV/bin/python" "$ROOT/control_center/main.py" --smoke-test
"$GUI_VENV/bin/python" -m nuitka \
  --mode=standalone \
  --enable-plugin=pyqt6 \
  --include-qt-plugins=platforms,imageformats,iconengines,wayland \
  --include-package=control_center \
  --output-dir="$GUI_OUT" \
  --output-filename=triforce-control-center \
  "$ROOT/control_center/main.py"
test -x "$GUI_OUT/main.dist/triforce-control-center"
printf 'BACKEND_RUNTIME=%s\nGUI_BUNDLE=%s\n' "$BACKEND_RUNTIME" "$GUI_OUT/main.dist"
