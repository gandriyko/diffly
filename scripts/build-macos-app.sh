#!/bin/sh
set -eu

usage() {
    printf '%s\n' "Usage: $0 [--install] [--open]" \
        '  --install  replace /Applications/Diffly.app with the new build' \
        '  --open     launch the app after building (the installed copy with --install)'
}

install=0
open_app=0
for arg in "$@"; do
    case "$arg" in
        --install) install=1 ;;
        --open) open_app=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

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
    --icon scripts/icon.icns \
    --collect-data diffly \
    --collect-submodules pygments.lexers \
    src/diffly/__main__.py

printf '%s\n' 'Built dist/Diffly.app'

app=dist/Diffly.app
if [ "$install" -eq 1 ]; then
    rm -rf /Applications/Diffly.app
    cp -R dist/Diffly.app /Applications/
    app=/Applications/Diffly.app
    printf '%s\n' 'Installed /Applications/Diffly.app'
fi

if [ "$open_app" -eq 1 ]; then
    open "$app"
fi
