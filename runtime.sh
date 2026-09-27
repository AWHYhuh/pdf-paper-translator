#!/bin/sh
# Sourced by launchers; APP_ROOT is the directory containing this file.
find_python() {
    for runtime in "${PDF_PAPER_TRANSLATOR_RUNTIME:-$APP_ROOT/.venv}" "$APP_ROOT/.venv" "$APP_ROOT/../.venv-pdf2zh-next"; do
        if [ -x "$runtime/bin/python" ] && "$runtime/bin/python" -c 'import fitz' >/dev/null 2>&1; then
            PYTHON="$runtime/bin/python"
            return 0
        fi
    done
    echo 'Mac/Linux runtime missing. Run install.sh (Windows virtual environments cannot be reused).' >&2
    return 1
}
