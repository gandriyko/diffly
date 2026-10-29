# Visual diff fixtures

Open a pair with the matching **Diffly: … diff fixture** configuration in
`.vscode/launch.json`. The files are valid examples for manual comparison and
merge checks; they are not expected-output snapshots.

- **Python:** repeated event lines, a new dataclass field and function argument,
  Unicode normalization, filtering, and changes in several separated blocks.
- **JavaScript:** inserted helper, optional chaining and nullish coalescing,
  changed template literals, multi-line replacements, and a longer inserted block.
- **JSON:** nested object and array edits, reordered keys, added and removed
  records, Unicode text, and changes between `null` and an empty array.
