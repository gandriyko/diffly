#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    printf '%s\n' 'This build requires macOS.' >&2
    exit 1
fi

cd "$(dirname "$0")/.."
uv run --with pyinstaller==6.22.3 pyinstaller \
    --noconfirm \
    --windowed \
    --onedir \
    --name Diffly \
    --osx-bundle-identifier com.diffly.app \
    --collect-data diffly \
    --collect-submodules pygments.lexers \
    src/diffly/__main__.py

printf '%s\n' 'Built dist/Diffly.app'
