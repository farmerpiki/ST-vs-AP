# Eval report
## Config
```yaml
model: minimax
variants:
- apply_patch
- span_tools
repeats: 6
temperature: 0.95
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
Macro-avg savings (median per task): -45.7%
Micro-avg savings (aggregate successful pairs): -30.1%
## Success rates
- apply_patch: 95.5%
- span_tools: 97.0%
## Token breakdown (all runs)
| variant | runs | successful | billed_total | input_total | input_cached | input_uncached | output_total | output_reasoning |
|---|---|---|---|---|---|---|---|---|
| apply_patch | 66 | 63 | 1722205 | 1651924 | 1415049 | 236875 | 70281 | 0 |
| span_tools | 66 | 64 | 2169711 | 2113199 | 1866904 | 246295 | 56512 | 0 |
## Paired savings (per task)
| task | both_success | baseline_median | span_median | savings_pct | write_arg_delta | turns_delta |
|---|---|---|---|---|---|---|
| 001_set_line | 6 | 14706 | 20387 | -38.6 | -109 | 1 |
| 002_insert_guard | 6 | 8513 | 14280 | -67.7 | -36 | 1 |
| 003_delete_block | 6 | 9067 | 17414 | -92.0 | -83 | 1 |
| 004_replace_block | 6 | 11869 | 19980 | -68.3 | -190 | 1 |
| 005_move_helper | 5 | 30034 | 47202 | -135.8 | -164 | 5 |
| 006_rename_identifier | 5 | 10498 | 12489 | -24.0 | -134 | 0 |
| 007_multi_edit | 6 | 23873 | 20236 | 15.2 | -146 | -2 |
| 008_bug_investigation | 6 | 7767 | 13993 | -80.2 | -63 | 1 |
| 009_algorithms_ranges | 4 | 45958 | 83495 | -25.4 | -1940 | 0 |
| 010_matrix_perf | 6 | 21356 | 23845 | -11.7 | -340 | 0 |
| 011_headers_to_modules | 5 | 82130 | 72536 | 26.1 | -574 | -5 |
## Failure breakdown
- max_turns: 5
## Failure tags
- max_turns: 5
- build_error: 4
- oracle_missing: 2
- oracle_forbidden: 1
## Largest wins / losses
- WIN  011_headers_to_modules: 26.1% (median 82130 -> 72536)
- WIN  007_multi_edit: 15.2% (median 23873 -> 20236)
- WIN  010_matrix_perf: -11.7% (median 21356 -> 23845)
- LOSS 005_move_helper: -135.8% (median 30034 -> 47202)
- LOSS 003_delete_block: -92.0% (median 9067 -> 17414)
- LOSS 008_bug_investigation: -80.2% (median 7767 -> 13993)
