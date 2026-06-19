"""Calibrate the static token cost of a variant.

Sends sys-prompt + tool-schemas + literal 'hi' to the model and
records the server's input_tokens. Subtract 1 to remove the 'hi'
token. The result is the exact server-tokenized size of the
system prompt + tool schemas for that variant.

Use these numbers in the eval/results.jsonl analyser to compute
the "agentic input" each run paid for (tool calls, tool results,
assistant text, user prompt) — i.e. the model's actual reading
budget per run.

Usage:
    .venv/bin/python eval/scripts/calibrate_static.py                # both variants
    .venv/bin/python eval/scripts/calibrate_static.py apply_patch    # one variant
    .venv/bin/python eval/scripts/calibrate_static.py --out FILE     # write JSON

The script is idempotent and cheap (1 call per variant, ~2s total).
Re-run after any change to:
  - eval/prompts/common.md
  - eval/prompts/apply_patch.md / span_tools.md
  - eval/runner/tools_*.py (tool descriptions / schemas)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.runner import eval as ev_mod  # noqa: E402
from eval.runner import model_adapter as ma  # noqa: E402

# Just one task is enough — the static cost is sys-prompt + tool-schemas
# which only depends on the variant, not the task.  We use one task just
# so the tool list (with task enum literals) is well-formed.
TASKS_FOR_TOOLS = [
    "eval/tasks/001_set_line.yml",
]


def measure_variant(variant: str, adapter) -> dict:
    """Send sys+tools+'hi' to the model and report the server's
    input_tokens. The hi cost is 1 token, so the static cost is
    input_tokens - 1.
    """
    measurements = []
    for task_path in TASKS_FOR_TOOLS:
        task = ev_mod.load_task(Path(task_path))
        sys_prompt, sys_sha, sys_tok_local = ev_mod.build_system_prompt(variant)
        tools = ev_mod._tools_with_task_enums(ev_mod.VARIANT_TOOLS[variant], task)
        schema_tok_local = ev_mod.tool_schemas_tokens(tools)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "hi"},
        ]
        # Single cold call: input_tokens is the server's exact size of
        # system prompt + tool schemas + 1 token for the literal 'hi'.
        # Subtract 1 to get the static cost.
        cold = adapter.chat(
            messages=messages,
            tools=list(tools.values()),
            temperature=0,
            max_output_tokens=16,
            top_p=None,
        )
        measurements.append({
            "task": task_path,
            "prompt_sha": sys_sha,
            "local_sys_tokens": sys_tok_local,
            "local_schema_tokens": schema_tok_local,
            "local_sys_plus_schema": sys_tok_local + schema_tok_local,
            "server_input_cold": cold.input_tokens,
            "static_cold_minus_hi": cold.input_tokens - 1,
        })
    return {
        "variant": variant,
        "model": getattr(adapter, "name", "unknown"),
        "timestamp": time.time(),
        "measurements": measurements,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("variants", nargs="*", default=["apply_patch", "span_tools"])
    p.add_argument("--out", type=str, default=None,
                   help="Write JSON report to this path")
    p.add_argument("--model", default="minimax", help="Adapter spec")
    args = p.parse_args()

    if "MINIMAX_API_KEY" not in os.environ:
        print("WARNING: MINIMAX_API_KEY is not set; the calibration call will fail.",
              file=sys.stderr)

    # Parse model spec
    if args.model.startswith("minimax:"):
        model_name = args.model.split(":", 1)[1]
        adapter = ma.MiniMaxAdapter(model=model_name, reasoning_effort=None)
    elif args.model == "minimax":
        adapter = ma.MiniMaxAdapter(reasoning_effort=None)
    else:
        print(f"Unsupported model spec: {args.model}", file=sys.stderr)
        return 2

    report = {"calibrated_at": time.time(), "variants": {}}
    for v in args.variants:
        if v not in ev_mod.VARIANT_TOOLS:
            print(f"unknown variant: {v}", file=sys.stderr)
            return 2
        print(f"[calibrate] variant={v} model={args.model}")
        data = measure_variant(v, adapter)
        for m in data["measurements"]:
            print(f"  task={m['task']}")
            print(f"    local  sys+schema = {m['local_sys_plus_schema']:>5d}  "
                  f"(sys={m['local_sys_tokens']}, schema={m['local_schema_tokens']})")
            print(f"    server static     = {m['static_cold_minus_hi']:>5d}  "
                  f"(cold={m['server_input_cold']})")
        report["variants"][v] = data

    # Summary
    print("\n=== STATIC COSTS (server-tokenized, system prompt + tool schemas) ===")
    for v, d in report["variants"].items():
        colds = [m["static_cold_minus_hi"] for m in d["measurements"]]
        print(f"  {v:14s}  {colds}  (min={min(colds)}, max={max(colds)})")

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
