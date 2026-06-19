"""Generate ``report.md`` and ``report.csv`` from ``results.jsonl``."""
from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def load_results(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    return float(statistics.median(xs))


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def _paired_key(r: dict) -> tuple[str, int]:
    return (r["task_id"], r.get("repeat", 0))


def build_summary(results: list[dict]) -> dict:
    """Compute aggregate metrics over the result set."""
    by_task_variant: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in results:
        by_task_variant[(r["task_id"], r["variant"])].append(r)
    tasks = sorted({r["task_id"] for r in results})
    variants = sorted({r["variant"] for r in results})

    # Per-task table
    task_rows: list[dict] = []
    for t in tasks:
        baseline = by_task_variant.get((t, "apply_patch"), [])
        cand = by_task_variant.get((t, "span_tools"), [])
        n_b = len(baseline)
        n_c = len(cand)
        b_succ = sum(1 for r in baseline if r.get("success"))
        c_succ = sum(1 for r in cand if r.get("success"))
        # Paired tokens for both-success pairs.
        paired_b: list[float] = []
        paired_c: list[float] = []
        paired_write_b: list[float] = []
        paired_write_c: list[float] = []
        paired_turns_b: list[float] = []
        paired_turns_c: list[float] = []
        if n_b and n_c:
            base_by = {_paired_key(r): r for r in baseline}
            for cr in cand:
                k = _paired_key(cr)
                br = base_by.get(k)
                if br and br.get("success") and cr.get("success"):
                    paired_b.append(float(br.get("total_billed_tokens", 0)))
                    paired_c.append(float(cr.get("total_billed_tokens", 0)))
                    paired_write_b.append(float(br.get("write_tool_arg_tokens", 0)))
                    paired_write_c.append(float(cr.get("write_tool_arg_tokens", 0)))
                    paired_turns_b.append(float(br.get("turns", 0)))
                    paired_turns_c.append(float(cr.get("turns", 0)))
        b_med = _median([r.get("total_billed_tokens", 0) for r in baseline])
        c_med = _median([r.get("total_billed_tokens", 0) for r in cand])
        b_in_cached = _median([r.get("total_input_cached_tokens", 0) for r in baseline])
        c_in_cached = _median([r.get("total_input_cached_tokens", 0) for r in cand])
        b_out_reasoning = _median([r.get("total_output_reasoning_tokens", 0) for r in baseline])
        c_out_reasoning = _median([r.get("total_output_reasoning_tokens", 0) for r in cand])
        if paired_b:
            savings = 100.0 * (_median(paired_b) - _median(paired_c)) / max(_median(paired_b), 1)
        else:
            savings = 0.0
        write_delta = _median(paired_write_c) - _median(paired_write_b) if paired_write_b else 0
        turns_delta = _median(paired_turns_c) - _median(paired_turns_b) if paired_turns_b else 0
        task_rows.append(
            {
                "task": t,
                "n": min(n_b, n_c) if n_b and n_c else 0,
                "both_success": sum(
                    1
                    for r in cand
                    if r.get("success")
                    and any(
                        br.get("success") and _paired_key(br) == _paired_key(r)
                        for br in baseline
                    )
                ),
                "baseline_success": b_succ,
                "span_success": c_succ,
                "baseline_median": int(b_med),
                "span_median": int(c_med),
                "savings_pct": round(savings, 1),
                "write_arg_delta": int(write_delta),
                "turns_delta": int(turns_delta),
                "baseline_cached_median": int(b_in_cached),
                "span_cached_median": int(c_in_cached),
                "baseline_reasoning_median": int(b_out_reasoning),
                "span_reasoning_median": int(c_out_reasoning),
            }
        )

    # Failure breakdown
    fail_reasons: dict[str, int] = defaultdict(int)
    fail_tags: dict[str, int] = defaultdict(int)
    for r in results:
        if not r.get("success"):
            rsn = r.get("failure_reason") or "unknown"
            fail_reasons[rsn] += 1
            for tag in r.get("failure_tags") or [rsn]:
                fail_tags[tag] += 1

    # Macro/micro savings
    macro_savings = (
        statistics.mean([r["savings_pct"] for r in task_rows if r["both_success"]])
        if any(r["both_success"] for r in task_rows)
        else 0.0
    )
    # Micro: aggregate all successful paired runs.
    micro_b: list[float] = []
    micro_c: list[float] = []
    for t in tasks:
        baseline = by_task_variant.get((t, "apply_patch"), [])
        cand = by_task_variant.get((t, "span_tools"), [])
        base_by = {_paired_key(r): r for r in baseline}
        for cr in cand:
            k = _paired_key(cr)
            br = base_by.get(k)
            if br and br.get("success") and cr.get("success"):
                micro_b.append(float(br.get("total_billed_tokens", 0)))
                micro_c.append(float(cr.get("total_billed_tokens", 0)))
    micro_savings = (
        100.0 * (sum(micro_b) - sum(micro_c)) / max(sum(micro_b), 1) if micro_b else 0.0
    )

    # Success rates
    success_rates = {
        v: (
            100.0
            * sum(1 for r in by_task_variant.values() and [] or [])  # placeholder
        )
        for v in variants
    }
    for v in variants:
        runs = [r for r in results if r["variant"] == v]
        success_rates[v] = (
            100.0 * sum(1 for r in runs if r.get("success")) / max(len(runs), 1)
        )

    # Token breakdown per variant (sum across all runs).
    token_breakdown: dict[str, dict[str, int]] = {}
    for v in variants:
        runs = [r for r in results if r["variant"] == v]
        token_breakdown[v] = {
            "runs": len(runs),
            "successful": sum(1 for r in runs if r.get("success")),
            "input_total": sum(int(r.get("total_input_tokens", 0) or 0) for r in runs),
            "input_cached": sum(int(r.get("total_input_cached_tokens", 0) or 0) for r in runs),
            "input_uncached": sum(int(r.get("total_input_uncached_tokens", 0) or 0) for r in runs),
            "output_total": sum(int(r.get("total_output_tokens", 0) or 0) for r in runs),
            "output_reasoning": sum(int(r.get("total_output_reasoning_tokens", 0) or 0) for r in runs),
            "billed_total": sum(int(r.get("total_billed_tokens", 0) or 0) for r in runs),
        }

    return {
        "tasks": task_rows,
        "fail_reasons": dict(fail_reasons),
        "fail_tags": dict(fail_tags),
        "macro_savings": macro_savings,
        "micro_savings": micro_savings,
        "success_rates": success_rates,
        "token_breakdown": token_breakdown,
        "tasks_count": len(tasks),
        "variants": variants,
        "n_total": len(results),
    }


def _fmt_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "_(no rows)_\n"
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    lines = [header, sep]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in columns) + " |")
    return "\n".join(lines) + "\n"


