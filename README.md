# <img src="scripts/icon.svg" width="48" height="48" alt="" align="absmiddle"> Diffly

<a href="docs/images/diffly-json.png"><img src="docs/images/diffly-json.png" alt="Diffly comparing two JSON files" width="600"></a>

Comparing the [JSON example files](tests/fixtures/diff/README.md).

<a href="docs/images/diffly-directories.png"><img src="docs/images/diffly-directories.png" alt="Diffly comparing two directories" width="600"></a>

Comparing the [example directories](tests/fixtures/directories/README.md).

Diffly is a text comparison and file merge editor for macOS and Linux. The two-pane comparison is described in [SPEC.md](SPEC.md).

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

To merge three versions of one text file, run:

```sh
.venv/bin/diffly --merge base.txt left.txt right.txt
.venv/bin/diffly --merge base.txt left.txt right.txt --output result.txt
```

To use Diffly as a Git mergetool, install the `diffly` command on your `PATH` and configure Git:

```sh
git config --global merge.tool diffly
git config --global mergetool.diffly.cmd 'diffly --merge "$BASE" "$LOCAL" "$REMOTE" --output "$MERGED"'
git config --global mergetool.diffly.trustExitCode true
```

After a conflicting `git merge`, run `git mergetool --tool=diffly`. Git passes the common BASE (which may be empty), the current branch as LOCAL, the incoming branch as REMOTE, and the working-tree file as MERGED. Diffly waits for the window to close. When the existing MERGED text differs from the generated merge, choose whether to open that existing text or start a fresh merge; the existing file is not changed until Save. **File → Recreate Merge Result** switches from the existing text to Diffly's three-way result. Git receives success only after the result is saved to MERGED, all conflicts are marked resolved, and the file remains unchanged through closing. Closing without completing these steps reports failure to Git. A direct `diffly` process is required so Git can wait for its exit; launching the app with a detached `open -a` command will not provide that status.

The **Merge** tab appears only for `--merge`. It places read-only Left and Right beside an editable Result in the center. Separate changes merge automatically; competing changes initially show Left's text in Result with a highlighted unresolved region. Base remains available through **Edit → Use Base**. Result does not insert conflict marker lines. An arrow beside a changed block copies that side's text into Result; both gutter arrows for that block disappear after application and return on Undo. The gutters use the same curved, theme-colored connection bands as the Files tab. The icon toolbar navigates conflicts and chooses Left or Right for the selected conflict. **F7** and **Shift+F7** navigate conflicts; **Ctrl+Alt+1/2/3** select Left, Right, or Base. Result starts in memory. **File → Save Result** offers a sibling of Left named `<stem>.merged<suffix>` by default; `--output` selects a path without writing it on launch. Saving with unresolved conflicts asks for confirmation. Result inherits Left's encoding, BOM, and newline style; its encoding menu is in the status bar. **File → Recreate Result** and **File → Reload Merge Sources** ask before discarding edits. Merge sessions store paths only, so reopening recomputes the merge when started with `--merge`.

Open a file into each pane with **File → Open Left/Right**. Edit either pane independently. Colored rows show differences, and stronger color marks changed text within replaced lines. Click a changed row or navigate with the toolbar to select a diff block, then apply it with the toolbar or delete it from the Edit menu. Each action is one Undo operation in its destination editor. **Cmd+S** on macOS (**Ctrl+S** on Linux) saves the active editor; **File → Save As…** also applies to that editor. A failed save leaves the original file untouched.

Use **Edit → Find…** (**Cmd/Ctrl+F**) or **Edit → Replace…** (**Cmd/Ctrl+H**) to open a compact panel above the footer. It searches only the current editor and follows the active pane if you switch editors. Use the arrow buttons, **F3** for the next match, or **Shift+F3** for the previous one. The panel also supports optional case matching, replacing one match, and replacing all matches in the current editor. Replace All is one Undo step. Search and replacement use literal text; close the panel with **Esc** or ×.

Open the **Settings** tab directly or through **Settings → Open Settings** (**Cmd/Ctrl+,**). The page groups appearance, editor, file comparison, and directory filtering preferences. Changes apply immediately and are saved to the user TOML configuration. The line number and syntax highlighting switches apply to both Files and Merge; line numbers are off by default. File comparison can ignore trailing spaces and tabs, all spaces and tabs, or letter case when determining differences. These rules change which differences are shown; applying a block still copies its original text. A saved file comparison remembers its rules.

Use **File → Save Comparison…** to name the current pair of files or directories. Reopen it from **Saved Comparisons**; **Recent Comparisons** keeps the last ten successful pairs and can be cleared. Saved comparisons can be removed from the menu. Diffly stores paths and comparison options, not file contents, in `sessions.json` beside the user configuration file. Reopening checks that both paths still exist and asks before discarding unsaved editor changes.

The **Directories** tab opens first. Enter each directory path above its tree and press Enter to compare, or use the folder icons; choosing the second folder starts comparison automatically. The first toolbar button refreshes the comparison. The two trees show matching paths side by side: yellow marks different content or changed folders, red marks left-only items, and green marks right-only items. The footer shows colored counts for different, left-only, right-only, and matching files, plus changed folders. Files are compared by content, not only by timestamp. Use ↑/↓ in the toolbar to move among different files and ←/→ to copy the selected file or folder to the indicated side. For a selected folder, Diffly copies the entire tree, including hidden and ignored files, and replaces the destination folder completely; destination-only children are removed. Folder replacement always asks for confirmation. File copies also ask, with a **Don't ask again this session** option for later file copies. The trees rescan after copying. Double-click a file present on both sides to open the pair in the **Files** tab. Symbolic links are compared by their targets and are not followed.

