"""
Evaluator for VCSR OpenEvolve — compiles evolved graph.h, benchmarks
D-Graph Build Time and memory-access counts on real datasets, and compares
against baseline.

Scoring:
    score = 0.6 * (baseline_total_writes / candidate_total_writes)
          + 0.4 * (baseline_time         / candidate_time)

    score > 1.0 means candidate is better than baseline.

Baseline is collected by run_experiment.sh into baseline.json.

combined_score accumulates as datasets complete: failed/skipped datasets
count as 0 in the average, so partial results still provide signal while
being naturally penalized.  OpenEvolve uses combined_score as the primary
ranking metric.
"""

import json
import os
import re
import shutil
import subprocess
import statistics
import sys
import tempfile
import time as time_mod
from typing import Any, Dict, List, Optional, Tuple

from openevolve.evaluation_result import EvaluationResult

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_SCRIPT_DIR, "src")
_DATA_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "data")
_GRAPH_H = os.path.join(_SRC_DIR, "graph.h")
_BASELINE_JSON = os.path.join(_SCRIPT_DIR, "baseline.json")

# ---------------------------------------------------------------------------
# Dataset definitions
# ---------------------------------------------------------------------------
DATASETS = [
    {
        "name": "mathoverflow",
        "base": os.path.join(_DATA_DIR, "sx-mathoverflow-unique-undir.base.el"),
        "dynamic": os.path.join(_DATA_DIR, "sx-mathoverflow-unique-undir.dynamic.el"),
    },
    {
        "name": "enron",
        "base": os.path.join(_DATA_DIR, "enron-unique-undir.base.el"),
        "dynamic": os.path.join(_DATA_DIR, "enron-unique-undir.dynamic.el"),
    },
    {
        "name": "amazon",
        "base": os.path.join(_DATA_DIR, "amazon0601.base.el"),
        "dynamic": os.path.join(_DATA_DIR, "amazon0601.dynamic.el"),
    },
    {
        "name": "stackoverflow",
        "base": os.path.join(_DATA_DIR, "sx-unique-undir.base.el"),
        "dynamic": os.path.join(_DATA_DIR, "sx-unique-undir.dynamic.el"),
    },
]

# ---------------------------------------------------------------------------
# Scoring weights — matches Python VCSR evaluator
# ---------------------------------------------------------------------------
SCORING_COMPONENTS = [
    ("total_writes", 0.60),
    ("time",         0.40),
]

EVAL_RUNS = 3         # runs per dataset (85% deterministic, time only 15%)
COMPILE_TIMEOUT = 60   # seconds
RUN_TIMEOUT = 300      # seconds per single bfs run
TOTAL_TIMEOUT = 240    # total budget for full eval (< OpenEvolve's 600s)
MAX_RETRIES = 3        # retries per run on transient crash
QUICK_REJECT = 0.5     # stage1 score below this → skip full eval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_baseline() -> Dict[str, Any]:
    """Load baseline from JSON file."""
    if not os.path.exists(_BASELINE_JSON):
        raise FileNotFoundError(
            f"Baseline file not found: {_BASELINE_JSON}\n"
            "Run `bash run_experiment.sh` to collect baseline first."
        )
    with open(_BASELINE_JSON) as f:
        return json.load(f)


def _parse_output(output: str) -> Dict[str, Any]:
    """Extract D-Graph Build Time and METRICS from program output."""
    result: Dict[str, Any] = {}
    m = re.search(r"D-Graph Build Time:\s*([\d.]+)", output)
    if m:
        result["build_time"] = float(m.group(1))
    m = re.search(r"METRICS_START\n(.*?)METRICS_END", output, re.DOTALL)
    if m:
        for line in m.group(1).strip().split("\n"):
            parts = line.strip().split()
            if len(parts) == 2:
                result[parts[0]] = int(parts[1])
    return result