def render_report(summary: dict, config: dict, run_dir: Path) -> str:
    rows = summary["tasks"]
    cols = [
        "task",
        "both_success",
        "baseline_median",
        "span_median",
        "savings_pct",
        "write_arg_delta",
        "turns_delta",
    ]
    parts: list[str] = []
    parts.append("# Eval report\n")
    parts.append("## Config\n")
    parts.append("```yaml\n")
    parts.append(_yaml_dump(config))
    parts.append("```\n")
    parts.append(
        f"Runs: {summary['n_total']} across {summary['tasks_count']} task(s) and "
        f"{len(summary['variants'])} variant(s).\n"
    )
    parts.append(
        "Macro-avg savings (median per task): "
        f"{summary['macro_savings']:.1f}%\n"
    )
    parts.append(
        "Micro-avg savings (aggregate successful pairs): "
        f"{summary['micro_savings']:.1f}%\n"
    )
    parts.append("## Success rates\n")
    for v, rate in summary["success_rates"].items():
        parts.append(f"- {v}: {rate:.1f}%\n")
    if summary.get("token_breakdown"):
        parts.append("## Token breakdown (all runs)\n")
        tok_cols = ["variant", "runs", "successful", "billed_total", "input_total", "input_cached", "input_uncached", "output_total", "output_reasoning"]
        tok_rows = []
        for v, b in summary["token_breakdown"].items():
            tok_rows.append({
                "variant": v,
                "runs": b["runs"],
                "successful": b["successful"],
                "billed_total": b["billed_total"],
                "input_total": b["input_total"],
                "input_cached": b["input_cached"],
                "input_uncached": b["input_uncached"],
                "output_total": b["output_total"],
                "output_reasoning": b["output_reasoning"],
            })
        parts.append(_fmt_table(tok_rows, tok_cols))
    parts.append("## Paired savings (per task)\n")
    parts.append(_fmt_table(rows, cols))
    if summary["fail_reasons"]:
        parts.append("## Failure breakdown\n")
        for k, v in sorted(summary["fail_reasons"].items(), key=lambda x: -x[1]):
            parts.append(f"- {k}: {v}\n")
    if summary.get("fail_tags"):
        parts.append("## Failure tags\n")
        for k, v in sorted(summary["fail_tags"].items(), key=lambda x: -x[1]):
            parts.append(f"- {k}: {v}\n")
    # Largest wins/losses
    both = [r for r in rows if r["both_success"]]
    if both:
        parts.append("## Largest wins / losses\n")
        wins = sorted(both, key=lambda r: -r["savings_pct"])[:3]
        losses = sorted(both, key=lambda r: r["savings_pct"])[:3]
        for r in wins:
            parts.append(
                f"- WIN  {r['task']}: {r['savings_pct']}% (median "
                f"{r['baseline_median']} -> {r['span_median']})\n"
            )
        for r in losses:
            parts.append(
                f"- LOSS {r['task']}: {r['savings_pct']}% (median "
                f"{r['baseline_median']} -> {r['span_median']})\n"
            )
    return "".join(parts)


