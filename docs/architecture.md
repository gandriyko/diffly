# Architecture

`core/diff/engine.py` and `core/documents/model.py` have no Qt imports. `TaskScheduler` runs file operations in a bounded Qt thread pool and line comparison in one worker process. `MainWindow` owns GUI state and checks both revision numbers before accepting a result or transferring a block.

Each editor owns its Qt undo stack. A block transfer replaces the target text inside one edit block. Save uses a revision snapshot: editing while a save runs keeps the pane dirty. Atomic replacement and a file stamp check protect the on-disk copy from most accidental overwrites.

The two editors sit directly beside each other. A right-edge diff overview displays whole-file changes and controls synchronized vertical scrolling by click or drag. A single horizontal scrollbar below both editors shares a pixel offset; shorter documents clamp at their own right edge. Horizontal trackpad gestures and Shift+wheel use the same shared scrollbar. An invisible scrollbar tracks the shared position and receives wheel events from either editor. The diff worker also computes changed character spans within replaced lines; the editors render those spans with a stronger color. Colors and editor font settings come from the bundled TOML configuration with an optional user override. Choosing a theme writes only the selected theme into the user TOML file.

`CodeEditor` extends `QPlainTextEdit` with a side area for line numbers. The line-number gutters contain no diff overview; one overview sits to the right of both panes. Both editors draw faint dotted indent guides for recognized source and markup files over visible text blocks only. `MainWindow` selects the diff under the text cursor and provides toolbar actions for navigating and applying it.

On macOS, a native `NSToolbar` uses Unified Compact style in the titlebar. Its custom vector icons call the same Qt actions as the fallback toolbar. The file footer reads metadata from each document's last loaded or saved file stamp. The diff worker returns line counts for the status bar alongside the hunk and character-span data.

Pygments tokenizes JSON, Python, JavaScript, PHP, Go, Rust, Bash, YAML, TOML, and Markdown in a dedicated worker process after a short debounce. The GUI accepts only a matching document revision and uses `QSyntaxHighlighter` to apply changed blocks in small batches. Each block keeps its cached spans in `QTextBlockUserData`, which follows line insertions and removals; local edits move existing token offsets until the worker returns. The last diff selections remain visible during recomputation, while diff actions are disabled until a current result arrives.

Directory comparison can reuse the scheduler and file service while providing its own core model and view.
