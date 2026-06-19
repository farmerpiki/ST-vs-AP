"""Per-task AP vs ST comparison report (sweep results).

Compact format. Each cell with a variant-vs-variant comparison shows the delta
with a semaphore emoji: 🟢 ST win, 🔴 ST loss, 🟡 within ±threshold.

Per task:
  pass  AP   🟢/🔴 count (or "-/3" if AP had 0 runs)
       ST   🟢/🔴 count
  turns  ST-AP   🟢/🔴/🟡 Δ turns (raw ap/st)
  tools  ST-AP   🟢/🔴/🟡 Δ tool-calls (raw ap/st)
  cached AP/ST    raw (no delta)
  uncached ST-AP    🟢/🔴/🟡 Δ% (raw ap/st)
  out    ST-AP    🟢/🔴/🟡 Δ% (raw ap/st)

Usage:
  .venv/bin/python eval/runs/.report.py [dir1 dir2 ...]
  .venv/bin/python eval/runs/.report.py --threshold 5
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path


EM = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _signed_int(x: float) -> str:
    if isinstance(x, float) and not x.is_integer():
        return f"{x:+.1f}"
    return f"{int(x):+d}"


def _signed_pct(x: float) -> str:
    return f"{x:+.1f}%"


def _fmt_int(n) -> str:
    return f"{int(round(n)):,}"


# USD per 1M tokens.  _cost returns microUSD (1 USD = 1_000_000 uUSD).
COST_UNCACHED = 0.30
COST_CACHED   = 0.06
COST_OUTPUT   = 1.20


def _cost(uncached: float, cached: float, output: float) -> float:
    # tokens * USD/M / 1e6 = USD; * 1e6 = microUSD
    return (uncached * COST_UNCACHED + cached * COST_CACHED + output * COST_OUTPUT)


def _fmt_cost(x: float) -> str:
    # microUSD with thousands separators (unit lives in column header)
    return f"{int(round(x)):,}"


def _fmt_cost_delta(x: float) -> str:
    # sign-prefixed microUSD delta (unit lives in column header)
    sign = "+" if x > 0 else "-"
    return f"{sign}{int(round(abs(x))):,}"


import unicodedata as _unicodedata
def _vlen(s) -> int:
    return sum(2 if _unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _vpad(s, width) -> str:
    pad = width - _vlen(s)
    return s + " " * pad if pad > 0 else s



def _col_widths(by_tv, threshold):
    """Pre-compute per-column (delta_W, ap_W, st_W) for the per-task table.
    For each metric column, we look at all (task, variant) formatted ap and
    st values, take the max length, and return a dict.
    """
    tasks = sorted({t for (t, _v) in by_tv.keys()})
    if not tasks:
        return {}

    def avg(rs, k): return sum(r.get(k, 0) for r in rs) / len(rs) if rs else 0
    def sums(rs, *ks):
        n = len(rs)
        return [sum(r.get(k, 0) for r in rs) / n for k in ks]
    def mcost(rs):
        # Total cost across the runs (NOT divided by n). Cost is a dollar
        # total; unlike turns/tokens it is not a per-run average.
        return (sum(r.get("total_input_uncached_tokens", 0) for r in rs) * 0.30 +
                sum(r.get("total_input_cached_tokens", 0) for r in rs) * 0.06 +
                sum(r.get("total_output_tokens", 0) for r in rs) * 1.20)

    def maxlen(extract):
        aw = sw = dw = 0
        for t in tasks:
            ap = by_tv.get((t, "apply_patch"), [])
            st = by_tv.get((t, "span_tools"), [])
            if not ap or not st: continue
            a_str, s_str = extract(ap, st)
            aw = max(aw, len(a_str))
            sw = max(sw, len(s_str))
            # delta: percentage or raw value -- for turns/tools it's the
            # raw count delta, for tokens it's the percentage.
            d_str = extract.delta(ap, st) if hasattr(extract, "delta") else ""
            dw = max(dw, len(d_str))
        return dw, aw, sw

    class _E:
        def __init__(self, f, delta_f):
            self.f = f
            self.delta_f = delta_f
        def __call__(self, ap, st):
            return self.f(ap), self.f(st)
        def delta(self, ap, st):
            return self.delta_f(ap, st)

    cols = {}
    cols["turns"]  = maxlen(_E(lambda rs: f"{avg(rs, 'turns'):.1f}",
                                lambda ap, st: f"{avg(st, 'turns') - avg(ap, 'turns'):+.1f}"))
    cols["tools"]  = maxlen(_E(lambda rs: f"{avg(rs, 'tool_calls'):.1f}",
                                lambda ap, st: f"{avg(st, 'tool_calls') - avg(ap, 'tool_calls'):+.1f}"))
    cols["cached"] = maxlen(_E(lambda rs: f"{int(avg(rs, 'total_input_cached_tokens')):,}",
                                lambda ap, st: f"{((avg(st, 'total_input_cached_tokens') - avg(ap, 'total_input_cached_tokens')) / avg(ap, 'total_input_cached_tokens') * 100) if avg(ap, 'total_input_cached_tokens') > 0 else 0:+.1f}%"))
    cols["uncach"] = maxlen(_E(lambda rs: f"{int(avg(rs, 'total_input_uncached_tokens')):,}",
                                lambda ap, st: f"{((avg(st, 'total_input_uncached_tokens') - avg(ap, 'total_input_uncached_tokens')) / avg(ap, 'total_input_uncached_tokens') * 100) if avg(ap, 'total_input_uncached_tokens') > 0 else 0:+.1f}%"))
    cols["out"]    = maxlen(_E(lambda rs: f"{int(avg(rs, 'total_output_tokens')):,}",
                                lambda ap, st: f"{((avg(st, 'total_output_tokens') - avg(ap, 'total_output_tokens')) / avg(ap, 'total_output_tokens') * 100) if avg(ap, 'total_output_tokens') > 0 else 0:+.1f}%"))
    # Delta is the absolute µ$ cost difference (st_total - ap_total), so the
    # delta field must be wide enough for the real numbers -- no cap.
    _ucost_dw, _ucost_aw, _ucost_sw = maxlen(_E(
        lambda rs: f"{int(round(mcost(rs))):,}",
        lambda ap, st: _fmt_cost_delta(mcost(st) - mcost(ap))))
    cols["ucost"] = (_ucost_dw, _ucost_aw, _ucost_sw)
    return cols


def cell_w(delta_pct, ap_v, st_v, threshold, dw, aw, sw,
           ap_fmt=lambda x: f"{x}", st_fmt=lambda x: f"{x}",
           delta_fmt=lambda x: f"{x:+.1f}%", delta_value=None):
    """Build a single ST-AP cell with no parens: {delta:>DW}{sem}{ap:>AW}/{st:<SW}.

    `delta_pct` (a percent) drives the semaphore.
    `delta_value` (optional, default = `delta_pct`) is the number actually
    displayed in the delta field.  For turns/tools we pass the raw count
    delta; for token columns we pass the percent.
    """
    if delta_pct <= -threshold:  key = "green"
    elif delta_pct >= threshold: key = "red"
    else:                        key = "yellow"
    val = delta_pct if delta_value is None else delta_value
    if ap_v and ap_v > 0:
        return f"{delta_fmt(val):>{dw}}{EM[key]}{ap_fmt(ap_v):>{aw}}/{st_fmt(st_v):>{sw}}"
    return f"{delta_fmt(0.0):>{dw}}{EM['yellow']}—/{'':<{sw}}"



def load_results(p: Path):
    if not p.exists():
        return []
    out = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _per_task_core(rows, threshold, repeats, picker, header_extras=""):
    """Shared per-task table printer.  `picker` is a callable that takes a
    list of runs (for one (task, variant) cell) and returns either a single
    run dict (for the "cheapest" mode) or a list of run dicts (for the
    averages mode, which is then aggregated).

    Returns the list of tasks printed (so the caller can do post-analysis
    if needed).
    """
    by_tv = defaultdict(list)
    for r in rows:
        by_tv[(r["task_id"], r["variant"])].append(r)

    tasks = sorted({r["task_id"] for r in rows})
    if not tasks:
        print("(no results)")
        return []

    # Pre-compute per-column widths (delta_W, ap_W, st_W) from the data
    # so each cell packs tightly.  This makes ap and st have separate
    # widths when their value distributions differ (e.g. uncached: ap_max=5, st_max=6).
    widths = _col_widths(by_tv, threshold)

    def aggregate(rs):
        # If picker returned a single run, treat it as the data point (n=1,
        # ok=1/0).  If it returned a list, average and report n.
        picked = picker(rs)
        if isinstance(picked, dict):
            picked = [picked]
        n = len(picked)
        if n == 0:
            return 0, 0, 0, 0, 0, 0, 0
        ok = sum(1 for r in picked if r["success"])
        turns = sum(r.get("turns", 0) for r in picked) / n
        tools = sum(r.get("tool_calls", 0) for r in picked) / n
        cached = sum(r.get("total_input_cached_tokens", 0) for r in picked) / n
        unch = sum(r.get("total_input_uncached_tokens", 0) for r in picked) / n
        out = sum(r.get("total_output_tokens", 0) for r in picked) / n
        return ok, n, turns, tools, cached, unch, out

    def compare_pass_cell(ap_ok, ap_n, st_ok, st_n):
        a = ap_ok if ap_n else 0
        s = st_ok if st_n else 0
        if ap_n == 0 and st_n == 0:
            return "-/-"
        if ap_n == 0:
            return f"-🟡{s}"
        if st_n == 0:
            return f"{a}🟡-"
        if a == s:
            return f"{a}🟡{s}"
        if s > a:
            return f"{a}🟢{s}"
        return f"{a}🔴{s}"

    def cell(delta_pct, txt, raw):
        if delta_pct <= -threshold:  key = "green"
        elif delta_pct >= threshold: key = "red"
        else:                        key = "yellow"
        return f"{EM[key]}{txt}({raw})"

    # Header widths match the per-row cells (computed dynamically).
    def _col_visible_width(w):
        # em = 2 visual cells, slash = 1
        dw, aw, sw = w
        return dw + 2 + aw + 1 + sw
    turns_cw  = _col_visible_width(widths["turns"])
    tools_cw  = _col_visible_width(widths["tools"])
    cached_cw = _col_visible_width(widths["cached"])
    unch_cw   = _col_visible_width(widths["uncach"])
    out_cw    = _col_visible_width(widths["out"])
    cost_cw   = _col_visible_width(widths["ucost"])
    print("  " + f"{'task':<24s}  " + _vpad('pass', 4)
          + "  " + _vpad('turns', turns_cw)
          + "  " + _vpad('tools', tools_cw)
          + "  " + _vpad('cached', cached_cw)
          + "  " + _vpad('uncached', unch_cw)
          + "  " + _vpad('out', out_cw)
          + "  " + _vpad('µ$cost', cost_cw) + header_extras)

    printed = []
    for t in tasks:
        ap = by_tv.get((t, "apply_patch"), [])
        st = by_tv.get((t, "span_tools"), [])
        ap_ok, ap_n, ap_t, ap_to, ap_c, ap_u, ap_o = aggregate(ap)
        st_ok, st_n, st_t, st_to, st_c, st_u, st_o = aggregate(st)

        if ap_n == 0 and st_n == 0:
            continue

        pass_cell = compare_pass_cell(int(ap_ok), int(ap_n), int(st_ok), int(st_n))

        if ap_n and st_n:
            d_turns = st_t - ap_t
            d_tools = st_to - ap_to
            d_unch_pct = (st_u - ap_u) / ap_u * 100 if ap_u > 0 else 0.0
            d_out_pct  = (st_o - ap_o) / ap_o * 100 if ap_o > 0 else 0.0
            d_turns_pct = d_turns / ap_t * 100 if ap_t else 0.0
            d_tools_pct = d_tools / ap_to * 100 if ap_to else 0.0

            turns_dw, turns_aw, turns_sw = widths["turns"]
            tools_dw, tools_aw, tools_sw = widths["tools"]
            unch_dw,  unch_aw,  unch_sw  = widths["uncach"]
            out_dw,   out_aw,   out_sw   = widths["out"]
            turns_cell = cell_w(d_turns_pct, ap_t, st_t, threshold, turns_dw, turns_aw, turns_sw,
                                delta_fmt=lambda x: f"{x:+.1f}",
                                delta_value=d_turns,
                                ap_fmt=lambda x: f"{x:.1f}", st_fmt=lambda x: f"{x:.1f}")
            tools_cell = cell_w(d_tools_pct, ap_to, st_to, threshold, tools_dw, tools_aw, tools_sw,
                                delta_fmt=lambda x: f"{x:+.1f}",
                                delta_value=d_tools,
                                ap_fmt=lambda x: f"{x:.1f}", st_fmt=lambda x: f"{x:.1f}")
            unch_cell  = cell_w(d_unch_pct, ap_u, st_u, threshold, unch_dw, unch_aw, unch_sw,
                                ap_fmt=lambda x: f"{int(x):,}", st_fmt=lambda x: f"{int(x):,}")
            out_cell   = cell_w(d_out_pct, ap_o, st_o, threshold, out_dw, out_aw, out_sw,
                                ap_fmt=lambda x: f"{int(x):,}", st_fmt=lambda x: f"{int(x):,}")
        else:
            turns_cell = tools_cell = unch_cell = out_cell = "—"

        cached_dw, cached_aw, cached_sw = widths["cached"]
        cached_cell = cell_w((st_c - ap_c) / ap_c * 100 if ap_c > 0 else 0,
                                ap_c, st_c, threshold, cached_dw, cached_aw, cached_sw,
                                ap_fmt=lambda x: f"{int(x):,}", st_fmt=lambda x: f"{int(x):,}")

        # Cost is a TOTAL (sum over the task's runs), not a per-run average:
        # ap_u/ap_c/ap_o come back per-run averaged, so multiply by the run
        # count to recover the total spend.
        ap_cost = _cost(ap_u, ap_c, ap_o) * ap_n if ap_n else 0.0
        st_cost = _cost(st_u, st_c, st_o) * st_n if st_n else 0.0
        d_cost = st_cost - ap_cost
        ucost_dw, ucost_aw, ucost_sw = widths["ucost"]
        # delta_value = absolute µ$ difference (st-ap); delta_pct only drives
        # the semaphore colour.
        cost_cell = cell_w(d_cost / ap_cost * 100 if ap_cost > 0 else 0,
                           ap_cost, st_cost, threshold, ucost_dw, ucost_aw, ucost_sw,
                           delta_fmt=lambda x: _fmt_cost_delta(x),
                           ap_fmt=lambda x: _fmt_cost(x), st_fmt=lambda x: _fmt_cost(x),
                           delta_value=d_cost)

        def _col_visible_width(w):
            # em = 2 visual cells, slash = 1
            dw, aw, sw = w
            return dw + 2 + aw + 1 + sw
        turns_cw  = _col_visible_width(widths["turns"])
        tools_cw  = _col_visible_width(widths["tools"])
        cached_cw = _col_visible_width(widths["cached"])
        unch_cw   = _col_visible_width(widths["uncach"])
        out_cw    = _col_visible_width(widths["out"])
        cost_cw   = _col_visible_width(widths["ucost"])
        # Humanize the task name: drop "NNN_" prefix, replace _ with space.
        tname = t.split("_", 1)[-1].replace("_", " ")
        # _vpad pads to visual cells (emojis = 2).  Don't use :{w}s here --
        # char-width pad misaligns cells containing emojis by 1 cell each.
        print("  " + f"{tname:<24s}  " + _vpad(pass_cell, 4)
              + "  " + _vpad(turns_cell, turns_cw)
              + "  " + _vpad(tools_cell, tools_cw)
              + "  " + _vpad(cached_cell, cached_cw)
              + "  " + _vpad(unch_cell, unch_cw)
              + "  " + _vpad(out_cell, out_cw)
              + "  " + _vpad(cost_cell, cost_cw))
        printed.append(t)

    return printed


def per_task_table(rows, threshold, repeats):
    """Average over all repeats for each (task, variant)."""
    return _per_task_core(rows, threshold, repeats, picker=lambda rs: rs)


def cheapest_run_table(rows, threshold, repeats):
    """For each (task, variant) keep only the single run with the lowest
    total cost (uncached*COST_UNCACHED + cached*COST_CACHED + output*COST_OUTPUT).

    Successful runs are preferred -- if at least one run succeeded we pick
    the cheapest among successes.  If every run failed we still report the
    cheapest run (it just won't be a useful comparison).
    """
    def cheapest(rs):
        if not rs:
            return []
        successes = [r for r in rs if r["success"]]
        pool = successes if successes else rs
        return [min(pool, key=lambda r: _cost(
            r.get("total_input_uncached_tokens", 0),
            r.get("total_input_cached_tokens", 0),
            r.get("total_output_tokens", 0)))]
    return _per_task_core(rows, threshold, repeats, picker=cheapest)


def overall_table(rows, threshold: float) -> None:
    by_v = defaultdict(list)
    for r in rows:
        by_v[r["variant"]].append(r)
    print(f"\nOVERALL")
    for v in ("apply_patch", "span_tools"):
        rs = by_v[v]
        if not rs: continue
        n = len(rs); ok = sum(1 for r in rs if r["success"])
        turns = sum(r.get("turns", 0) for r in rs) / n
        tools = sum(r.get("tool_calls", 0) for r in rs) / n
        cached = sum(r.get("total_input_cached_tokens", 0) for r in rs) / n
        unch = sum(r.get("total_input_uncached_tokens", 0) for r in rs) / n
        out = sum(r.get("total_output_tokens", 0) for r in rs) / n
        cost = _cost(unch, cached, out)
        print(f"  {v:<12s} n={n:>2d} pass={ok:>2d}  turns={turns:.1f}  tools={tools:.1f}  "
              f"cached={_fmt_int(cached)}  uncached={_fmt_int(unch)}  out={_fmt_int(out)}  µ$={_fmt_cost(cost)}")
    if "apply_patch" in by_v and "span_tools" in by_v:
        ap = by_v["apply_patch"]; st = by_v["span_tools"]
        ap_n = len(ap); ap_ok = sum(1 for r in ap if r["success"])
        st_n = len(st); st_ok = sum(1 for r in st if r["success"])
        ap_t  = sum(r.get("turns", 0) for r in ap) / ap_n
        st_t  = sum(r.get("turns", 0) for r in st) / st_n
        ap_to = sum(r.get("tool_calls", 0) for r in ap) / ap_n
        st_to = sum(r.get("tool_calls", 0) for r in st) / st_n
        ap_u  = sum(r.get("total_input_uncached_tokens", 0) for r in ap) / ap_n
        st_u  = sum(r.get("total_input_uncached_tokens", 0) for r in st) / st_n
        ap_c  = sum(r.get("total_input_cached_tokens", 0) for r in ap) / ap_n
        st_c  = sum(r.get("total_input_cached_tokens", 0) for r in st) / st_n
        ap_o  = sum(r.get("total_output_tokens", 0) for r in ap) / ap_n
        st_o  = sum(r.get("total_output_tokens", 0) for r in st) / st_n
        d_turns  = st_t - ap_t
        d_tools  = st_to - ap_to
        d_unch_pct = (st_u - ap_u) / ap_u * 100 if ap_u > 0 else 0.0
        d_out_pct  = (st_o - ap_o) / ap_o * 100 if ap_o > 0 else 0.0
        d_turns_pct = d_turns / ap_t * 100 if ap_t else 0.0
        d_tools_pct = d_tools / ap_to * 100 if ap_to else 0.0
        d_cached_pct = (st_c - ap_c) / ap_c * 100 if ap_c > 0 else 0.0

        def cell(delta_pct, txt, raw):
            if delta_pct <= -threshold:  key = "green"
            elif delta_pct >= threshold: key = "red"
            else:                        key = "yellow"
            return f"{EM[key]}{txt}({raw})"

        ap_cost = _cost(ap_u, ap_c, ap_o)
        st_cost = _cost(st_u, st_c, st_o)
        d_cost = st_cost - ap_cost
        d_cost_pct = d_cost / ap_cost * 100 if ap_cost > 0 else 0.0

        print()
        print(f"  turns  {cell(d_turns_pct, _signed_int(d_turns), f'{ap_t:.1f}/{st_t:.1f}')}")
        print(f"  tools  {cell(d_tools_pct, _signed_int(d_tools), f'{ap_to:.1f}/{st_to:.1f}')}")
        print(f"  cached {cell(d_cached_pct, _signed_pct(d_cached_pct), f'{_fmt_int(ap_c)}/{_fmt_int(st_c)}') if ap_c > 0 else f'{_fmt_int(ap_c)}/{_fmt_int(st_c)}'}")
        print(f"  uncached {cell(d_unch_pct, _signed_pct(d_unch_pct), f'{_fmt_int(ap_u)}/{_fmt_int(st_u)}')}")
        print(f"  out    {cell(d_out_pct, _signed_pct(d_out_pct), f'{_fmt_int(ap_o)}/{_fmt_int(st_o)}')}")
        print(f"  $cost  {cell(d_cost_pct, _fmt_cost_delta(d_cost), f'{_fmt_cost(ap_cost)}/{_fmt_cost(st_cost)}')}")


def grand_totals(rows, threshold: float) -> None:
    """Sum tokens / cost / turns / tool calls across all runs (not
    averaged).  Pass rate is per-run; everything else is the literal sum
    of provider-reported usage."""
    by_v = defaultdict(list)
    for r in rows:
        by_v[r["variant"]].append(r)
    if "apply_patch" not in by_v or "span_tools" not in by_v:
        return
    ap = by_v["apply_patch"]; st = by_v["span_tools"]
    n_ap, n_st = len(ap), len(st)
    if n_ap == 0 or n_st == 0:
        return

    def sums(rs, field):
        return sum(r.get(field, 0) for r in rs)

    ap_turns = sums(ap, "turns");     st_turns = sums(st, "turns")
    ap_tools = sums(ap, "tool_calls"); st_tools = sums(st, "tool_calls")
    ap_cached = sums(ap, "total_input_cached_tokens"); st_cached = sums(st, "total_input_cached_tokens")
    ap_unch = sums(ap, "total_input_uncached_tokens"); st_unch = sums(st, "total_input_uncached_tokens")
    ap_out = sums(ap, "total_output_tokens"); st_out = sums(st, "total_output_tokens")
    ap_cost = _cost(ap_unch, ap_cached, ap_out)
    st_cost = _cost(st_unch, st_cached, st_out)
    ap_ok = sum(1 for r in ap if r["success"])
    st_ok = sum(1 for r in st if r["success"])

    # Best totals are computed further down; initialize here so the width
    # block can reference them safely.
    ap_b_turns = st_b_turns = ap_b_tools = st_b_tools = 0
    ap_b_cached = st_b_cached = ap_b_unch = st_b_unch = 0
    ap_b_out = st_b_out = ap_b_cost = st_b_cost = 0
    ap_b_ok = st_b_ok = 0

    # Pre-compute column widths for the TOTALS table from BOTH the all
    # totals and the best totals.  Each cell has its own per-column (DW,
    # AW, SW), derived from the max of (all_ap, all_st, best_ap, best_st)
    # so the column packs tightly when all and best have different scales.
    def _pct(a, b):
        if a == 0: return 0
        return (b - a) / a * 100
    def _int_str(v): return str(int(v))
    def _comma_str(v): return f"{int(v):,}"

    # ap and st widths per column, considering both all and best totals.
    def _aw_sw(ap_all, st_all, ap_best, st_best, fmt):
        aw = max(len(fmt(ap_all)), len(fmt(ap_best)))
        sw = max(len(fmt(st_all)), len(fmt(st_best)))
        return aw, sw
    aw_turns, sw_turns  = _aw_sw(ap_turns,  st_turns,  ap_b_turns,  st_b_turns,  _int_str)
    aw_tools, sw_tools  = _aw_sw(ap_tools,  st_tools,  ap_b_tools,  st_b_tools,  _int_str)
    aw_cached, sw_cached = _aw_sw(ap_cached, st_cached, ap_b_cached, st_b_cached, _comma_str)
    aw_unch,   sw_unch  = _aw_sw(ap_unch,   st_unch,   ap_b_unch,   st_b_unch,   _comma_str)
    aw_out,    sw_out   = _aw_sw(ap_out,    st_out,    ap_b_out,    st_b_out,    _comma_str)
    aw_cost,   sw_cost  = _aw_sw(ap_cost,   st_cost,   ap_b_cost,   st_b_cost,   _fmt_cost)

    # Delta widths per column.  turns/tools: raw count deltas.  tokens:
    # percentage deltas.  cost: microUSD delta.
    def _int_delta(ap_all, st_all, ap_best, st_best):
        return max(len(f"{int(st_all - ap_all):+d}"), len(f"{int(st_best - ap_best):+d}"))
    def _pct_delta_str(ap_all, st_all, ap_best, st_best):
        return max(len(f"{_pct(ap_all, st_all):+.1f}%"), len(f"{_pct(ap_best, st_best):+.1f}%"))
    def _cost_delta_str(ap_all, st_all, ap_best, st_best):
        return max(len(_fmt_cost_delta(st_all - ap_all)),
                   len(_fmt_cost_delta(st_best - ap_best)))
    dw_turns  = _int_delta(ap_turns,  st_turns,  ap_b_turns,  st_b_turns)
    dw_tools  = _int_delta(ap_tools,  st_tools,  ap_b_tools,  st_b_tools)
    dw_cached = _pct_delta_str(ap_cached, st_cached, ap_b_cached, st_b_cached)
    dw_unch   = _pct_delta_str(ap_unch,   st_unch,   ap_b_unch,   st_b_unch)
    dw_out    = _pct_delta_str(ap_out,    st_out,    ap_b_out,    st_b_out)
    dw_cost   = _cost_delta_str(ap_cost, st_cost,  ap_b_cost,  st_b_cost)

    def cell_int(ap_v, st_v, dw, aw, sw):
        d = st_v - ap_v
        return cell_w(_pct(ap_v, st_v), ap_v, st_v, threshold, dw, aw, sw,
                      delta_fmt=lambda x: f"{x:+d}",
                      ap_fmt=lambda x: f"{x:d}", st_fmt=lambda x: f"{x:d}",
                      delta_value=d)

    def cell_pct(ap_v, st_v, dw, aw, sw):
        return cell_w(_pct(ap_v, st_v), ap_v, st_v, threshold, dw, aw, sw,
                      ap_fmt=lambda x: f"{int(x):,}", st_fmt=lambda x: f"{int(x):,}")

    def cell_cost(ap_c, st_c, dw, aw, sw):
        d = st_c - ap_c
        return cell_w(d / ap_c * 100 if ap_c else 0, ap_c, st_c, threshold, dw, aw, sw,
                      delta_fmt=lambda x: _fmt_cost_delta(x),
                      ap_fmt=lambda x: _fmt_cost(x), st_fmt=lambda x: _fmt_cost(x),
                      delta_value=d)

    # --- grand total (sum of all runs) ---
    # Compute best (cheapest successful run per task, else cheapest failed) totals up front.
    def best_picks(rs):
        by_t = defaultdict(list)
        for r in rs:
            by_t[r["task_id"]].append(r)
        out = []
        for runs in by_t.values():
            if not runs:
                continue
            # Cheapest successful run if any succeeded; otherwise fall back to
            # the cheapest failed run (mirrors the per-task cheapest table --
            # don't drop a task just because every run failed).
            successes = [r for r in runs if r["success"]]
            pool = successes if successes else runs
            out.append(min(pool, key=lambda r: _cost(
                r.get("total_input_uncached_tokens", 0),
                r.get("total_input_cached_tokens", 0),
                r.get("total_output_tokens", 0))))
        return out

    ap_best = best_picks(ap)
    st_best = best_picks(st)

    def sums(rs, field):
        return sum(r.get(field, 0) for r in rs)

    if ap_best and st_best:
        ap_b_turns  = sums(ap_best, "turns");                st_b_turns  = sums(st_best, "turns")
        ap_b_tools  = sums(ap_best, "tool_calls");           st_b_tools  = sums(st_best, "tool_calls")
        ap_b_cached = sums(ap_best, "total_input_cached_tokens"); st_b_cached = sums(st_best, "total_input_cached_tokens")
        ap_b_unch   = sums(ap_best, "total_input_uncached_tokens"); st_b_unch   = sums(st_best, "total_input_uncached_tokens")
        ap_b_out    = sums(ap_best, "total_output_tokens");  st_b_out    = sums(st_best, "total_output_tokens")
        ap_b_cost   = _cost(ap_b_unch, ap_b_cached, ap_b_out)
        st_b_cost   = _cost(st_b_unch, st_b_cached, st_b_out)
        ap_b_ok     = sum(1 for r in ap_best if r["success"])
        st_b_ok     = sum(1 for r in st_best if r["success"])
    else:
        ap_b_turns = st_b_turns = ap_b_tools = st_b_tools = 0
        ap_b_cached = st_b_cached = ap_b_unch = st_b_unch = ap_b_out = st_b_out = 0
        ap_b_cost = st_b_cost = 0.0
        ap_b_ok = st_b_ok = 0

    # Pass column: raw count delta with "ok/total" formatted ap/st.
    # Compute widths from data.
    def _pass_str(ok, total): return f"{ok}/{total}"
    # For all: ap=(ap_ok, n_ap), st=(st_ok, n_st).  For best: same with b_ vars.
    ap_all_pass  = _pass_str(ap_ok,  n_ap)
    st_all_pass  = _pass_str(st_ok,  n_st)
    ap_best_pass = _pass_str(ap_b_ok, len(ap_best))
    st_best_pass = _pass_str(st_b_ok, len(st_best))
    aw_pass = max(len(ap_all_pass), len(ap_best_pass))
    sw_pass = max(len(st_all_pass), len(st_best_pass))
    d_all_pass  = st_ok  - ap_ok
    d_best_pass = st_b_ok - ap_b_ok
    dw_pass = max(len(f"{d_all_pass:+d}"), len(f"{d_best_pass:+d}"))

    def _cell_pass(ap_v, st_v, dw, aw, sw):
        # ap_v, st_v are (ok, total) tuples.  We use the percent change in
        # pass count vs total for the semaphore.
        ok_a, tot_a = ap_v; ok_s, tot_s = st_v
        d = ok_s - ok_a
        delta_pct = d / tot_a * 100 if tot_a else 0
        return cell_w(delta_pct, 1.0, 1.0, threshold, dw, aw, sw,
                      delta_fmt=lambda x: f"{x:+d}",
                      ap_fmt=lambda _: f"{ok_a}/{tot_a}",
                      st_fmt=lambda _: f"{ok_s}/{tot_s}",
                      delta_value=d)

    # Build cells for all = (ap_all, st_all) and best = (ap_best, st_best)
    # for each metric, then print a 2-row x 7-column table:
    #   row1 = "all", row2 = "best"
    #   cols = pass, turns, tools, cached, uncached, out, µ$cost
    # Each metric column has a fixed (DW, AW, SW) so slashes/semaphores
    # align vertically down the column.
    LBL = 4
    metrics = [
        ("turns",  ap_turns,  st_turns,  ap_b_turns,  st_b_turns,
                   lambda a, b: cell_int(a, b, dw_turns, aw_turns, sw_turns)),
        ("pass",   (ap_ok, n_ap), (st_ok, n_st), (ap_b_ok, len(ap_best)), (st_b_ok, len(st_best)),
                   lambda a, b: _cell_pass(a, b, dw_pass, aw_pass, sw_pass)),
        ("tools",  ap_tools,  st_tools,  ap_b_tools,  st_b_tools,
                   lambda a, b: cell_int(a, b, dw_tools, aw_tools, sw_tools)),
        ("cached", ap_cached, st_cached, ap_b_cached, st_b_cached,
                   lambda a, b: cell_pct(a, b, dw_cached, aw_cached, sw_cached)),
        ("uncached", ap_unch,   st_unch,   ap_b_unch,   st_b_unch,
                   lambda a, b: cell_pct(a, b, dw_unch, aw_unch, sw_unch)),
        ("out",    ap_out,    st_out,    ap_b_out,    st_b_out,
                   lambda a, b: cell_pct(a, b, dw_out, aw_out, sw_out)),
        ("µ$cost", ap_cost,   st_cost,   ap_b_cost,   st_b_cost,
                   lambda a, b: cell_cost(a, b, dw_cost, aw_cost, sw_cost)),
    ]
    # Pre-format all cells, then compute each metric column's visual width
    # so the columns line up.
    all_cells  = [m[5](m[1], m[2]) for m in metrics]
    best_cells = [m[5](m[3], m[4]) for m in metrics]
    col_widths = [max(_vlen(a), _vlen(b)) for a, b in zip(all_cells, best_cells)]

    # Build simple labels.
    label_all  = "all"
    label_best = "best"

    def _row(label, cells):
        # _vpad pads to `w` VISUAL cells.  Don't use :{w}s afterwards --
        # the :Ns format counts characters, but w is in visual cells
        # (emoji = 2 cells but 1 char), so the char-width spec would
        # misalign cells that contain emojis.
        line = f"  {label:<{LBL}s}"
        for cell, w in zip(cells, col_widths):
            line += f"  {_vpad(cell, w)}"
        return line

    print("\n=== TOTALS  (all = sum of all runs; best = sum of cheapest successful run per task, or cheapest failed run if none succeeded) ===")
    print(_row("",       [m[0] for m in metrics]))
    print(_row(label_all,  all_cells))
    print(_row(label_best, best_cells))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*",
                    help="Result dirs (default: all eval/runs/sweep_*/results.jsonl and v5_*/results.jsonl)")
    ap.add_argument("--threshold", type=float, default=2.5,
                    help="+/- threshold % for win/loss vs tie (default 2.5)")
    ap.add_argument("--repeats", type=int, default=None,
                    help="Total repeats per (task, variant) -- shown as denominator in pass column when runs missing (default: inferred from the data)")

    args = ap.parse_args()

    if args.dirs:
        rows = []
        for d in args.dirs:
            for r in load_results(Path(d) / "results.jsonl"):
                rows.append(r)
    else:
        rows = []
        for p in sorted(Path("eval/runs").glob("sweep_*/results.jsonl")):
            for r in load_results(p):
                rows.append(r)
        for p in sorted(Path("eval/runs").glob("v5_*/results.jsonl")):
            for r in load_results(p):
                rows.append(r)
        for p in sorted(Path("eval/runs").glob("v6_*/results.jsonl")):
            for r in load_results(p):
                rows.append(r)

    if not rows:
        print("(no results found)")
        return

    # Repeats per (task, variant): use the flag if given, else infer from the
    # data as the largest run-count of any (task, variant) group.
    if args.repeats is not None:
        repeats = args.repeats
    else:
        _grp = defaultdict(int)
        for r in rows:
            _grp[(r.get("task_id"), r.get("variant"))] += 1
        repeats = max(_grp.values()) if _grp else 0

    print(f"=== per-task AP vs ST  (n={len(rows)} runs, threshold = +/-{args.threshold}%, repeats={repeats}) ===")
    print(f"    pass = AP/ST (raw pass counts, semaphore = ST win/loss/equal).")
    print(f"    turns/tools/cached/uncached/out/µ$cost = ST-AP delta:")
    print(f"        emoji + signed delta + (AP/ST in parens).  emoji: 🟢 ST < AP (win), 🟡 within ±{args.threshold}%, 🔴 ST > AP (loss).")
    per_task_table(rows, args.threshold, repeats)
    print(f"\n=== cheapest-run AP vs ST  (1 cheapest run per (task, variant); threshold = +/-{args.threshold}%) ===")
    print(f"    Same column conventions as the per-task table above.  Cells")
    print(f"    reflect a single run (the lowest-cost one for that (task, variant)),")
    print(f"    not the average of all repeats.")
    cheapest_run_table(rows, args.threshold, repeats)
    grand_totals(rows, args.threshold)


if __name__ == "__main__":
    main()
