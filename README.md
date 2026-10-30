# <img src="scripts/icon.svg" width="48" height="48" alt="" align="absmiddle"> Diffly

<a href="docs/images/diffly-json.png"><img src="docs/images/diffly-json.png" alt="Diffly comparing two JSON files" width="600"></a>

Comparing the [JSON example files](tests/fixtures/diff/README.md).

<a href="docs/images/diffly-directories.png"><img src="docs/images/diffly-directories.png" alt="Diffly comparing two directories" width="600"></a>

Comparing the [example directories](tests/fixtures/directories/README.md).

Diffly is a two-pane text comparison and merge editor for macOS and Linux. This repository implements the Phase 1 MVP in [SPEC.md](SPEC.md).

## Run

Requires Python 3.11 or newer.

To install Diffly as a command available outside this repository, use `uv` from the repository root:

```sh
uv tool install --python 3.12 .
diffly
```

`uv` keeps Diffly and its dependencies in a dedicated environment instead of installing them into the system Python. If `diffly` is not found, run `uv tool update-shell` and open a new terminal. After changing the source code, run `uv tool install --reinstall --python 3.12 .` to update the installed copy.

For development, create a virtual environment in the repository instead:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/diffly
```

To build a macOS app that can launch from Applications:

```sh
scripts/build-macos-app.sh                    # build dist/Diffly.app only
scripts/build-macos-app.sh --install          # also replace /Applications/Diffly.app
scripts/build-macos-app.sh --install --open   # install and launch it
```

Without `--install`, drag `Diffly.app` from the `dist` folder into Applications in Finder. The build includes Python, Qt, and the bundled theme configuration, so the `.app` does not depend on this repository or its virtual environment. Run the build again after code changes and replace the copy in Applications. The app is built for the current Mac's CPU architecture and is signed locally; distributing it to other Macs requires Developer ID signing and notarization.

Open one or two files directly from the command line:

```sh
.venv/bin/diffly left.txt right.txt
.venv/bin/diffly left.txt
```

Relative paths are resolved from the current working directory. The first file opens in the left pane and the optional second file opens in the right pane. Run `diffly --help` for usage.

Open a file into each pane with **File → Open Left/Right**. Edit either pane independently. Colored rows show differences, and stronger color marks changed text within replaced lines. Click a changed row or navigate with the toolbar to select a diff block, then apply it with the toolbar or delete it from the Edit menu. Each action is one Undo operation in its destination editor. **Cmd+S** on macOS (**Ctrl+S** on Linux) saves the active editor; **File → Save As…** also applies to that editor. A failed save leaves the original file untouched.

The **Directories** tab opens first. Enter each directory path above its tree and press Enter to compare, or use the folder icons; choosing the second folder starts comparison automatically. The first toolbar button refreshes the comparison. The two trees show matching paths side by side: yellow marks different content or changed folders, red marks left-only items, and green marks right-only items. Files are compared by content, not only by timestamp. Use ↑/↓ in the toolbar to move among different files and ←/→ to copy the selected file to the indicated side. Diffly asks before each copy; check **Don't ask again this session** in the confirmation to skip later prompts until the app closes. The trees rescan after copying. Double-click a file present on both sides to open the pair in the **Files** tab. Symbolic links are compared by their targets and are not followed.

Open the directory comparison directly with `.venv/bin/diffly --directories left_dir right_dir`. A sample pair is available under `tests/fixtures/directories/`.

Open **Settings → Directory ignore masks…** to add, edit, or remove masks. A mask without `/` matches a file or folder name at any depth, such as `*.log` or `node_modules`; a mask with `/` matches a path relative to the selected root, such as `build/*.tmp`. Ignored folders and everything inside them are skipped. Masks are saved in the user TOML configuration as `ignore_patterns` and applied to both directories. The initial list is empty.

In the **Files** tab, Refresh reloads opened files from disk and compares them again; it asks before discarding unsaved edits. The toolbar also navigates between differences, applies the selected block in either direction, and pastes clipboard text into the active editor at its cursor. Hovering a toolbar button shows a border without changing the icon color. The large left and right arrows show the destination of each apply action. Both editors show line numbers; recognized source and markup files also show faint dotted indent guides every four spaces by default to trace nesting levels. Missing lines are shown as diagonally hatched spaces, aligning the following text without changing either file. A slightly wider colored diff overview at the far right replaces the visible vertical scrollbar. Click or drag its viewport marker to scroll both panes; the mouse wheel over either editor also scrolls them together. One horizontal scrollbar below both panes controls their horizontal offsets; horizontal trackpad gestures and Shift+wheel scroll both panes too. A shorter pane stops at its own right edge.

On macOS, Diffly sets the native toolbar to the compact inline titlebar style. The editors use the full width of the window. The status bar shows deleted, added, changed, and identical line counts. Next to **Tab: 2/4 spaces**, the **Encoding** button shows the active pane's encoding. Its menu lists **Auto** and the available encodings. Choosing one reopens an existing file with that encoding; for an untitled pane it sets the encoding used when saving. Click **Tab: 2/4 spaces** to switch tab stops and indent guides; the choice is saved in the user TOML config. A double-click on an empty part of the Qt toolbar toggles a maximized window without entering fullscreen. A double-click on the empty title area also toggles a maximized window without entering fullscreen.

On Linux, Diffly draws its own title bar so the menu, diff toolbar, window title, and window controls share one row. Drag the title to move the window, double-click it to maximize or restore, and use the bottom-right size grip to resize.

When run from a terminal, **Ctrl+C** closes Diffly through the usual unsaved-changes check.

On macOS, press **Command+Option+Down** for the next difference and **Command+Option+Up** for the previous one. **Command+Option+Left/Right** applies the selected difference toward the arrow. On Linux, use **Ctrl+Alt** with the same arrow keys. Navigation moves the text cursor to the changed line in the active editor. **Command+Option+Delete** on macOS or **Ctrl+Alt+Delete** on Linux deletes the selected block from the active editor. Plain arrow and Delete keys retain their usual text-editing behavior inside an editor.

The bundled [TOML configuration](src/diffly/config.toml) defines font size, tab width, the initial theme, and four themes: Gruvbox Dark, Dark, Light, and Solarized Dark. Choose a theme from the **Theme** menu; Diffly saves that choice to the user TOML configuration. For other persistent overrides, create `~/.config/diffly/config.toml` with only the values you want to change, for example:

```toml
[settings]
theme = "light"
font_family = "Menlo"
font_size = 14
tab_width = 4
indent_guide_width = 4
ignore_patterns = ["*.log", "node_modules"]
```

Set `DIFFLY_CONFIG` to use another TOML path. An empty `font_family` selects the system monospace font. Theme definitions in the override can replace individual colors under `[themes.<theme_id>]`.

UTF-8 and Unicode BOMs are detected directly; other text encodings are detected with charset-normalizer. Short or ambiguous files may need an explicit choice from the **Encoding** menu. Diffly preserves the detected encoding, BOM, and line endings when saving an opened file. JSON, Python, JavaScript, PHP, Go, Rust, Bash, YAML, TOML, and Markdown files have syntax coloring through [Pygments](https://pygments.org/docs/lexers/); untitled buffers are detected from their first lines. Lexing runs in a separate process after a short debounce, and results are accepted only for the current document revision. Existing syntax and diff colors remain visible while new results are computed. Only changed syntax blocks are reformatted; cached colors follow line insertions and local edits. Syntax coloring is disabled above 2 MB while large-file editing remains a future phase. The current editor loads whole files; virtualized large-file editing and distributable packaging are later phases.

## Development

To use the committed `uv.lock` for reproducible development dependencies:

```sh
uv run --locked --extra dev pytest
```

Alternatively, install the development dependencies with pip:

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Set `DIFFLY_NO_DIALOGS=1` for scripted or automated runs: modal dialogs are not shown, questions answer Yes, notices are printed to stderr and file pickers cancel. The test suite enables it in `tests/conftest.py`.
