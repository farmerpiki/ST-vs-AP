# Eval report
## Config
```yaml
model: minimax
variants:
- apply_patch
- span_tools
repeats: 6
temperature: 0.0
top_p: null
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
Macro-avg savings (median per task): -42.3%
Micro-avg savings (aggregate successful pairs): 4.4%
## Success rates
- apply_patch: 90.9%
- span_tools: 92.4%
## Token breakdown (all runs)
| variant | runs | successful | billed_total | input_total | input_cached | input_uncached | output_total | output_reasoning |
|---|---|---|---|---|---|---|---|---|
| apply_patch | 66 | 60 | 3293843 | 3148278 | 2807134 | 341144 | 145565 | 0 |
| span_tools | 66 | 61 | 1874982 | 1824443 | 1598895 | 225548 | 50539 | 0 |
## Paired savings (per task)
| task | both_success | baseline_median | span_median | savings_pct | write_arg_delta | turns_delta |
|---|---|---|---|---|---|---|
| 001_set_line | 6 | 15097 | 15532 | -2.9 | -99 | -1 |
| 002_insert_guard | 5 | 12248 | 13096 | -32.3 | -38 | 0 |
| 003_delete_block | 6 | 8933 | 15504 | -73.6 | -84 | 1 |
| 004_replace_block | 6 | 10529 | 22932 | -117.8 | -98 | 2 |
| 005_move_helper | 3 | 42029 | 22983 | -213.1 | -79 | 3 |
| 006_rename_identifier | 6 | 14818 | 12541 | 15.4 | -178 | -1 |
| 007_multi_edit | 6 | 26177 | 24107 | 7.9 | -220 | -1 |
| 008_bug_investigation | 6 | 8233 | 13719 | -66.6 | -67 | 1 |
| 009_algorithms_ranges | 3 | 72038 | 72548 | 19.6 | -1982 | -3 |
| 010_matrix_perf | 6 | 12329 | 18727 | -51.9 | -146 | 0 |
| 011_headers_to_modules | 3 | 124935 | 41900 | 50.0 | -551 | -8 |
## Failure breakdown
- max_turns: 10
- build_error: 1
## Failure tags
- build_error: 11
- max_turns: 10
- oracle_missing: 4
## Largest wins / losses
- WIN  011_headers_to_modules: 50.0% (median 124935 -> 41900)
- WIN  009_algorithms_ranges: 19.6% (median 72038 -> 72548)
- WIN  006_rename_identifier: 15.4% (median 14818 -> 12541)
- LOSS 005_move_helper: -213.1% (median 42029 -> 22983)
- LOSS 004_replace_block: -117.8% (median 10529 -> 22932)
- LOSS 003_delete_block: -73.6% (median 8933 -> 15504)
