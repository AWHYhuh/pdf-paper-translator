#!/bin/sh
set -eu
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$APP_ROOT/runtime.sh"
find_python
"$PYTHON" -c 'import platform, importlib.metadata as m; print(platform.platform()); [print(p, m.version(p)) for p in ("pdf2zh-next", "babeldoc", "PyMuPDF")]'
"$PYTHON" -c 'import os, sys; ready=bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()); print("DEEPSEEK_API_KEY: " + ("configured (hidden)" if ready else "missing")); sys.exit(0 if ready else 1)'
