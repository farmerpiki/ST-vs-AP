# Eval report
## Config
```yaml
model: minimax
variants:
- apply_patch
- span_tools
repeats: 6
temperature: 1.0
top_p: 0.95
top_k: 40
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
Macro-avg savings (median per task): -21.5%
Micro-avg savings (aggregate successful pairs): 10.8%
## Success rates
- apply_patch: 95.5%
- span_tools: 97.0%
## Token breakdown (all runs)
| variant | runs | successful | billed_total | input_total | input_cached | input_uncached | output_total | output_reasoning |
|---|---|---|---|---|---|---|---|---|
| apply_patch | 66 | 63 | 1812392 | 1743009 | 1497794 | 245215 | 69383 | 0 |
| span_tools | 66 | 64 | 1823730 | 1770457 | 1546702 | 223755 | 53273 | 0 |
## Paired savings (per task)
| task | both_success | baseline_median | span_median | savings_pct | write_arg_delta | turns_delta |
|---|---|---|---|---|---|---|
| 001_set_line | 6 | 11831 | 15774 | -33.3 | -48 | 0 |
| 002_insert_guard | 6 | 8752 | 14437 | -65.0 | -29 | 1 |
| 003_delete_block | 5 | 8774 | 15341 | -67.5 | -86 | 1 |
| 004_replace_block | 6 | 10968 | 18255 | -66.4 | -107 | 1 |
| 005_move_helper | 5 | 23911 | 21226 | 14.5 | -320 | -1 |
| 006_rename_identifier | 6 | 9584 | 12797 | -33.5 | -134 | 0 |
| 007_multi_edit | 5 | 30846 | 25157 | 20.8 | -228 | -2 |
| 008_bug_investigation | 6 | 10259 | 13646 | -33.0 | -60 | 0 |
| 009_algorithms_ranges | 4 | 51283 | 86718 | -25.7 | -1011 | 0 |
| 010_matrix_perf | 6 | 16577 | 17321 | -4.5 | -311 | 0 |
| 011_headers_to_modules | 6 | 116136 | 49566 | 57.3 | -767 | -7 |
## Failure breakdown
- max_turns: 3
- call_timeout: 2
## Failure tags
- max_turns: 3
- oracle_forbidden: 3
- oracle_missing: 2
- build_error: 2
- call_timeout: 2
## Largest wins / losses
- WIN  011_headers_to_modules: 57.3% (median 116136 -> 49566)
- WIN  007_multi_edit: 20.8% (median 30846 -> 25157)
- WIN  005_move_helper: 14.5% (median 23911 -> 21226)
- LOSS 003_delete_block: -67.5% (median 8774 -> 15341)
- LOSS 004_replace_block: -66.4% (median 10968 -> 18255)
- LOSS 002_insert_guard: -65.0% (median 8752 -> 14437)
