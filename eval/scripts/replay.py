#!/usr/bin/env python3
"""Cheap log-replay harness for prompt / steering experiments.

Reconstruct the chat history from a recorded transcript up to a chosen turn,
optionally mutate it (edit/append the system prompt, append a trailing steering
message), then resample the model's decision for that turn N times -- WITHOUT
executing any tools. You see the tool calls the model *would* make.

Typical questions it answers cheaply:
  * "Can we elide a re-check?" -- point --turn at the re-check turn and resample
    N times: how often does the model re-read vs just proceed?
  * "Does a steering hint produce a better next tool call?" -- point --turn at
    the turn *after* a mistake (its real prior tool result is reconstructed),
    add --steer "<hint>", and resample: does it recover?
  * "Does a system-prompt tweak still make the same mistake?" -- --system-append
    / --system-file and resample.
  * "Do different temperature/top_p/top_k fix it?" -- --grid "0:: 0.7:0.9:
    1.0:0.95:40" resamples each setting. Sampling params don't change the
    prompt, so every setting reuses the cached prefix -- the cheapest sweep.

Caching (keep it cheap): the message PREFIX is byte-identical across the N
samples, so the provider's prompt cache hits and only the first sample pays full
input price. Prefer trailing variation (--steer) over editing the system prefix
(--system-*), which busts the cached prefix. Per-sample cached/uncached/output
tokens and an estimated cost are printed so you can see the cache working.

(Transcripts also log a per-turn response_id; the Responses API can continue
from it via previous_response_id, but only if the original run stored the
response server-side. Old runs did not, so this script relies on prefix
caching, which always works.)

Usage:
  eval/scripts/replay.py <transcript.jsonl> [--turn K] [--n N] [--steer TEXT]
      [--system-append TEXT | --system-file FILE]
      [--model M] [--temp T] [--top-p P] [--top-k K] [--max-tokens N]
      [--tasks-dir DIR] [--variant V] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.runner.eval import (  # noqa: E402
    VARIANT_TOOLS,
    _assistant_message,
    _tool_result,
    _tools_with_task_enums,
    build_system_prompt,
    load_task,
)
from eval.runner.model_adapter import ToolCall, make_adapter  # noqa: E402

# USD per 1M tokens -> _cost returns microUSD (matches eval/runs/.report.py).
COST_UNCACHED, COST_CACHED, COST_OUTPUT = 0.30, 0.06, 1.20


def _ucost(uncached: float, cached: float, output: float) -> float:
    return uncached * COST_UNCACHED + cached * COST_CACHED + output * COST_OUTPUT


def _load_transcript(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _run_config(transcript_path: Path) -> dict:
    """Best-effort read of the sibling run config.yml (model + sampling)."""
    cfg_path = transcript_path.parent.parent / "config.yml"
    if not cfg_path.is_file():
        return {}
    try:
        import yaml

        return yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return {}


def reconstruct(events: list[dict], task, variant: str, system: str, target_turn: int) -> list[dict]:
    """Rebuild the chat messages as they stood just before `target_turn`.

    System prompt is supplied by the caller (so it can be mutated). Assistant
    turns and their tool results come from the recorded 'turn'/'tool' events;
    tool results are paired to tool_calls in order (the event log omits the
    call id on the tool side). Missing results (e.g. forbidden-call turns that
    log no 'tool' event) are back-filled so the message structure stays valid.
    """
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task.user_prompt},
    ]
    pending: list[ToolCall] = []  # tool_calls awaiting their result events

    def flush_pending_placeholders() -> None:
        # Any tool_calls with no recorded result get a stub so the assistant
        # tool-call message is followed by a result for every call.
        for tc in pending:
            messages.append(_tool_result(tc.id or "x", {"ok": False, "error": "result_not_logged"}))
        pending.clear()

    for ev in events:
        kind = ev.get("event")
        if kind == "turn":
            if ev.get("turn", 0) >= target_turn:
                break
            flush_pending_placeholders()
            tcs = [
                ToolCall(name=t["name"], args=t.get("args") or {}, id=t.get("id"))
                for t in ev.get("tool_calls") or []
            ]
            messages.append(_assistant_message(ev.get("text") or "", tcs))
            pending = list(tcs)
        elif kind == "tool":
            if pending:
                tc = pending.pop(0)
                messages.append(_tool_result(tc.id or "x", ev.get("result") or {"ok": ev.get("result_ok")}))
            # tool events with no pending call (shouldn't happen) are ignored
    flush_pending_placeholders()
    return messages


def _fmt_calls(tcs: list[ToolCall]) -> str:
    if not tcs:
        return "(no tool call -> final answer)"
    parts = []
    for tc in tcs:
        a = tc.args or {}
        if tc.name == "edit_batch":
            ops = "; ".join(f"{o.get('op')} {o.get('at') or o.get('from','')}" for o in a.get("ops", []))
            parts.append(f"edit_batch[{ops}]" + (" +verify" if a.get("verify") else ""))
        elif tc.name == "apply_patch":
            parts.append("apply_patch" + (" +verify" if a.get("verify") else ""))
        elif tc.name in ("read_range", "rg"):
            loc = a.get("path") or a.get("pattern", "")
            if a.get("start"):
                loc = f"{loc}:{a.get('start')}+{a.get('count')}"
            parts.append(f"{tc.name}({loc})")
        elif tc.name == "run_check":
            parts.append(f"run_check({a.get('name','')})")
        else:
            parts.append(tc.name)
    return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--turn", type=int, default=None, help="resample this turn (default: the last recorded turn)")
    ap.add_argument("--n", type=int, default=5, help="samples to draw (default 5; share a cached prefix)")
    ap.add_argument("--steer", default=None, help="trailing user message injected before the resampled turn (cache-preserving)")
    ap.add_argument("--system-file", type=Path, default=None, help="replace the whole system prompt (busts cache)")
    ap.add_argument("--system-append", default=None, help="append text to the system prompt (busts cache)")
    ap.add_argument("--variant", default=None, help="override variant (default: from transcript init)")
    ap.add_argument("--tasks-dir", type=Path, default=ROOT / "eval" / "tasks")
    ap.add_argument("--model", default=None, help="override model (default: run config.yml)")
    ap.add_argument("--temp", type=float, default=None)
    ap.add_argument("--top-p", type=float, default=None)
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--grid", default=None,
                    help='compare several sampling settings, e.g. "0:: 0.7:0.9: 1.0:0.95:40" '
                         '(temp:top_p:top_k, empty=unset); all reuse the cached prefix')
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--dry-run", action="store_true", help="reconstruct + print messages and token estimate; no model call")
    args = ap.parse_args(argv)

    events = _load_transcript(args.transcript)
    init = next((e for e in events if e.get("event") == "init"), {})
    variant = args.variant or init.get("variant")
    task_id = init.get("task")
    if not variant or not task_id:
        print("transcript missing init.variant/task", file=sys.stderr)
        return 2

    task = load_task(args.tasks_dir / f"{task_id}.yml")

    turns = [e.get("turn", 0) for e in events if e.get("event") == "turn"]
    target_turn = args.turn if args.turn is not None else (max(turns) if turns else 0)

    # System prompt (the mutation surface).
    if args.system_file:
        system = args.system_file.read_text()
    else:
        system = build_system_prompt(variant)[0]
        if args.system_append:
            system = system + "\n" + args.system_append
    if args.system_file or args.system_append:
        print("[note] system prompt edited -> the cached prefix is invalidated; "
              "prefer --steer for cheap probing.", file=sys.stderr)

    messages = reconstruct(events, task, variant, system, target_turn)
    if args.steer:
        messages.append({"role": "user", "content": args.steer})

    tools = _tools_with_task_enums(VARIANT_TOOLS[variant], task)

    print(f"task={task_id} variant={variant} resample turn={target_turn} "
          f"history_msgs={len(messages)} steer={'yes' if args.steer else 'no'}")

    if args.dry_run:
        from eval.runner import token_count
        print(f"[dry-run] prefix ~{token_count.count_messages(messages)} tokens; "
              f"last 3 messages:")
        for m in messages[-3:]:
            c = m.get("content")
            c = (c[:160] + "...") if isinstance(c, str) and len(c) > 160 else c
            print(f"  - {m['role']}: {c}  {('tool_calls=' + str(len(m['tool_calls']))) if m.get('tool_calls') else ''}")
        return 0

    cfg = _run_config(args.transcript)
    model_name = args.model or cfg.get("model") or "minimax"
    model = make_adapter(model_name, reasoning_effort=args.reasoning_effort)

    # Sampling settings to compare. --grid "t:p:k t:p:k ..." sweeps several;
    # otherwise a single setting from --temp/--top-p/--top-k (falling back to
    # the run's config). Sampling params do NOT change the prompt, so EVERY
    # setting reuses the same cached prefix -- a sampling sweep is the cheapest
    # variation there is.
    if args.grid:
        settings = _parse_grid(args.grid)
    else:
        settings = [(
            args.temp if args.temp is not None else cfg.get("temperature"),
            args.top_p if args.top_p is not None else cfg.get("top_p"),
            args.top_k if args.top_k is not None else cfg.get("top_k"),
        )]

    print(f"model={model_name}  {args.n} sample(s) x {len(settings)} setting(s)  "
          f"(shared cached prefix)\n")

    from collections import Counter
    g_unc = g_cac = g_out = 0
    for (temp, top_p, top_k) in settings:
        first_tool: Counter = Counter()
        s_unc = s_cac = s_out = 0
        print(f"=== temp={temp} top_p={top_p} top_k={top_k} ===")
        for i in range(args.n):
            # Pass the SAME `messages` each time -> identical prefix -> the
            # provider prompt cache hits after the first call (across settings
            # too, since only sampling differs).
            r = model.chat(
                messages=messages,
                tools=list(tools.values()),
                temperature=temp,
                max_output_tokens=args.max_tokens,
                top_p=top_p,
                top_k=top_k,
            )
            unc = max(0, r.input_tokens - r.cached_input_tokens)
            s_unc += unc
            s_cac += r.cached_input_tokens
            s_out += r.output_tokens
            first_tool[r.tool_calls[0].name if r.tool_calls else "(final)"] += 1
            print(f"  [{i+1}/{args.n}] {_fmt_calls(r.tool_calls)}")
            print(f"        in={r.input_tokens} (cached={r.cached_input_tokens}/uncached={unc}) out={r.output_tokens}")
            if r.text and r.text.strip():
                print(f"        text: {r.text.strip()[:200]}")
        print(f"  -> first-tool: {dict(first_tool)}  (uncached={s_unc} cached={s_cac} out={s_out})\n")
        g_unc += s_unc
        g_cac += s_cac
        g_out += s_out

    cost = _ucost(g_unc, g_cac, g_out)
    hit = g_cac / (g_cac + g_unc) * 100 if (g_cac + g_unc) else 0
    print(f"grand totals: uncached={g_unc} cached={g_cac} output={g_out}  "
          f"~{int(round(cost)):,} µ$  (cache hit rate {hit:.0f}% of input)")
    return 0


def _parse_grid(spec: str) -> list[tuple]:
    """Parse "t:p:k t:p:k ..." into [(temp, top_p, top_k), ...]; empty = None."""
    out = []
    for tok in spec.split():
        parts = (tok.split(":") + ["", "", ""])[:3]
        t = float(parts[0]) if parts[0] != "" else None
        p = float(parts[1]) if parts[1] != "" else None
        k = int(parts[2]) if parts[2] != "" else None
        out.append((t, p, k))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
