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
    --icon scripts/icon.icns \
    --collect-data diffly \
    --collect-submodules pygments.lexers \
    src/diffly/__main__.py

printf '%s\n' 'Built dist/Diffly.app'

# Only ask in an interactive terminal so scripted builds never block on input.
if [ -t 0 ]; then
    printf '%s' 'Install to /Applications? [y/N] '
    read -r answer
    case "$answer" in
        [yY]*)
            rm -rf /Applications/Diffly.app
            cp -R dist/Diffly.app /Applications/
            printf '%s\n' 'Installed /Applications/Diffly.app'
            ;;
    esac
fi
