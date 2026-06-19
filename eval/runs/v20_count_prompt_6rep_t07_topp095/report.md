# Eval report
## Config
```yaml
model: minimax
variants:
- apply_patch
- span_tools
repeats: 6
temperature: 0.7
top_p: 0.95
top_k: null
max_turns: null
max_tool_calls: null
keep_worktrees: false
strict_gold_diff: false
seed: 0
tasks:
- eval/tasks/001_set_line.yml
- eval/tasks/002_insert_guard.yml
- eval/tasks/003_delete_block.yml
- eval/tasks/004_replace_block.yml
- eval/tasks/005_move_helper.yml
- eval/tasks/006_rename_identifier.yml
- eval/tasks/007_multi_edit.yml
- eval/tasks/008_bug_investigation.yml
- eval/tasks/009_algorithms_ranges.yml
- eval/tasks/010_matrix_perf.yml
- eval/tasks/011_headers_to_modules.yml
```
Runs: 132 across 11 task(s) and 2 variant(s).
Macro-avg savings (median per task): -37.6%
Micro-avg savings (aggregate successful pairs): -7.9%
## Success rates
- apply_patch: 95.5%
- span_tools: 98.5%
## Token breakdown (all runs)
| variant | runs | successful | billed_total | input_total | input_cached | input_uncached | output_total | output_reasoning |
|---|---|---|---|---|---|---|---|---|
| apply_patch | 66 | 63 | 1580857 | 1514984 | 1257306 | 257678 | 65873 | 0 |
| span_tools | 66 | 65 | 1679225 | 1631584 | 1434657 | 196927 | 47641 | 0 |
## Paired savings (per task)
| task | both_success | baseline_median | span_median | savings_pct | write_arg_delta | turns_delta |
|---|---|---|---|---|---|---|
| 001_set_line | 6 | 12226 | 11272 | 7.8 | -74 | -1 |
| 002_insert_guard | 6 | 8639 | 11300 | -30.8 | -42 | 0 |
| 003_delete_block | 6 | 9008 | 15108 | -67.7 | -86 | 1 |
| 004_replace_block | 6 | 9830 | 22240 | -126.3 | -112 | 2 |
| 005_move_helper | 3 | 39619 | 33877 | -43.7 | -54 | 0 |
| 006_rename_identifier | 6 | 9648 | 12798 | -32.6 | -133 | 0 |
| 007_multi_edit | 6 | 22428 | 26390 | -17.7 | -184 | 0 |
| 008_bug_investigation | 6 | 8229 | 13699 | -66.5 | -67 | 1 |
| 009_algorithms_ranges | 5 | 54768 | 78205 | -51.7 | -396 | 4 |
| 010_matrix_perf | 6 | 14558 | 20312 | -39.5 | -254 | 0 |
| 011_headers_to_modules | 6 | 53241 | 23645 | 55.6 | -493 | -4 |
## Failure breakdown
- max_turns: 4
## Failure tags
- max_turns: 4
- build_error: 3
- oracle_missing: 1
- oracle_forbidden: 1
## Largest wins / losses
- WIN  011_headers_to_modules: 55.6% (median 53241 -> 23645)
- WIN  001_set_line: 7.8% (median 12226 -> 11272)
- WIN  007_multi_edit: -17.7% (median 22428 -> 26390)
- LOSS 004_replace_block: -126.3% (median 9830 -> 22240)
- LOSS 003_delete_block: -67.7% (median 9008 -> 15108)
- LOSS 008_bug_investigation: -66.5% (median 8229 -> 13699)
