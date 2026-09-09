#!/bin/sh
set -eu
exec "$(dirname "$0")/stack.sh" status "${1:-all}"
