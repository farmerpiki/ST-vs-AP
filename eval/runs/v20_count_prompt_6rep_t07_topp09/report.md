# Eval report
## Config
```yaml
model: minimax
variants:
- apply_patch
- span_tools
repeats: 6
temperature: 0.7
top_p: 0.9
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
Macro-avg savings (median per task): -34.8%
Micro-avg savings (aggregate successful pairs): 31.8%
## Success rates
- apply_patch: 93.9%
- span_tools: 98.5%
## Token breakdown (all runs)
| variant | runs | successful | billed_total | input_total | input_cached | input_uncached | output_total | output_reasoning |
|---|---|---|---|---|---|---|---|---|
| apply_patch | 66 | 62 | 2458625 | 2326929 | 1986093 | 340836 | 131696 | 0 |
| span_tools | 66 | 65 | 1720970 | 1675668 | 1455086 | 220582 | 45302 | 0 |
## Paired savings (per task)
| task | both_success | baseline_median | span_median | savings_pct | write_arg_delta | turns_delta |
|---|---|---|---|---|---|---|
| 001_set_line | 6 | 9353 | 12502 | -33.7 | -46 | 0 |
| 002_insert_guard | 6 | 8775 | 14859 | -69.3 | -38 | 1 |
| 003_delete_block | 5 | 8859 | 17203 | -73.5 | -86 | 1 |
| 004_replace_block | 6 | 7851 | 27532 | -250.7 | -89 | 4 |
| 005_move_helper | 4 | 34952 | 18701 | 48.7 | -372 | -3 |
| 006_rename_identifier | 6 | 13880 | 12858 | 7.4 | -148 | 0 |
| 007_multi_edit | 6 | 22103 | 20655 | 6.5 | -231 | -1 |
| 008_bug_investigation | 6 | 8291 | 13672 | -64.9 | -67 | 1 |
| 009_algorithms_ranges | 5 | 56655 | 76278 | -24.5 | -917 | 1 |
| 010_matrix_perf | 6 | 23816 | 22297 | 6.4 | -507 | 0 |
| 011_headers_to_modules | 5 | 122700 | 40572 | 64.4 | -634 | -10 |
## Failure breakdown
- max_turns: 4
- oracle_failed: 1
## Failure tags
- max_turns: 4
- oracle_missing: 4
- build_error: 2
- oracle_forbidden: 2
## Largest wins / losses
- WIN  011_headers_to_modules: 64.4% (median 122700 -> 40572)
- WIN  005_move_helper: 48.7% (median 34952 -> 18701)
- WIN  006_rename_identifier: 7.4% (median 13880 -> 12858)
- LOSS 004_replace_block: -250.7% (median 7851 -> 27532)
- LOSS 003_delete_block: -73.5% (median 8859 -> 17203)
- LOSS 002_insert_guard: -69.3% (median 8775 -> 14859)
