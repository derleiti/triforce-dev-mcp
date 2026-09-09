#!/bin/sh
set -eu
exec "$(dirname "$0")/stack.sh" down "${1:-all}"
