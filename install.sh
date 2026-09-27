#!/bin/sh
set -eu
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v uv >/dev/null 2>&1 || { echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' >&2; exit 1; }
if [ -e "$APP_ROOT/.venv" ] && [ ! -x "$APP_ROOT/.venv/bin/python" ]; then
    backup="$APP_ROOT/.venv-backup-$(date +%Y%m%d-%H%M%S)-$$"
    mv "$APP_ROOT/.venv" "$backup"
    echo "Preserved incompatible runtime: $backup"
fi
if [ ! -x "$APP_ROOT/.venv/bin/python" ]; then
    uv venv "$APP_ROOT/.venv" --python "${PYTHON_VERSION:-3.12}"
fi
uv pip install --python "$APP_ROOT/.venv/bin/python" --requirement "$APP_ROOT/requirements.txt"
"$APP_ROOT/.venv/bin/python" -c 'import fitz, pdf2zh_next, babeldoc'
echo 'Installation complete. Run ./doctor.sh to check the runtime and API key.'
