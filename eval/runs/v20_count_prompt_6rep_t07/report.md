# Eval report
## Config
```yaml
model: minimax
variants:
- apply_patch
- span_tools
repeats: 6
temperature: 0.7
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
Macro-avg savings (median per task): -31.6%
Micro-avg savings (aggregate successful pairs): -12.5%
## Success rates
- apply_patch: 93.9%
- span_tools: 100.0%
## Token breakdown (all runs)
| variant | runs | successful | billed_total | input_total | input_cached | input_uncached | output_total | output_reasoning |
|---|---|---|---|---|---|---|---|---|
| apply_patch | 66 | 62 | 1878768 | 1800977 | 1545869 | 255108 | 77791 | 0 |
| span_tools | 66 | 66 | 1792156 | 1734943 | 1513719 | 221224 | 57213 | 0 |
## Paired savings (per task)
| task | both_success | baseline_median | span_median | savings_pct | write_arg_delta | turns_delta |
|---|---|---|---|---|---|---|
| 001_set_line | 5 | 6890 | 10969 | -35.1 | -32 | 0 |
| 002_insert_guard | 6 | 8209 | 14322 | -74.5 | -26 | 1 |
| 003_delete_block | 6 | 9059 | 15537 | -71.5 | -86 | 1 |
| 004_replace_block | 6 | 10742 | 18232 | -69.7 | -114 | 1 |
| 005_move_helper | 5 | 20095 | 41827 | -102.5 | -223 | 3 |
| 006_rename_identifier | 6 | 12690 | 12361 | 2.6 | -149 | 0 |
| 007_multi_edit | 6 | 25182 | 25365 | -0.7 | -213 | -1 |
| 008_bug_investigation | 6 | 8025 | 13683 | -70.5 | -65 | 1 |
| 009_algorithms_ranges | 6 | 59130 | 74168 | -25.4 | -492 | 1 |
| 010_matrix_perf | 6 | 27442 | 18130 | 33.9 | -616 | -2 |
| 011_headers_to_modules | 4 | 106682 | 40102 | 66.3 | -1061 | -9 |
## Failure breakdown
- max_turns: 3
- oracle_failed: 1
## Failure tags
- oracle_missing: 3
- max_turns: 3
- build_error: 2
- oracle_forbidden: 1
## Largest wins / losses
- WIN  011_headers_to_modules: 66.3% (median 106682 -> 40102)
- WIN  010_matrix_perf: 33.9% (median 27442 -> 18130)
- WIN  006_rename_identifier: 2.6% (median 12690 -> 12361)
- LOSS 005_move_helper: -102.5% (median 20095 -> 41827)
- LOSS 002_insert_guard: -74.5% (median 8209 -> 14322)
- LOSS 003_delete_block: -71.5% (median 9059 -> 15537)
