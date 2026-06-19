# apply_patch vs span-edit tools — eval harness

This repo is a v1 eval harness for comparing two C++ editing toolchains on
small focused tasks:

- **Variant A — `apply_patch`**: shared inspect tools + `apply_patch` as the
  only write tool.
- **Variant B — `span_tools`**: shared inspect tools + `edit_batch` (with
  `splice`, `move_span`, `replace_word`) as the only write tool.

The spec lives in `spec.md`. The eval measures success rate, token usage, and
turns, and emits a paired `report.md` per run directory.

## Layout

```
eval/
  prompts/        # system-prompt fragments per variant
  fixtures/       # tiny_cpp project used by all tasks
  tasks/          # task YAMLs (one per task)
  runner/         # tool impls + the model loop + report generator
  tests/          # unittest suite for the tools
  runs/           # output of CLI runs (config.yml, results.jsonl, report.md, ...)
```

## Quick start

```bash
.venv/bin/python -m venv .venv   # already created
.venv/bin/pip install pyyaml

# Smoke run: scripted MockModel that solves every fixture task in both
# variants. Useful for verifying the harness end-to-end.
.venv/bin/python -m eval.runner.eval \
    --model mock --smoke \
    --tasks eval/tasks/00*.yml \
    --variants apply_patch,span_tools \
    --repeats 1 \
    --out eval/runs/smoke

# Real model run (requires openai + OPENAI_API_KEY):
.venv/bin/python -m eval.runner.eval \
    --model openai:gpt-4o-mini \
    --tasks eval/tasks/00*.yml \
    --variants apply_patch,span_tools \
    --repeats 3 \
    --temperature 0 \
    --out eval/runs/$(date +%Y%m%d_%H%M%S)

# Regenerate a report from an existing run dir:
.venv/bin/python -m eval.runner.report eval/runs/smoke

# Sweep: runs both variants across all tasks at a given (temp, top_p, top_k)
# triplet.  Override via SWEEP_CONFIGS=label:temp:topp:topk; topp/topk may
# be left empty to omit the flag.
SWEEP_CONFIGS="t07_topp09:0.7:0.9:" bash eval/scripts/sweep.sh
```


## Tools

Shared inspect tools:

- `status` — dirty files; no content
- `rg` — regex search; pattern required; cwd-rel paths
- `read_range` — read lines; returns span `S#`
- `diff` — `stat` counts or `hunks` patch
- `run_check` — task check by name

Variant A write tool: `apply_patch(patch)`.
Variant B write tool: `edit_batch(ops=[...])`:

- `splice(at,text?)` — insert/delete/replace; `C=0` insert, `C>=1` replace C lines
- `move_span(from,to,...)` — move code; `to path:N` insert, `to path:N+C` replace, source deleted
- `replace_word(paths,from,to,expected?,dry_run?)` — C++ identifier rename

## Tasks

Eleven tasks. The first ten are 1–4 edits each on the `tiny_cpp` fixture;
011 is a larger multi-file conversion on its own `modules_cpp` fixture.

| id | name | type |
|---|---|---|
| 001 | set_line | splice with count=1 (single-line replace) |
| 002 | insert_guard | splice insert |
| 003 | delete_block | splice delete |
| 004 | replace_block | splice replace |
| 005 | move_helper | move_span |
| 006 | rename_identifier | replace_word |
| 007 | multi_edit | splice batch across files |
| 008 | bug_investigation | find + fix a logic bug |
| 009 | algorithms_ranges | rewrite a loop with std algorithms |
| 010 | matrix_perf | performance-oriented rewrite |
| 011 | headers_to_modules | convert headers → C++20 modules (cmake + ninja, in-place) |

## Tests

```bash
.venv/bin/python -m unittest discover -s eval/tests
```

## Per-task comparison report

`eval/runs/.report.py` aggregates one or more sweep result dirs into a
compact per-task AP vs SP table with semaphore deltas (🟢 SP win,
🔴 SP loss, 🟡 within ±threshold):

```bash
.venv/bin/python eval/runs/.report.py eval/runs/v6_t07_topp09_t07_topp09
# or all sweeps:
.venv/bin/python eval/runs/.report.py
```

Columns: pass (AP/SP, semaphore), turns, tool calls, cached, uncached
input, output. Threshold defaults to ±2.5% (`--threshold`); pass count
denominator defaults to 3 (`--repeats`).

## Files per run

A run directory contains:

- `config.yml` — exact CLI config used
- `results.jsonl` — one JSON per (task, variant, repeat)
- `results.csv` — flat per-run table
- `report.md` — paired savings + success rates
- `report.csv` — per-task table
- `transcripts/<task>__<variant>__<r>.jsonl` — full transcript
- `diffs/<task>__<variant>__<r>.diff` — final repo diff
- `worktrees/<task>__<variant>__<r>/` — final fixture state (kept by default)

## Model adapters

The runner ships with four adapters:

- `mock` — scripted responses; no real API
- `echo` — echoes back the last user message (smoke test)
- `openai[:MODEL]` (alias `openai_chat[:MODEL]`) — OpenAI Chat Completions
  (requires `openai` package and `OPENAI_API_KEY`)
- `minimax[:MODEL][@URL]` — MiniMax via the Responses API
  (requires `MINIMAX_API_KEY`; default base URL `https://api.minimax.io/v1`,
  default model `MiniMax-M3`)

`mock` plus `--smoke` runs scripted responses that solve each of the seven
edit-primitive tasks (001-007) in both variants; the open-ended tasks
(008-010) have no canonical script and are skipped. This is the recommended
harness self-test.

## Token accounting

Both adapters report token usage in the same shape. The metrics track
**input / cached input / uncached input / output / reasoning output** as
separate fields so the report can call out cache hits, which dominate
on multi-turn runs.

| field | source |
|---|---|
| `total_input_tokens` | provider-reported input tokens (incl. cached) |
| `total_input_cached_tokens` | provider-reported cached portion of input |
| `total_input_uncached_tokens` | `input - cached` (what the provider actually re-reads) |
| `total_output_tokens` | provider-reported output tokens |
| `total_output_reasoning_tokens` | provider-reported reasoning tokens (subset of output) |
| `total_billed_tokens` | `input + output` (the spec metric) |
| `peak_input_tokens` | largest single-turn local input estimate |
| `write_tool_arg_tokens` | tokens spent in write-tool arguments (apply_patch / edit_batch) |

For MiniMax's Responses API the `cached_input_tokens` and
`reasoning_output_tokens` are populated from `input_tokens_details`
and `output_tokens_details` respectively. Chat Completions typically
reports 0 for these (provider-dependent).

## Quick real-API check

```bash
# Single-turn smoke against MiniMax (no worktree, no fixture):
.venv/bin/python -m eval.runner.smoke_real --model minimax
```

```bash
# Single-turn smoke against OpenAI:
.venv/bin/python -m eval.runner.smoke_real --model openai:gpt-4o-mini
```