Open the directory comparison directly with `.venv/bin/diffly --directories left_dir right_dir`. A sample pair is available under `tests/fixtures/directories/`.

In **Settings → Directory filtering**, add, edit, or remove custom masks. A mask without `/` matches a file or folder name at any depth, such as `*.log` or `node_modules`; a mask with `/` matches a path relative to the selected root, such as `build/*.tmp`. Ignored folders and everything inside them are skipped. Python caches, editor and temporary files, and build and dependency folders are filtered immediately by three built-in groups. Uncheck a group on the settings page to include its files. The initial custom-mask list is empty.

By default, directory comparisons also read `.gitignore` files in each selected directory, nested directories, and parent directories up to the Git working-tree root. Outside a Git working tree, Diffly reads accessible parent `.gitignore` files. More specific files can override parent rules, including with `!` negation; an ignored folder remains pruned. Uncheck **Use .gitignore from selected and parent folders** on the settings page to compare those paths too. The choice, enabled preset groups, and custom masks are saved in the user TOML configuration as `use_gitignore`, `ignore_presets`, and `ignore_patterns`. Custom masks and enabled presets always exclude matching paths, including paths that a `.gitignore` rule would re-include.

In the **Files** tab, Refresh reloads opened files from disk and compares them again; it asks before discarding unsaved edits. The toolbar also navigates between differences, applies the selected block in either direction, and pastes clipboard text into the active editor at its cursor. Hovering a toolbar button shows a border without changing the icon color. The large left and right arrows show the destination of each apply action. Recognized source and markup files show faint dotted indent guides every four spaces by default to trace nesting levels. Missing lines are shown as diagonally hatched spaces, aligning the following text without changing either file. A slightly wider colored diff overview at the far right replaces the visible vertical scrollbar. Click or drag its viewport marker to scroll both panes; the mouse wheel over either editor also scrolls them together. One horizontal scrollbar below both panes controls their horizontal offsets; horizontal trackpad gestures and Shift+wheel scroll both panes too. A shorter pane stops at its own right edge.

On macOS, Diffly sets the native toolbar to the compact inline titlebar style. The editors use the full width of the window. The status bar shows deleted, added, changed, and identical line counts. The **Encoding** button shows the active pane's encoding. Its menu lists **Auto** and the available encodings. Choosing one reopens an existing file with that encoding; for an untitled pane it sets the encoding used when saving. Change tab stops and indent guides on the **Settings** page; the choice is saved in the user TOML config. A double-click on an empty part of the Qt toolbar toggles a maximized window without entering fullscreen. A double-click on the empty title area also toggles a maximized window without entering fullscreen.

On Linux, Diffly draws its own title bar so the menu, diff toolbar, window title, and window controls share one row. Drag the title to move the window, double-click it to maximize or restore, and use the bottom-right size grip to resize.

When run from a terminal, **Ctrl+C** closes Diffly through the usual unsaved-changes check.

On macOS, press **Command+Option+Down** for the next difference and **Command+Option+Up** for the previous one. **Command+Option+Left/Right** applies the selected difference toward the arrow. On Linux, use **Ctrl+Alt** with the same arrow keys. Navigation moves the text cursor to the changed line in the active editor. **Command+Option+Delete** on macOS or **Ctrl+Alt+Delete** on Linux deletes the selected block from the active editor. Plain arrow and Delete keys retain their usual text-editing behavior inside an editor.

The bundled [TOML configuration](src/diffly/config.toml) defines font size, tab width, initial comparison rules, the initial theme, and four themes: Gruvbox Dark, Dark, Light, and Solarized Dark. Choose a theme on the **Settings** page; Diffly saves that choice to the user TOML configuration. For other persistent overrides, create `~/.config/diffly/config.toml` with only the values you want to change, for example:

```toml
[settings]
theme = "light"
font_family = "Menlo"
font_size = 14
tab_width = 4
indent_guide_width = 4
ignore_trailing_whitespace = false
ignore_whitespace = false
ignore_case = false
ignore_patterns = ["*.log", "node_modules"]
use_gitignore = true
ignore_presets = ["python", "temporary", "build"]
```

Set `DIFFLY_CONFIG` to use another TOML path. An empty `font_family` selects the system monospace font. Theme definitions in the override can replace individual colors under `[themes.<theme_id>]`.

UTF-8 and Unicode BOMs are detected directly; other text encodings are detected with charset-normalizer. Short or ambiguous files may need an explicit choice from the **Encoding** menu. Diffly preserves the detected encoding, BOM, and line endings when saving an opened file. Syntax coloring through [Pygments](https://pygments.org/docs/lexers/) supports JSON, Python, JavaScript, TypeScript (`.ts`, `.tsx`), JSX, HTML, CSS, SCSS, XML/SVG, SQL, PHP, Go, Rust, Bash, YAML, TOML, Markdown, Dockerfile, Makefile, and INI-style files (`.ini`, `.cfg`, `.editorconfig`). Dockerfile variants (`Dockerfile.dev`), `GNUmakefile`, and `.mk` files are recognized by name or extension. Untitled buffers are detected from their first lines. Lexing runs in a separate process after a short debounce, and results are accepted only for the current document revision. Existing syntax and diff colors remain visible while new results are computed. Only changed syntax blocks are reformatted; cached colors follow line insertions and local edits. Syntax coloring is disabled above 2 MB while large-file editing remains a future phase. The current editor loads whole files; virtualized large-file editing and distributable packaging are later phases.

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