def _patch_and_compile(program_path: str, work_dir: str) -> Optional[str]:
    """Compile evolved graph.h, return path to bfs binary or None."""
    src_dst = os.path.join(work_dir, "src")
    if os.path.exists(src_dst):
        shutil.rmtree(src_dst)
    shutil.copytree(_SRC_DIR, src_dst)
    shutil.copy2(program_path, os.path.join(src_dst, "graph.h"))
    shutil.copy2(os.path.join(_SCRIPT_DIR, "Makefile"), work_dir)
    try:
        result = subprocess.run(
            ["make", "bfs"], cwd=work_dir,
            capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    bfs_path = os.path.join(work_dir, "bfs")
    if result.returncode != 0 or not os.path.exists(bfs_path):
        return None
    return bfs_path


def _run_once(
    bfs_path: str, ds: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """Run bfs once on a dataset, return parsed output dict or None."""
    try:
        result = subprocess.run(
            [bfs_path, "-B", ds["base"], "-D", ds["dynamic"],
             "-s", "-n", "1", "-r", "0"],
            capture_output=True, text=True, timeout=RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    parsed = _parse_output(result.stdout)
    if "build_time" not in parsed:
        return None
    return parsed


def _benchmark_dataset(
    bfs_path: str, ds: Dict[str, str], runs: int
) -> Optional[Dict[str, Any]]:
    """Run bfs on a dataset `runs` times with retry.  Returns metrics or None."""
    times = []
    last_run = None
    for _ in range(runs):
        parsed = None
        for _ in range(MAX_RETRIES):
            parsed = _run_once(bfs_path, ds)
            if parsed is not None:
                break
        if parsed is None:
            return None
        times.append(parsed["build_time"])
        last_run = parsed
    return {
        "time": statistics.median(times),
        "total_writes": last_run.get("total_writes", 0),
        "total_reads": last_run.get("total_reads", 0),
        "write_insert": last_run.get("num_write_insert", 0),
        "write_rebal": last_run.get("num_write_rebal", 0),
        "write_resize": last_run.get("num_write_resize", 0),
        "read_insert": last_run.get("num_read_insert", 0),
        "read_rebal": last_run.get("num_read_rebal", 0),
        "read_resize": last_run.get("num_read_resize", 0),
        "num_rebalance": last_run.get("num_rebalance", 0),
        "num_resize": last_run.get("num_resize", 0),
    }


def _compute_score(
    baseline: Dict[str, Any], result: Dict[str, Any], name: str
) -> Tuple[float, Dict[str, float]]:
    """Compute weighted score for one dataset."""
    details: Dict[str, float] = {}
    combined = 0.0
    for component, weight in SCORING_COMPONENTS:
        bl_val = baseline.get(f"{name}_{component}", 0)
        cand_val = result.get(component, 0)
        ratio = bl_val / max(cand_val, 1.0 if component != "time" else 1e-9)
        details[f"{component}_improvement"] = ratio
        combined += weight * ratio
    details["combined"] = combined
    return combined, details


# ---------------------------------------------------------------------------
# Single evaluate() — no stage1/stage2 split.
#
# Flow:
#   1. Compile
#   2. Quick check on smallest dataset (1 run) — reject terrible candidates
#   3. Full eval on all datasets (EVAL_RUNS each) with time budget
#   4. combined_score is set ONLY if full eval completes successfully
# ---------------------------------------------------------------------------
def evaluate(program_path: str) -> EvaluationResult:
    metrics: Dict[str, Any] = {}
    artifacts: Dict[str, Any] = {}
    t_start = time_mod.monotonic()

    work_dir = tempfile.mkdtemp(prefix="vcsr_eval_")
    try:
        # ---- Step 1: Compile ----
        bfs_path = _patch_and_compile(program_path, work_dir)
        if bfs_path is None:
            metrics["compiles"] = 0.0
            metrics["combined_score"] = 0.0
            metrics["compile_error"] = 1.0
            artifacts["error"] = "compilation failed"
            artifacts["feedback"] = (
                "Code failed to compile. Check for syntax errors, missing "
                "includes, type mismatches, or use of C++ features beyond C++11."
            )
            return EvaluationResult(metrics=metrics, artifacts=artifacts)
        metrics["compiles"] = 1.0
        metrics["compile_error"] = 0.0

        try:
            baseline = _load_baseline()
        except FileNotFoundError as e:
            metrics["combined_score"] = 0.0
            artifacts["error"] = str(e)
            return EvaluationResult(metrics=metrics, artifacts=artifacts)

        # ---- Step 2: Quick check on smallest dataset (1 run) ----
        ds0 = DATASETS[0]
        quick = _benchmark_dataset(bfs_path, ds0, runs=1)
        if quick is None:
            metrics["runs_successfully"] = 0.0
            metrics["combined_score"] = 0.0
            metrics["runtime_error"] = 1.0
            artifacts["error"] = f"{ds0['name']}: runtime failure"
            artifacts["feedback"] = (
                "Code compiled but crashed at runtime (assertion failure or "
                "segfault). Most likely cause: new_index values violate the "
                "overlap constraint (new_index[i] < new_index[i-1] + degree), "
                "or indices go out of bounds. Ensure every vertex has enough "
                "space for its edges and no edge lists overlap."
            )
            return EvaluationResult(metrics=metrics, artifacts=artifacts)
        metrics["runtime_error"] = 0.0

        quick_score, quick_details = _compute_score(baseline, quick, ds0["name"])
        metrics["stage1_score"] = quick_score
        for k, v in quick_details.items():
            metrics[f"stage1_{k}"] = v

        if quick_score < QUICK_REJECT:
            metrics["combined_score"] = 0.0
            metrics["quick_rejected"] = 1.0
            artifacts["rejected"] = f"stage1 score {quick_score:.4f} < {QUICK_REJECT}"
            artifacts["feedback"] = (
                f"Quick check on {ds0['name']} scored {quick_score:.4f} "
                f"(threshold {QUICK_REJECT}). The gap distribution is much "
                "worse than baseline — it caused significantly more rebalance "
                "writes or rebalance events. The strategy needs fundamental "
                "rethinking, not just parameter tuning."
            )
            return EvaluationResult(metrics=metrics, artifacts=artifacts)
        metrics["quick_rejected"] = 0.0

        # ---- Step 3: Full evaluation on all datasets ----
        scores = []
        datasets_passed = 0
        dataset_errors = []

        for ds in DATASETS:
            name = ds["name"]

            # Check time budget
            elapsed = time_mod.monotonic() - t_start
            if elapsed > TOTAL_TIMEOUT:
                metrics[f"{name}_runs_successfully"] = 0.0
                metrics[f"{name}_score"] = 0.0
                metrics[f"{name}_timeout"] = 1.0
                artifacts[f"{name}_error"] = "time budget exhausted"
                dataset_errors.append(
                    f"{name}: timed out (budget exhausted after {elapsed:.0f}s)"
                )
                scores.append(0.0)
                continue

            result = _benchmark_dataset(bfs_path, ds, runs=EVAL_RUNS)

            if result is None:
                metrics[f"{name}_runs_successfully"] = 0.0
                metrics[f"{name}_score"] = 0.0
                metrics[f"{name}_runtime_error"] = 1.0
                artifacts[f"{name}_error"] = "runtime failure or timeout"
                dataset_errors.append(
                    f"{name}: crashed or timed out during benchmark"
                )
                scores.append(0.0)
                continue

            datasets_passed += 1
            metrics[f"{name}_timeout"] = 0.0
            metrics[f"{name}_runtime_error"] = 0.0

            # Raw candidate metrics
            metrics[f"{name}_runs_successfully"] = 1.0
            for key in ["time", "total_writes", "write_insert", "write_rebal",
                         "write_resize", "num_rebalance", "num_resize"]:
                metrics[f"{name}_candidate_{key}"] = float(result[key])
                bl_val = baseline.get(f"{name}_{key}", 0)
                metrics[f"{name}_baseline_{key}"] = float(bl_val)

            # Per-dataset score
            combined, details = _compute_score(baseline, result, name)
            for k, v in details.items():
                metrics[f"{name}_{k}"] = v
            metrics[f"{name}_score"] = combined
            scores.append(combined)

        metrics["runs_successfully"] = datasets_passed / len(DATASETS)
        metrics["datasets_passed"] = float(datasets_passed)

        # ---- combined_score: cumulative average ----
        # Failed/skipped datasets count as 0 in the average, so partial
        # results still give signal while being naturally penalized.
        metrics["combined_score"] = sum(scores) / len(DATASETS)

        if datasets_passed < len(DATASETS):
            metrics["timeout"] = 1.0
            artifacts["incomplete"] = f"{datasets_passed}/{len(DATASETS)} datasets passed"
            artifacts["feedback"] = (
                f"Only {datasets_passed}/{len(DATASETS)} datasets completed. "
                + "; ".join(dataset_errors)
                + ". The algorithm is too slow on larger graphs — likely the "
                "gap distribution causes excessive rebalances that cascade. "
                "Consider reducing computational complexity in "
                "calculate_positions_V1 or improving gap allocation to prevent "
                "rebalance cascades on high-degree vertices."
            )
        else:
            metrics["timeout"] = 0.0

        artifacts["eval_time"] = f"{time_mod.monotonic() - t_start:.1f}s"

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return EvaluationResult(metrics=metrics, artifacts=artifacts)


# ---------------------------------------------------------------------------
# Standalone execution (for manual testing)
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="VCSR evaluator — benchmark evolved graph.h against baseline."
    )
    parser.add_argument(
        "program_path", nargs="?", default=None,
        help="Path to evolved graph.h. If omitted, uses the current src/graph.h.",
    )
    args = parser.parse_args()
    program_path = args.program_path or _GRAPH_H

    print(f"=== Full evaluation ({EVAL_RUNS} runs per dataset) ===")
    result = evaluate(program_path)

    print("\n--- Metrics ---")
    for k, v in sorted(result.metrics.items()):
        print(f"  {k}: {v}")
    if result.artifacts:
        print("\n--- Artifacts ---")
        for k, v in sorted(result.artifacts.items()):
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
