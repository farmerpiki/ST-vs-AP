"""Quick end-to-end real-API smoke test.

Runs a single MiniMax call to verify wiring (key, base URL, response
shape) without spinning up a worktree. Prints token usage broken out
into input (cached vs uncached) and output (reasoning vs other).

Usage:

    .venv/bin/python -m eval.runner.smoke_real
    .venv/bin/python -m eval.runner.smoke_real --model minimax
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .model_adapter import AdapterError, make_adapter


DEFAULT_PROMPT = "Reply with the single word 'pong' and nothing else."


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="smoke_real")
    p.add_argument("--model", default="minimax")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--max-output-tokens", type=int, default=128)
    p.add_argument("--reasoning-effort", default=None)
    args = p.parse_args(argv)

    try:
        adapter = make_adapter(args.model, reasoning_effort=args.reasoning_effort)
    except AdapterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"[smoke_real] adapter: {adapter.name}")
    print(f"[smoke_real] prompt:  {args.prompt!r}")
    try:
        resp = adapter.chat(
            messages=[{"role": "user", "content": args.prompt}],
            tools=[],
            temperature=0,
            max_output_tokens=args.max_output_tokens,
        )
    except AdapterError as e:
        print(f"error: call failed: {e}", file=sys.stderr)
        return 3

    print("[smoke_real] response text:")
    print(f"  {resp.text!r}")
    print("[smoke_real] usage:")
    print(f"  input_total           = {resp.input_tokens}")
    print(f"  input_cached          = {resp.cached_input_tokens}")
    print(f"  input_uncached        = {max(0, resp.input_tokens - resp.cached_input_tokens)}")
    print(f"  output_total          = {resp.output_tokens}")
    print(f"  output_reasoning      = {resp.reasoning_output_tokens}")
    print(f"  total                 = {resp.total_tokens}")
    print(f"  finish_reason         = {resp.finish_reason}")
    if resp.tool_calls:
        print(f"  tool_calls            = {len(resp.tool_calls)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