def _yaml_dump(obj) -> str:
    import yaml as _y
    return _y.safe_dump(obj, sort_keys=False)


def write_csv(summary: dict, path: Path) -> None:
    cols = [
        "task",
        "n",
        "both_success",
        "baseline_success",
        "span_success",
        "baseline_median",
        "span_median",
        "savings_pct",
        "write_arg_delta",
        "turns_delta",
        "baseline_cached_median",
        "span_cached_median",
        "baseline_reasoning_median",
        "span_reasoning_median",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in summary["tasks"]:
            w.writerow(r)


def render_per_run_csv(results: list[dict], path: Path) -> None:
    cols = [
        "run_id",
        "task_id",
        "variant",
        "model",
        "temperature",
        "success",
        "failure_reason",
        "failure_tags",
        "check_verdicts",
        "turns",
        "tool_calls",
        "total_billed_tokens",
        "total_input_tokens",
        "total_input_cached_tokens",
        "total_input_uncached_tokens",
        "total_output_tokens",
        "total_output_reasoning_tokens",
        "peak_input_tokens",
        "system_prompt_tokens",
        "tool_schema_tokens",
        "write_tool_arg_tokens",
        "changed_files",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            row = {c: r.get(c, "") for c in cols}
            cf = r.get("changed_files") or []
            row["changed_files"] = ",".join(cf)
            row["failure_tags"] = ",".join(r.get("failure_tags") or [])
            row["check_verdicts"] = ",".join(
                str(cr.get("verdict") or "") for cr in r.get("check_results") or []
            )
            w.writerow(row)


def main(run_dir: Path) -> None:
    results_path = run_dir / "results.jsonl"
    results = load_results(results_path)
    config_path = run_dir / "config.yml"
    config: dict = {}
    if config_path.is_file():
        import yaml as _y
        try:
            config = _y.safe_load(config_path.read_text()) or {}
        except Exception:
            config = {}
    summary = build_summary(results)
    md = render_report(summary, config, run_dir)
    (run_dir / "report.md").write_text(md)
    write_csv(summary, run_dir / "report.csv")
    render_per_run_csv(results, run_dir / "results.csv")
    print(f"[report] wrote {run_dir/'report.md'}, {run_dir/'report.csv'}, {run_dir/'results.csv'}")


if __name__ == "__main__":
    import sys

    raise SystemExit(main(Path(sys.argv[1])))
