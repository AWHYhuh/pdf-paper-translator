#!/bin/sh
set -eu
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$APP_ROOT/runtime.sh"
find_python
exec "$PYTHON" -X utf8 "$APP_ROOT/paperflow.py" "$@"
