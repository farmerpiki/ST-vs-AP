#!/usr/bin/env bash
#
# Canonical temperature / top_p / top_k sweep for the apply_patch-vs-span_tools eval.
#
# The (temperature, top_p) pairings below are the standard grid the prior
# sweeps used (see commits "60-run sweeps ..."). Keep them here so a sweep
# is one command and the params never have to be re-derived.
#
# Recommended MiniMax-M3 parameters (per official docs): T=1.0, top_p=0.95, top_k=40.
#
# Usage:
#   eval/scripts/sweep.sh OUT_PREFIX [TASKS] [REPEATS] [VARIANTS] [MODEL]
#
#   OUT_PREFIX  required. Each config lands in <OUT_PREFIX>_<label>/
#               e.g. OUT_PREFIX=eval/runs/t011 -> eval/runs/t011_t07_topp09/
#   TASKS       space- or glob-expandable task paths
#               (default: eval/tasks/*.yml)
#   REPEATS     repeats per (task, variant) (default: 3)
#   VARIANTS    comma list (default: apply_patch,span_tools)
#   MODEL       model spec (default: minimax)
#
# Override the grid with SWEEP_CONFIGS="label:temp:topp:topk ..." (any of topp/topk
# can be empty to leave the parameter unspecified).
# Examples:
#   SWEEP_CONFIGS="t0:0: t1:1.0:0.95:40" eval/scripts/sweep.sh eval/runs/quick
#   SWEEP_CONFIGS="t0:0:: t1:1.0:0.95:40" eval/scripts/sweep.sh eval/runs/quick  # top_k=40 only for t1
#
# "Fast 1-repeat n times" mode:
#   REPEATS=1 SWEEP_N_PASSES=3 eval/scripts/sweep.sh eval/runs/probe
# Runs the grid 3 independent times (each with --repeats 1 and --seed 0/1/2),
# giving you 3 independent samples per (task, variant) in separate output
# dirs (eval/runs/probe_<label>_p{1,2,3}/).  Useful for fast probing to
# surface any specific bad cases without paying for a full N-repeat sweep.
#   SWEEP_SEED=N  -- override the seed in single-pass mode (default 0).
#
# Calibrated static costs (system prompt + tool schemas, server-tokenized) are
# measured automatically before the first config via calibrate_static.py and
# written to <OUT_PREFIX>/.calibration.json.  This is what .probe.py uses to
# compute agentic_in. Skip with SWEEP_SKIP_CALIBRATION=1.
set -euo pipefail

OUT_PREFIX="${1:?usage: sweep.sh OUT_PREFIX [TASKS] [REPEATS] [VARIANTS] [MODEL]}"
TASKS="${2:-eval/tasks/*.yml}"
REPEATS="${3:-3}"
VARIANTS="${4:-apply_patch,span_tools}"
MODEL="${5:-minimax}"

# label:temperature:top_p:top_k  (empty top_p / top_k fields => flag omitted)
# Default grid: t=0; t=0.7 at top_p {unset, 0.9, 0.95}; t=0.95 and t=1.0 at 0.95.
# The t1_topp095 config now also sets top_k=40 (per MiniMax-M3 recommendation).
CONFIGS="${SWEEP_CONFIGS:-t0:0:: t07:0.7:: t07_topp09:0.7:0.9: t07_topp095:0.7:0.95: t095_topp095:0.95:0.95: t1_topp095_topk40:1.0:0.95:40}"

PY="${PY:-.venv/bin/python}"

# ---- Calibrate static token costs (sys + tools) before the sweep runs ----
if [ "${SWEEP_SKIP_CALIBRATION:-0}" != "1" ]; then
    mkdir -p "$OUT_PREFIX"
    CALIB="$OUT_PREFIX/.calibration.json"
    echo "[sweep] calibrating static costs -> $CALIB"
    if "$PY" eval/scripts/calibrate_static.py --out "$CALIB"; then
        "$PY" - "$CALIB" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for v, info in d.get("variants", {}).items():
    colds = [m["static_cold_minus_hi"] for m in info["measurements"]]
    print(f"[sweep]   {v}: static = {colds[0]} (server-tokenized sys+schema)")
PY
    else
        echo "[sweep] WARNING: calibration failed; .probe.py will fall back to hardcoded values" >&2
    fi
fi

# SWEEP_N_PASSES: when set (with REPEATS=1), run the sweep N_PASSES
# independent times, each with --repeats 1 and a different --seed
# (0, 1, 2, ...).  Output lands in <OUT_PREFIX>_<label>_p<pass>/.
# This is the "fast 1-repeat n times" mode: one quick sample per
# (task, variant) per pass, repeated for independent samples so you
# can spot bad cases without paying for full N-repeat sweeps.

run_sweep() {
    # run_sweep <pass_label> <seed>
    local pass_label="$1"
    local seed="$2"
    for cfg in $CONFIGS; do
        label="${cfg%%:*}"
        rest="${cfg#*:}"
        temp="${rest%%:*}"
        rest="${rest#*:}"
        topp="${rest%%:*}"
        topk="${rest#*:}"

        topp_args=()
        [ -n "$topp" ] && topp_args=(--top-p "$topp")
        topk_args=()
        [ -n "$topk" ] && topk_args=(--top-k "$topk")

        out="${OUT_PREFIX}_${label}${pass_label}"
        rm -rf "$out"
        echo "[sweep] ${label}${pass_label}: temp=${temp} top_p=${topp:-<unset>} top_k=${topk:-<unset>} seed=${seed} -> ${out}"
        # shellcheck disable=SC2086
        "$PY" -m eval.runner.eval \
            --model "$MODEL" \
            --tasks $TASKS \
            --variants "$VARIANTS" \
            --repeats "$REPEATS" \
            --temperature "$temp" \
            --seed "$seed" \
            "${topp_args[@]}" \
            "${topk_args[@]}" \
            --out "$out"
    done
}

if [ "${REPEATS}" = "1" ] && [ "${SWEEP_N_PASSES:-1}" -gt 1 ] 2>/dev/null; then
    for pass in $(seq 1 "$SWEEP_N_PASSES"); do
        run_sweep "_p${pass}" "$((pass - 1))"
    done
    echo "[sweep] done. configs: ${CONFIGS} x ${SWEEP_N_PASSES} passes (1 repeat each)"
else
    run_sweep "" "${SWEEP_SEED:-0}"
    echo "[sweep] done. configs: ${CONFIGS} x ${REPEATS} repeats"
fi
