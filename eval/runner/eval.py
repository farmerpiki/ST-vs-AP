"""Main eval runner: model + tools + worktree per (task, variant, repeat)."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import token_count
from .metrics import RunMetrics
from .model_adapter import (
    AdapterError,
    ModelResponse,
    ToolCall,
    make_adapter,
)
from .oracle import run_checks, verify_oracle
from .state import ToolState
from .tools_shared import SHARED_TOOL_DISPATCH, SHARED_TOOLS
from .tools_apply_patch import APPLY_PATCH_TOOL, tool_apply_patch
from .tools_span import EDIT_BATCH_TOOL, tool_edit_batch
from .util import copy_fixture, diff_stat, diff_text, git_init_and_commit


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # /home/claudiu/eval


VARIANT_TOOLS = {
    "apply_patch": {**SHARED_TOOLS, "apply_patch": APPLY_PATCH_TOOL},
    "span_tools": {**SHARED_TOOLS, "edit_batch": EDIT_BATCH_TOOL},
}
VARIANT_DISPATCH = {
    "apply_patch": {
        **SHARED_TOOL_DISPATCH,
        "apply_patch": tool_apply_patch,
    },
    "span_tools": {
        **SHARED_TOOL_DISPATCH,
        "edit_batch": tool_edit_batch,
    },
}
VARIANT_PROMPTS = {
    "apply_patch": ROOT / "eval" / "prompts" / "apply_patch.md",
    "span_tools": ROOT / "eval" / "prompts" / "span_tools.md",
}
COMMON_PROMPT = ROOT / "eval" / "prompts" / "common.md"


@dataclass
class Task:
    id: str
    fixture: str
    user_prompt: str
    allowed_paths: list[str]
    checks: list[dict]
    limits: dict
    oracle: dict
    raw: dict = field(default_factory=dict)
    path: Path = field(default_factory=Path)

    @classmethod
    def from_yaml(cls, p: Path) -> "Task":
        d = yaml.safe_load(p.read_text())
        return cls(
            id=d["id"],
            fixture=d["fixture"],
            user_prompt=d.get("user_prompt", "").strip(),
            allowed_paths=d.get("allowed_paths", []) or [],
            checks=d.get("checks", []) or [],
            limits=d.get("limits", {}) or {},
            oracle=d.get("oracle", {}) or {},
            raw=d,
            path=p,
        )


def load_task(path: Path) -> Task:
    return Task.from_yaml(path)




def _tools_with_task_enums(tools: dict, task) -> dict:
    """Return a copy of `tools` with task-specific enums injected.

    Currently injects the list of allowed run_check names so the model
    can read the valid values directly from the JSON schema.
    """
    import copy
    out = copy.deepcopy(tools)
    rc = out.get("run_check")
    if rc is not None and task.checks:
        names = [c.get("name") for c in task.checks if c.get("name")]
        if names:
            rc_desc = rc.get("description", "")
            rc["description"] = rc_desc + f" allowed={','.join(names)}."
            props = rc.setdefault("input_schema", {}).setdefault("properties", {})
            if "name" in props:
                props["name"]["enum"] = names
    return out


def build_system_prompt(variant: str) -> tuple[str, str, int]:
    common = COMMON_PROMPT.read_text()
    extra = VARIANT_PROMPTS[variant].read_text()
    full = common + "\n\n" + extra
    return full, hashlib.sha256(full.encode()).hexdigest(), token_count.count_tokens(full)


def tool_schemas_tokens(tools: dict) -> int:
    # Approximate cost of the tool schema as a single big JSON string.
    blob = json.dumps(list(tools.values()))
    return token_count.count_tokens(blob)


def dispatch_tool(
    name: str, args: dict, state: ToolState, variant: str
) -> dict:
    table = VARIANT_DISPATCH[variant]
    if name not in table:
        return {"ok": False, "error": "unknown_tool", "name": name}
    try:
        return table[name](args, state)
    except _ForbiddenCall as e:  # type: ignore[name-defined]
        return {"ok": False, "error": "forbidden_in_variant", "name": name, "details": str(e)}
    except Exception as e:  # pragma: no cover - tool bugs
        return {"ok": False, "error": "tool_exception", "name": name, "details": str(e)}


class _ForbiddenCall(Exception):
    pass


def _enforce_variant_writes(name: str, variant: str) -> None:
    write_tools = {
        "apply_patch": "apply_patch",
        "span_tools": "edit_batch",
    }
    allowed = write_tools[variant]
    if name in {"apply_patch", "edit_batch"} and name != allowed:
        raise _ForbiddenCall(f"{name} not allowed in variant {variant}")


def _assistant_message(
    text: str, tool_calls: list[ToolCall]
) -> dict:
    # Some providers (e.g. diffusiongemma) reject empty content on
    # assistant messages. Use None (or omit) instead of "" when there
    # are no text tokens, and only include "content" if we actually
    # have something to say or there are tool calls (which the SDK
    # still pairs with the role).
    has_text = bool(text and text.strip())
    msg: dict[str, Any] = {"role": "assistant"}
    if has_text:
        msg["content"] = text
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id or f"call_{i}",
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.args),
                },
            }
            for i, tc in enumerate(tool_calls)
        ]
        # If a tool-calling assistant message has no text, include an
        # empty content=None so the role is still valid for providers
        # that require the field.
        if not has_text:
            msg["content"] = None
    return msg


def _tool_result(call_id: str, result: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(result),
    }


def run_one(
    *,
    task: Task,
    variant: str,
    repeat: int,
    model,
    temperature: Optional[float],
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    max_tokens: Optional[int],
    worktree_root: Path,
    keep_worktrees: bool,
    transcripts_dir: Path,
    diffs_dir: Path,
    out_metrics: dict,
) -> dict:
    """Execute a single (task, variant, repeat) run."""
    worktree = worktree_root / f"{task.id}__{variant}__r{repeat}"
    if worktree.exists():
        shutil.rmtree(worktree)
    fixture_src = ROOT / "eval" / "fixtures" / task.fixture / "base"
    copy_fixture(fixture_src, worktree)
    fixture_sha = _dir_hash(fixture_src)
    commit_sha = git_init_and_commit(worktree)

    state = ToolState(
        worktree=worktree,
        allowed_paths=task.allowed_paths,
        checks=task.checks,
    )
    sys_prompt, sys_sha, sys_tokens = build_system_prompt(variant)
    tools = _tools_with_task_enums(VARIANT_TOOLS[variant], task)
    schema_tokens = tool_schemas_tokens(tools)

    metrics = RunMetrics()
    metrics.system_prompt_tokens = sys_tokens
    metrics.tool_schema_tokens = schema_tokens
    metrics.user_prompt_tokens = token_count.count_tokens(task.user_prompt)

    messages: list[dict] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": task.user_prompt},
    ]

    limits = task.limits or {}
    max_turns = int(limits.get("max_turns", 12))
    max_tool_calls = int(limits.get("max_tool_calls", 40))

    failure_reason: Optional[str] = None
    done = False
    transcript: list[dict] = []
    transcript.append({"event": "init", "variant": variant, "task": task.id, "repeat": repeat, "commit": commit_sha})
    transcript.append({"event": "system", "tokens": sys_tokens, "sha": sys_sha})

    # Per-call wall-clock timeout (seconds). Reasoning models can take
    # 10+ minutes for a single turn; we cut them off after this so a
    # stuck/hanging model doesn't block the whole sweep. The adapter
    # itself uses 600s; this wrapper is a fail-safe on top.
    # Note: we do NOT wait for the underlying thread to finish on
    # timeout -- the request is fire-and-forget, and we move on.
    per_call_timeout = 600
    for turn in range(max_turns):
        if done:
            break
        try:
            local_input = _estimate_input_tokens(messages)
            import concurrent.futures
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(
                model.chat,
                messages=messages,
                tools=list(tools.values()),
                temperature=temperature,
                max_output_tokens=max_tokens,
                top_p=top_p,
                top_k=top_k,
            )
            try:
                response = fut.result(timeout=per_call_timeout)
            except concurrent.futures.TimeoutError:
                failure_reason = "call_timeout"
                transcript.append({
                    "event": "error",
                    "error": f"model.chat() did not return within {per_call_timeout}s",
                })
                # Detach: the underlying thread keeps running but we
                # don't block on it. The process may keep the
                # connection open until the API eventually responds,
                # but we've already moved on.
                ex.shutdown(wait=False)
                break
            ex.shutdown(wait=True)
        except AdapterError as e:
            failure_reason = "adapter_error"
            transcript.append({"event": "error", "error": str(e)})
            break
        except Exception as e:
            failure_reason = "tool_exception"
            transcript.append({"event": "error", "error": str(e), "trace": traceback.format_exc()})
            break

        metrics.record_turn(
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_input_tokens=response.cached_input_tokens,
            reasoning_output_tokens=response.reasoning_output_tokens,
            local_input_tokens=local_input,
        )
        transcript.append(
            {
                "event": "turn",
                "turn": turn,
                "text": response.text,
                "response_id": getattr(response, "response_id", "") or "",
                "tool_calls": [
                    {"name": tc.name, "args": tc.args, "id": tc.id}
                    for tc in response.tool_calls
                ],
                "usage": {
                    "input_tokens": response.input_tokens,
                    "cached_input_tokens": response.cached_input_tokens,
                    "input_uncached_tokens": max(
                        0, response.input_tokens - response.cached_input_tokens
                    ),
                    "output_tokens": response.output_tokens,
                    "reasoning_output_tokens": response.reasoning_output_tokens,
                    "total_tokens": response.total_tokens,
                    "local_input_tokens": local_input,
                },
            }
        )
        if response.text:
            metrics.record_assistant_text(response.text)

        messages.append(_assistant_message(response.text, response.tool_calls))

        if not response.tool_calls:
            done = True
            break

        for tc in response.tool_calls:
            # Enforce variant write-tool policy.
            try:
                _enforce_variant_writes(tc.name, variant)
            except _ForbiddenCall as e:
                result = {"ok": False, "error": "forbidden_in_variant", "details": str(e)}
                metrics.record_tool_call(
                    name=tc.name,
                    args_tokens=token_count.count_tokens(json.dumps(tc.args)),
                    output_tokens=token_count.count_tokens(json.dumps(result)),
                )
                messages.append(_tool_result(tc.id or "x", result))
                continue
            args_tokens = token_count.count_tokens(json.dumps(tc.args))
            result = dispatch_tool(tc.name, tc.args, state, variant)
            output_tokens = token_count.count_tokens(json.dumps(result))
            metrics.record_tool_call(
                name=tc.name, args_tokens=args_tokens, output_tokens=output_tokens
            )
            messages.append(_tool_result(tc.id or "x", result))
            transcript.append(
                {
                    "event": "tool",
                    "name": tc.name,
                    "args": tc.args,
                    "result_ok": result.get("ok"),
                    "result": _truncate(result, 4000),
                }
            )
            if metrics.tool_calls > max_tool_calls:
                failure_reason = "max_tool_calls"
                done = True
                break
        if metrics.tool_calls > max_tool_calls:
            break
    else:
        if not done and failure_reason is None:
            failure_reason = "max_turns"

    if failure_reason is None and not done:
        failure_reason = "model_no_final"

    # Verify
    checks_passed, check_results = run_checks(worktree, task.checks)
    oracle_result = verify_oracle(worktree, task.oracle, task.allowed_paths)
    oracle_result["checks_passed"] = checks_passed

    success = (
        checks_passed
        and oracle_result["allowed_paths_ok"]
        and oracle_result["required_contains_ok"]
        and oracle_result["forbidden_contains_ok"]
    )
    failure_tags = _failure_tags(
        success=success,
        loop_failure=failure_reason,
        checks_passed=checks_passed,
        check_results=check_results,
        oracle_result=oracle_result,
    )
    if not success and failure_reason is None:
        # Pick a descriptive failure reason based on the oracle/check.
        if not checks_passed:
            failure_reason = _check_failure_tag(check_results)
        elif not oracle_result["allowed_paths_ok"]:
            failure_reason = "forbidden_path"
        elif not oracle_result["required_contains_ok"]:
            failure_reason = "oracle_failed"
        elif not oracle_result["forbidden_contains_ok"]:
            failure_reason = "oracle_failed"
        else:
            failure_reason = "unknown"

    # Diff and changed files.
    changed, stats = diff_stat(worktree)
    final_diff = diff_text(worktree, paths=changed or None)
    diff_path = diffs_dir / f"{task.id}__{variant}__r{repeat}.diff"
    diff_path.write_text(final_diff, encoding="utf-8")

    # Save transcript.
    tr_path = transcripts_dir / f"{task.id}__{variant}__r{repeat}.jsonl"
    with tr_path.open("w") as f:
        for entry in transcript:
            f.write(json.dumps(entry) + "\n")

    result = {
        "run_id": f"{int(time.time())}_{task.id}/{variant}/{repeat}",
        "repeat": repeat,
        "task_id": task.id,
        "variant": variant,
        "model": getattr(model, "name", "unknown"),
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "success": success,
        "failure_reason": failure_reason if not success else None,
        "failure_tags": failure_tags if not success else [],
        "turns": metrics.turns,
        "tool_calls": metrics.tool_calls,
        "total_billed_tokens": metrics.total_billed_tokens,
        "total_input_tokens": metrics.total_input_tokens,
        "total_input_cached_tokens": metrics.total_input_cached_tokens,
        "total_input_uncached_tokens": metrics.total_input_uncached_tokens,
        "total_output_tokens": metrics.total_output_tokens,
        "total_output_reasoning_tokens": metrics.total_output_reasoning_tokens,
        "peak_input_tokens": metrics.peak_input_tokens,
        "system_prompt_tokens": metrics.system_prompt_tokens,
        "tool_schema_tokens": metrics.tool_schema_tokens,
        "assistant_tool_arg_tokens": metrics.assistant_tool_arg_tokens,
        "write_tool_arg_tokens": metrics.write_tool_arg_tokens,
        "tool_output_tokens": metrics.tool_output_tokens,
        "checks_passed": checks_passed,
        "check_results": [
            {
                "name": r["name"],
                "ok": r["ok"],
                "exit_code": r["exit_code"],
                "verdict": r.get("verdict"),
                "summary": r.get("summary", {}),
            }
            for r in check_results
        ],
        "allowed_paths_ok": oracle_result["allowed_paths_ok"],
        "out_of_scope": oracle_result["out_of_scope"],
        "required_contains_ok": oracle_result["required_contains_ok"],
        "missing_required": oracle_result["missing_required"],
        "forbidden_contains_ok": oracle_result["forbidden_contains_ok"],
        "present_forbidden": oracle_result["present_forbidden"],
        "changed_files": changed,
        "diff_stat": {p: {"added": a, "deleted": d} for p, (a, d) in stats.items()},
        "diff_path": str(diff_path.relative_to(ROOT)) if diff_path.is_relative_to(ROOT) else str(diff_path),
        "transcript_path": str(tr_path.relative_to(ROOT)) if tr_path.is_relative_to(ROOT) else str(tr_path),
        "worktree": str(worktree.relative_to(ROOT)) if worktree.is_relative_to(ROOT) else str(worktree),
        "commit_sha": commit_sha,
        "fixture_sha256": fixture_sha,
        "prompt_sha256": sys_sha,
        "metrics": metrics.to_dict(),
    }

    out_metrics.update(metrics.to_dict())

    if not keep_worktrees:
        # leave in place so the user can inspect if they want; do not
        # aggressively delete because debugging is more important.
        pass

    return result


def _check_failure_tag(check_results: list[dict]) -> str:
    """Map a failing check's verdict to a precise failure tag.

    ``summarize_check_output`` already distinguishes "the code didn't
    compile" (build_error) from "it compiled but a test/assertion
    failed" (tests_failed); surface that instead of collapsing every
    non-passing check into "tests_failed" (which made build failures
    indistinguishable from real test failures in the aggregate report).
    """
    verdicts = [r.get("verdict") for r in check_results if not r.get("ok", True)]
    if "build_error" in verdicts:
        return "build_error"
    if "timeout" in verdicts:
        return "check_timeout"
    if "tests_failed" in verdicts:
        return "tests_failed"
    if verdicts:
        return "checks_failed"
    return "tests_failed"


def _failure_tags(
    *,
    success: bool,
    loop_failure: Optional[str],
    checks_passed: bool,
    check_results: list[dict],
    oracle_result: dict,
) -> list[str]:
    if success:
        return []
    tags: list[str] = []
    if loop_failure:
        tags.append(loop_failure)
    if not checks_passed:
        tags.append(_check_failure_tag(check_results))
    if not oracle_result.get("allowed_paths_ok", True):
        tags.append("forbidden_path")
    if not oracle_result.get("required_contains_ok", True):
        tags.append("oracle_missing")
    if not oracle_result.get("forbidden_contains_ok", True):
        tags.append("oracle_forbidden")
    out: list[str] = []
    for tag in tags or ["unknown"]:
        if tag not in out:
            out.append(tag)
    return out


def _estimate_input_tokens(messages: list[dict]) -> int:
    """Local estimate of input tokens for the next call."""
    return token_count.count_messages(messages)


def _truncate(obj: Any, max_chars: int) -> Any:
    s = json.dumps(obj)
    if len(s) <= max_chars:
        return obj
    return {"_truncated": True, "preview": s[:max_chars]}


def _dir_hash(p: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p.rglob("*")):
        if f.is_file() and ".git" not in f.parts and "build" not in f.parts:
            h.update(f.relative_to(p).as_posix().encode())
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def run_plan(tasks: list[Task], variants: list[str], repeats: int) -> list[tuple[Task, str, int]]:
    """Return the eval execution order.

    Repeats are refinement passes: run every task/variant once before starting
    the next repeat, so partial sweep results form a usable baseline quickly.
    """
    return [
        (task, variant, repeat)
        for repeat in range(repeats)
        for task in tasks
        for variant in variants
    ]


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval")
    p.add_argument("--model", required=True, help="Model spec. Supported: mock, echo, openai[:MODEL], openai_chat[:MODEL], minimax[:MODEL][@URL]")
    p.add_argument("--variants", default="apply_patch,span_tools")
    p.add_argument("--tasks", nargs="+", required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument(
        "--temperature", type=float, default=None,
        help="Sampling temperature. If omitted, the model's adapter default is used.",
    )
    p.add_argument(
        "--top-p", type=float, default=None,
        help="Nucleus sampling top_p. If omitted, the model's adapter default is used.",
    )
    p.add_argument(
        "--top-k", type=int, default=None,
        help="Top-K sampling. If omitted, the model's adapter default is used.",
    )
    p.add_argument(
        "--max-tokens", type=int, default=None,
        help="Per-request max_tokens. If omitted, the model's adapter default is used.",
    )
    p.add_argument("--max-turns", type=int, default=None)
    p.add_argument("--max-tool-calls", type=int, default=None)
    p.add_argument("--keep-worktrees", action="store_true")
    p.add_argument("--strict-gold-diff", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", required=True)
    p.add_argument("--smoke", action="store_true", help="Run scripted mock smoke test")
    p.add_argument("--reasoning-effort", default=None, help="Reasoning effort for minimax/openai (e.g. low, medium, high)")
    args = p.parse_args(argv)

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in variants:
        if v not in VARIANT_TOOLS:
            p.error(f"unknown variant: {v}")

    out_dir = Path(args.out).resolve()
    if out_dir.exists():
        p.error(f"out directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    (out_dir / "transcripts").mkdir()
    (out_dir / "diffs").mkdir()
    (out_dir / "worktrees").mkdir()

    config = {
        "model": args.model,
        "variants": variants,
        "repeats": args.repeats,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_turns": args.max_turns,
        "max_tool_calls": args.max_tool_calls,
        "keep_worktrees": args.keep_worktrees,
        "strict_gold_diff": args.strict_gold_diff,
        "seed": args.seed,
        "tasks": [str(Path(t)) for t in args.tasks],
    }
    (out_dir / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    print(f"[eval] config written to {out_dir/'config.yml'}")

    if args.smoke:
        return _smoke_run(
            out_dir=out_dir,
            tasks=args.tasks,
            variants=variants,
            temperature=args.temperature,
            max_turns=args.max_turns or 6,
            max_tool_calls=args.max_tool_calls or 12,
        )

    # Real model path: each repeat is a full pass over task AP/ST pairs, so a
    # partial sweep has a complete one-repeat baseline before refinements.
    model = make_adapter(args.model, reasoning_effort=args.reasoning_effort)
    results_path = out_dir / "results.jsonl"
    tasks = [load_task(Path(t)) for t in args.tasks]
    for task, variant, repeat in run_plan(tasks, variants, args.repeats):
        print(
            f"[eval] running {task.id} variant={variant} repeat={repeat}",
            flush=True,
        )
        try:
            res = run_one(
                task=task,
                variant=variant,
                repeat=repeat,
                model=model,
                temperature=args.temperature,
                top_p=args.top_p,
                top_k=args.top_k,
                max_tokens=args.max_tokens,
                worktree_root=out_dir / "worktrees",
                keep_worktrees=args.keep_worktrees,
                transcripts_dir=out_dir / "transcripts",
                diffs_dir=out_dir / "diffs",
                out_metrics={},
            )
        except Exception as e:
            res = {
                "run_id": f"{int(time.time())}_{task.id}/{variant}/{repeat}",
                "task_id": task.id,
                "variant": variant,
                "model": args.model,
                "temperature": args.temperature,
                "success": False,
                "failure_reason": "runner_exception",
                "error": str(e),
                "trace": traceback.format_exc(),
            }
        with results_path.open("a") as f:
            f.write(json.dumps(res) + "\n")
    print(f"[eval] results written to {results_path}")
    from . import report as _report
    _report.main(out_dir)
    return 0


# ---------------------------------------------------------------------
# Smoke test: scripted MockModel
# ---------------------------------------------------------------------

# Tasks with a canonical scripted solution in `_script_for_task`. The
# open-ended tasks (008-010) have no single canonical edit, so they are
# skipped by the smoke rather than scripted.
SCRIPTED_SMOKE_TASKS = {
    "001_set_line",
    "002_insert_guard",
    "003_delete_block",
    "004_replace_block",
    "005_move_helper",
    "006_rename_identifier",
    "007_multi_edit",
}


def _smoke_run(
    *,
    out_dir: Path,
    tasks: list[str],
    variants: list[str],
    temperature: float,
    max_turns: int,
    max_tool_calls: int,
) -> int:
    from .model_adapter import MockModel, ScriptedResponse, ToolCall

    results_path = out_dir / "results.jsonl"
    loaded_tasks = []
    for tpath in tasks:
        task = load_task(Path(tpath))
        if task.id in SCRIPTED_SMOKE_TASKS:
            loaded_tasks.append(task)
        else:
            # Tasks without a canonical smoke script (e.g. the open-ended
            # 008-010) would otherwise report spurious failures. Skip them
            # so the smoke stays a clean harness self-test on any glob.
            print(f"[smoke] {task.id} skipped (no scripted solution)", flush=True)
    for task, variant, repeat in run_plan(loaded_tasks, variants, 1):
        print(
            f"[smoke] {task.id} variant={variant}", flush=True
        )
        model, script = _script_for_task(task, variant)
        # Bound the script to max_turns/max_tool_calls
        if not script:
            script = []
        # Inject limit overrides by wrapping model
        model = _LimitWrapper(model, max_turns, max_tool_calls)
        res = run_one(
            task=task,
            variant=variant,
            repeat=repeat,
            model=model,
            temperature=temperature,
            max_tokens=None,
            worktree_root=out_dir / "worktrees",
            keep_worktrees=False,
            transcripts_dir=out_dir / "transcripts",
            diffs_dir=out_dir / "diffs",
            out_metrics={},
        )
        with results_path.open("a") as f:
            f.write(json.dumps(res) + "\n")
    print(f"[smoke] results written to {results_path}")
    from . import report as _report
    _report.main(out_dir)
    return 0


class _LimitWrapper:
    def __init__(self, inner, max_turns, max_tool_calls):
        self._inner = inner
        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls

    @property
    def name(self):
        return self._inner.name

    def chat(self, *, messages, tools, temperature, max_output_tokens, top_p=None):
        return self._inner.chat(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            top_p=top_p,
        )


def _script_for_task(task: Task, variant: str) -> tuple[MockModel, list[ScriptedResponse]]:
    """Build a minimal scripted model that solves ``task`` for ``variant``.

    The script:
      1. Calls ``status`` to inspect the worktree.
      2. Calls ``rg`` to find the relevant file/line.
      3. Calls ``read_range`` to obtain a span handle.
      4. Applies the right edit primitive(s).
      5. Calls ``run_check`` to verify.
      6. Emits a final text.
    """
    from .model_adapter import MockModel, ScriptedResponse, ToolCall

    assert task.id in SCRIPTED_SMOKE_TASKS, task.id
    if task.id == "001_set_line":
        return _script_set_line(task, variant)
    if task.id == "002_insert_guard":
        return _script_insert_guard(task, variant)
    if task.id == "003_delete_block":
        return _script_delete_block(task, variant)
    if task.id == "004_replace_block":
        return _script_replace_block(task, variant)
    if task.id == "005_move_helper":
        return _script_move_helper(task, variant)
    if task.id == "006_rename_identifier":
        return _script_rename(task, variant)
    if task.id == "007_multi_edit":
        return _script_multi_edit(task, variant)
    return MockModel([], final_text="I cannot solve this task in smoke mode."), []


# ---- task scripts ----


def _script_set_line(task, variant):
    from .model_adapter import MockModel, ScriptedResponse, ToolCall
    from .util import file_rev

    path = "include/config.hpp"
    fixture = ROOT / "eval" / "fixtures" / task.fixture / "base"
    rev = file_rev(fixture / path)
    script: list[ScriptedResponse] = []
    if variant == "apply_patch":
        script += [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "kDefaultRetries", "paths": ["include"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 20})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": _set_line_patch("constexpr int kDefaultRetries = 5;")})]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Updated the default retry count to 5."),
        ]
    else:
        script += [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "kDefaultRetries", "paths": ["include"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 20})]),
            ScriptedResponse(tool_calls=[ToolCall("edit_batch", {
                "atomic": True, "coords": "snapshot",
                "ops": [{"op": "splice", "at": "S1:5+1", "text": "constexpr int kDefaultRetries = 5;"}],
            })]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Updated the default retry count to 5."),
        ]
    return MockModel(script, final_text="done"), script


def _set_line_patch(replacement_line: str) -> str:
    return (
        "*** Begin Patch\n"
        "*** Update File: include/config.hpp\n"
        "@@\n"
        " namespace tiny {\n"
        "\n"
        "-constexpr int kDefaultRetries = 3;\n"
        f"+{replacement_line}\n"
        " constexpr int kMaxRetries = 10;\n"
        "*** End Patch\n"
    )


def _script_insert_guard(task, variant):
    from .model_adapter import MockModel, ScriptedResponse, ToolCall
    from .util import file_rev

    path = "src/parser.cpp"
    rev = file_rev(ROOT / "eval" / "fixtures" / task.fixture / "base" / path)
    if variant == "apply_patch":
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {path}\n"
            "@@\n"
            " std::optional<std::string> ParseName(std::string_view input) {\n"
            "+    if (input.empty()) return std::nullopt;\n"
            "     std::string acc;\n"
            "*** End Patch\n"
        )
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "ParseName", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 100})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": patch})]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Added the empty-input guard."),
        ]
    else:
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "ParseName", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 100})]),
            ScriptedResponse(tool_calls=[ToolCall("edit_batch", {
                "atomic": True, "coords": "snapshot",
                "ops": [{"op": "splice", "at": f"{path}@{rev}:49+0",
                         "text": "    if (input.empty()) return std::nullopt;\n"}],
            })]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Added the empty-input guard."),
        ]
    return MockModel(script, final_text="done"), script


def _script_delete_block(task, variant):
    from .model_adapter import MockModel, ScriptedResponse, ToolCall
    from .util import file_rev

    path = "src/normalize.cpp"
    rev = file_rev(ROOT / "eval" / "fixtures" / task.fixture / "base" / path)
    if variant == "apply_patch":
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {path}\n"
            "@@\n"
            " std::string NormalizePath(std::string_view input) {\n"
            "-    if (input.empty()) {\n"
            "-        // Legacy fallback: callers used to receive \".\" for empty input.\n"
            "-        // Current contract is to return an empty string instead.\n"
            "-        return \".\";\n"
            "-    }\n"
            "     std::string out;\n"
            "*** End Patch\n"
        )
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "Legacy fallback", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 50})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": patch})]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Removed the legacy fallback."),
        ]
    else:
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "Legacy fallback", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 50})]),
            ScriptedResponse(tool_calls=[ToolCall("edit_batch", {
                "atomic": True, "coords": "snapshot",
                "ops": [{"op": "splice", "at": f"{path}@{rev}:9+5"}],
            })]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Removed the legacy fallback."),
        ]
    return MockModel(script, final_text="done"), script


def _script_replace_block(task, variant):
    from .model_adapter import MockModel, ScriptedResponse, ToolCall
    from .util import file_rev

    path = "src/normalize.cpp"
    rev = file_rev(ROOT / "eval" / "fixtures" / task.fixture / "base" / path)
    new_body = (
        "unsigned char ClampToByte(int value) {\n"
        "    if (value < 0) return 0;\n"
        "    if (value > 255) return 255;\n"
        "    return static_cast<unsigned char>(value);\n"
        "}\n"
    )
    if variant == "apply_patch":
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {path}\n"
            "@@\n"
            " }\n"
            "\n"
            "-unsigned char ClampToByte(int value) {\n"
            "-    // Old verbose form kept for reference; current implementation is\n"
            "-    // simpler. We keep this block deliberately wordy.\n"
            "-    unsigned char result;\n"
            "-    if (value < 0) {\n"
            "-        result = 0;\n"
            "-    } else {\n"
            "-        if (value > 255) {\n"
            "-            result = 255;\n"
            "-        } else {\n"
            "-            result = static_cast<unsigned char>(value);\n"
            "-        }\n"
            "-    }\n"
            "-    return result;\n"
            "-}\n"
            + ("\n".join("+ " + l for l in new_body.split("\n")) + "\n")
            + "\n"
            + " }  // namespace tiny\n"
            "*** End Patch\n"
        )
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "ClampToByte", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 25, "count": 40})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": patch})]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Simplified ClampToByte."),
        ]
    else:
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "ClampToByte", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 25, "count": 40})]),
            ScriptedResponse(tool_calls=[ToolCall("edit_batch", {
                "atomic": True, "coords": "snapshot",
                "ops": [{"op": "splice", "at": f"{path}@{rev}:32+15", "text": new_body}],
            })]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Simplified ClampToByte."),
        ]
    return MockModel(script, final_text="done"), script


def _script_move_helper(task, variant):
    from .model_adapter import MockModel, ScriptedResponse, ToolCall
    from .util import file_rev

    path = "src/parser.cpp"
    rev = file_rev(ROOT / "eval" / "fixtures" / task.fixture / "base" / path)
    if variant == "apply_patch":
        # Two hunks: remove the forward decl, then remove the duplicate def
        # at the bottom, then add a new def above Tokenize.
        # Simpler: just one big hunk that replaces the forward decl + surrounding
        # context to a definition, then a second hunk that removes the bottom def.
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {path}\n"
            "@@\n"
            " }  // namespace\n"
            "\n"
            "-bool IsWhitespace(char c);\n"
            "+bool IsWhitespace(char c) {\n"
            "+    return c == ' ' || c == '\\t' || c == '\\n' || c == '\\r';\n"
            "+}\n"
            "\n"
            " std::vector<Token> Tokenize(std::string_view input) {\n"
            "*** End Patch\n"
        )
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "IsWhitespace", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 100})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": patch})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 30, "count": 100})]),
            # Second hunk: remove the bottom def
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": (
                "*** Begin Patch\n"
                f"*** Update File: {path}\n"
                "@@\n"
                " }\n"
                "\n"
                "-bool IsWhitespace(char c) {\n"
                "-    return c == ' ' || c == '\\t' || c == '\\n' || c == '\\r';\n"
                "-}\n"
                "\n"
                " std::optional<std::string> ParseName(std::string_view input) {\n"
                "*** End Patch\n"
            )})]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Moved IsWhitespace above Tokenize."),
        ]
    else:
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "IsWhitespace", "paths": ["src"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": path, "start": 1, "count": 100})]),
            ScriptedResponse(tool_calls=[ToolCall("edit_batch", {
                "atomic": True, "coords": "snapshot",
                "ops": [
                    {"op": "move_span",
                     "from": f"{path}@{rev}:44+3",
                     "to": f"{path}@{rev}:18"},
                    {"op": "splice",
                     "at": f"{path}@{rev}:16+1",
                     "text": ""},
                ],
            })]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Moved IsWhitespace above Tokenize."),
        ]
    return MockModel(script, final_text="done"), script


def _script_rename(task, variant):
    from .model_adapter import MockModel, ScriptedResponse, ToolCall

    if variant == "apply_patch":
        patch = (
            "*** Begin Patch\n"
            "*** Update File: include/parser.hpp\n"
            "@@\n"
            "-std::optional<std::string> ParseName(std::string_view input);\n"
            "+std::optional<std::string> ParseIdentifier(std::string_view input);\n"
            "*** End Patch\n"
        )
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "ParseName", "paths": ["src", "include"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": "include/parser.hpp", "start": 1, "count": 20})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": patch})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": (
                "*** Begin Patch\n"
                "*** Update File: src/parser.cpp\n"
                "@@\n"
                "-std::optional<std::string> ParseName(std::string_view input) {\n"
                "+std::optional<std::string> ParseIdentifier(std::string_view input) {\n"
                "*** End Patch\n"
            )})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": (
                "*** Begin Patch\n"
                "*** Update File: tests/parser_test.cpp\n"
                "@@\n"
                "-    auto r = tiny::ParseName(\"foo\");\n"
                "+    auto r = tiny::ParseIdentifier(\"foo\");\n"
                "*** End Patch\n"
            )})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": (
                "*** Begin Patch\n"
                "*** Update File: tests/parser_test.cpp\n"
                "@@\n"
                "-    auto e = tiny::ParseName(\"\");\n"
                "+    auto e = tiny::ParseIdentifier(\"\");\n"
                "*** End Patch\n"
            )})]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Renamed ParseName to ParseIdentifier."),
        ]
    else:
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "ParseName", "paths": ["src", "include"]})]),
            ScriptedResponse(tool_calls=[ToolCall("edit_batch", {
                "atomic": True, "coords": "snapshot",
                "ops": [{
                    "op": "replace_word",
                    "paths": ["src/parser.cpp", "include/parser.hpp", "tests/parser_test.cpp"],
                    "from": "ParseName", "to": "ParseIdentifier",
                    "boundary": "cpp_identifier",
                    "expected": {"exact": 5},
                }],
            })]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Renamed ParseName to ParseIdentifier."),
        ]
    return MockModel(script, final_text="done"), script


def _script_multi_edit(task, variant):
    from .model_adapter import MockModel, ScriptedResponse, ToolCall
    from .util import file_rev

    hpp = "include/users.hpp"
    cpp = "src/users.cpp"
    parser_cpp = "src/parser.cpp"
    rev_h = file_rev(ROOT / "eval" / "fixtures" / task.fixture / "base" / hpp)
    rev_c = file_rev(ROOT / "eval" / "fixtures" / task.fixture / "base" / cpp)
    rev_p = file_rev(ROOT / "eval" / "fixtures" / task.fixture / "base" / parser_cpp)
    if variant == "apply_patch":
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {hpp}\n"
            "@@\n"
            "-bool FindUser(int id, User* out);\n"
            "+std::optional<User> FindUser(int id);\n"
            "*** End Patch\n"
            "*** Begin Patch\n"
            f"*** Update File: {cpp}\n"
            "@@\n"
            "-bool FindUser(int id, User* out) {\n"
            "-    for (const auto& u : KnownUsers()) {\n"
            "-        if (u.id == id) {\n"
            "-            if (out) *out = u;\n"
            "-            return true;\n"
            "-        }\n"
            "-    }\n"
            "-    return false;\n"
            "-}\n"
            "+std::optional<User> FindUser(int id) {\n"
            "+    for (const auto& u : KnownUsers()) {\n"
            "+        if (u.id == id) return u;\n"
            "+    }\n"
            "+    return std::nullopt;\n"
            "+}\n"
            "*** End Patch\n"
            "*** Begin Patch\n"
            f"*** Update File: {parser_cpp}\n"
            "@@\n"
            "-    User u;\n"
            "-    bool seen = FindUser(1, &u);\n"
            "-    (void)seen;\n"
            "+    (void)FindUser(1);\n"
            "*** End Patch\n"
        )
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "FindUser", "paths": ["src", "include"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": hpp, "start": 1, "count": 30})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": cpp, "start": 1, "count": 50})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": _patch_a()})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": _patch_b()})]),
            ScriptedResponse(tool_calls=[ToolCall("apply_patch", {"patch": _patch_c()})]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Migrated FindUser to std::optional<User>."),
        ]
    else:
        # Three ops in one batch.
        script = [
            ScriptedResponse(tool_calls=[ToolCall("rg", {"pattern": "FindUser", "paths": ["src", "include"]})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": hpp, "start": 1, "count": 30})]),
            ScriptedResponse(tool_calls=[ToolCall("read_range", {"path": cpp, "start": 1, "count": 50})]),
            ScriptedResponse(tool_calls=[ToolCall("edit_batch", {
                "atomic": True, "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": f"{hpp}@{rev_h}:16+1", "text": "std::optional<User> FindUser(int id);"},
                    {"op": "splice", "at": f"{cpp}@{rev_c}:18+9", "text": (
                        "std::optional<User> FindUser(int id) {\n"
                        "    for (const auto& u : KnownUsers()) {\n"
                        "        if (u.id == id) return u;\n"
                        "    }\n"
                        "    return std::nullopt;\n"
                        "}\n"
                    )},
                    {"op": "splice", "at": f"{parser_cpp}@{rev_p}:21+4", "text": "    (void)FindUser(1);\n"},
                ],
            })]),
            ScriptedResponse(tool_calls=[ToolCall("run_check", {"name": "build"})]),
            ScriptedResponse(text="Migrated FindUser to std::optional<User>."),
        ]
    return MockModel(script, final_text="done"), script


# ---- multi-file apply_patch helpers (one Begin/End per call) ----


def _patch_a():
    return (
        "*** Begin Patch\n"
        "*** Update File: include/users.hpp\n"
        "@@\n"
        "-bool FindUser(int id, User* out);\n"
        "+std::optional<User> FindUser(int id);\n"
        "*** End Patch\n"
    )


def _patch_b():
    return (
        "*** Begin Patch\n"
        "*** Update File: src/users.cpp\n"
        "@@\n"
        "-bool FindUser(int id, User* out) {\n"
        "-    for (const auto& u : KnownUsers()) {\n"
        "-        if (u.id == id) {\n"
        "-            if (out) *out = u;\n"
        "-            return true;\n"
        "-        }\n"
        "-    }\n"
        "-    return false;\n"
        "-}\n"
        "+std::optional<User> FindUser(int id) {\n"
        "+    for (const auto& u : KnownUsers()) {\n"
        "+        if (u.id == id) return u;\n"
        "+    }\n"
        "+    return std::nullopt;\n"
        "+}\n"
        "*** End Patch\n"
    )


def _patch_c():
    return (
        "*** Begin Patch\n"
        "*** Update File: src/parser.cpp\n"
        "@@\n"
        "-    User u;\n"
        "-    bool seen = FindUser(1, &u);\n"
        "-    (void)seen;\n"
        "+    (void)FindUser(1);\n"
        "*** End Patch\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
