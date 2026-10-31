# Python merge fixture

Open the files in Base, Left, Right order:

```sh
diffly --merge tests/fixtures/merge/base.txt tests/fixtures/merge/left.txt tests/fixtures/merge/right.txt
```

The `.txt` names keep the launch configuration stable. Each file is valid Python
with a Python shebang, so Merge also highlights its syntax. Search for `CASE NN`
to jump to a scenario. The unchanged case headers and endings separate nearby
edits without padding the files with repeated text.

| Case | Scenario | Expected merge |
| --- | --- | --- |
| 01–03 | One-line replacement, simultaneous insertion, deletion versus edit | Three small conflicts |
| 04 | Eight base lines versus eleven Left and five Right lines | Medium conflict; unequal ribbon and hatched gaps |
| 05 | Twenty-five base lines versus thirty-two Left and seventeen Right lines | Large conflict; long ribbon and gaps |
| 06–07 | Competing multiline insertions and partially overlapping replacements | Conflicts with different start/end lines |
| 08–11 | Whitespace, letter case, Unicode, and blank-line deletion versus comment | Four short conflicts, including an empty side |
| 12–16 | Identical edit, one-sided insertion/deletion, adjacent independent edits, identical insertion | Automatic merge, with gaps where lengths differ |
| 17 | Different keys edited on the same source line | Line-level conflict |
| 18 | Large deletion versus a twenty-two-line rewrite | Large conflict with an empty Left block |
| 19 | Left and Right replace partly overlapping ranges, preserving the edges | Medium conflict with shifted line positions |
| 20 | One-sided edit after a conflict | Automatic merge |
| 21 | Three consecutive equal-length competing edits | Three distinct small conflicts and arrows |
| 22 | Different multiline insertions at the same tail position | Conflict with unequal heights |
| 23 | Literal `<<<<<<< LEFT` inside a Python string plus a real edit | One real conflict; marker text stays literal |
| 24–26 | One-sided expansions and identical deletion | Automatic merge and hatched gaps |
| 27–28 | Partial overlap near the end and final-line replacement | Two conflicts close to EOF |

Expected initial result: **31 regions, 21 conflicts**. Unresolved conflicts start
with Left's text; the merge engine does not add marker lines. The 28 cases use
roughly 250 lines per file, keeping the conflict density high while still
allowing a meaningful scroll and alignment check.
