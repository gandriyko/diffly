# Directory comparison fixture

Run the sample pair from the repository root:

```sh
.venv/bin/python -m diffly --directories tests/fixtures/directories/left tests/fixtures/directories/right
```

The pair contains identical files, changed files with the same size, files and folders present on only one side, nested differences, and a file-versus-folder conflict. `logs/session.log` and `node_modules` demonstrate ignore masks: add `*.log` and `node_modules` under **Settings → Directory ignore masks…** and compare again to see those entries disappear.

These are text files for visual and manual interaction checks. Double-click `src/app.py` or `docs/guide.md` to open the file pair in the Files tab.
