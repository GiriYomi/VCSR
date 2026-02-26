#!/bin/bash
# ==========================================================================
#  run_experiment.sh — One-click full VCSR OpenEvolve experiment
#
#  Usage:
#    bash run_experiment.sh              # default 100 iterations
#    bash run_experiment.sh 50           # custom iteration count
#    bash run_experiment.sh 100 --resume # resume from last checkpoint
#
#  What it does:
#    1. Activate conda environment (dgap_evolve)
#    2. Build baseline binary from original source
#    3. Collect baseline timings (10 runs per dataset, take median)
#    4. Save baseline.json
#    5. Launch OpenEvolve evolution
#    6. On completion, print summary of best result
#
#  Designed to run unattended on a server — all output logged to file.
# ==========================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ITERATIONS="${1:-100}"
RESUME_FLAG="${2:-}"
# Time is only 15% of score; 3 runs suffices for a stable median
BASELINE_RUNS=3
CONDA_ENV="dgap_evolve"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$(cd "$SCRIPT_DIR/../data" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/openevolve_output_${TIMESTAMP}"
CHECKPOINT_DIR="$OUTPUT_DIR/checkpoints"
LOG_FILE="$SCRIPT_DIR/experiment_${TIMESTAMP}.log"

DATASETS=(
  "mathoverflow|sx-mathoverflow-unique-undir"
  "enron|enron-unique-undir"
  "amazon|amazon0601"
  "stackoverflow|sx-unique-undir"
)

# ---------------------------------------------------------------------------
# Logging helper — tee to both stdout and log file
# ---------------------------------------------------------------------------
exec > >(tee -a "$LOG_FILE") 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ---------------------------------------------------------------------------
# Step 0: Activate conda environment
# ---------------------------------------------------------------------------
log "=========================================="
log " VCSR OpenEvolve Experiment"
log " Iterations: $ITERATIONS"
log " Log file:   $LOG_FILE"
log "=========================================="
echo ""

log "Step 0: Activating conda environment '$CONDA_ENV'"
eval "$(conda shell.bash hook 2>/dev/null)"
conda activate "$CONDA_ENV"
log "  Python: $(which python)"
log "  OpenEvolve: $(which openevolve-run)"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Build baseline binary
# ---------------------------------------------------------------------------
log "Step 1: Building baseline binary"
cd "$SCRIPT_DIR"
make clean 2>&1 | tail -1
make bfs 2>&1 | tail -1

if [ ! -f "$SCRIPT_DIR/bfs" ]; then
  log "ERROR: Build failed — bfs binary not found"
  exit 1
fi
log "  Build successful"
echo ""

# ---------------------------------------------------------------------------
# Step 2: Collect baseline timings
# ---------------------------------------------------------------------------
log "Step 2: Collecting baseline ($BASELINE_RUNS runs per dataset, 85% metrics are deterministic)"

json_entries=()

for entry in "${DATASETS[@]}"; do
  IFS='|' read -r short_name file_prefix <<< "$entry"
  base_file="${DATA_DIR}/${file_prefix}.base.el"
  dynamic_file="${DATA_DIR}/${file_prefix}.dynamic.el"

  if [ ! -f "$base_file" ] || [ ! -f "$dynamic_file" ]; then
    log "ERROR: Dataset files not found for $short_name"
    log "  base:    $base_file"
    log "  dynamic: $dynamic_file"
    exit 1
  fi

  times=()
  # Deterministic metrics (same every run) — extracted from last run
  unset det_metrics 2>/dev/null || true
  declare -A det_metrics
  for ((r=1; r<=BASELINE_RUNS; r++)); do
    output=$(./bfs -B "$base_file" -D "$dynamic_file" -s -n 1 -r 0 2>&1) || {
      log "WARNING: bfs crashed for $short_name run $r (exit=$?), retrying..."
      continue
    }
    t=$(echo "$output" | grep "D-Graph Build Time" | awk '{print $4}' || true)
    if [ -z "$t" ]; then
      log "WARNING: Failed to extract build time for $short_name run $r, skipping"
      continue
    fi
    times+=("$t")
    # Extract all deterministic metrics from METRICS block
    for metric in total_writes total_reads num_write_insert num_read_insert \
                  num_write_rebal num_read_rebal num_write_resize num_read_resize \
                  num_rebalance num_resize; do
      val=$(echo "$output" | grep "^${metric} " | awk '{print $2}' || true)
      if [ -n "$val" ]; then
        det_metrics[$metric]="$val"
      fi
    done
    printf "\r  [%-15s] run %2d/%d  %ss" "$short_name" "$r" "$BASELINE_RUNS" "$t"
  done
  echo ""

  if [ ${#times[@]} -eq 0 ]; then
    log "ERROR: All runs failed for $short_name"
    exit 1
  fi

  # Compute median time
  sorted=($(printf '%s\n' "${times[@]}" | sort -g))
  mid=$(( (${#sorted[@]} - 1) / 2 ))
  median=${sorted[$mid]}

  log "  $short_name median: ${median}s  total_writes: ${det_metrics[total_writes]}"

  # Add time + all deterministic metrics to baseline JSON
  json_entries+=("\"${short_name}_time\": ${median}")
  # Map C++ metric names to evaluator keys
  json_entries+=("\"${short_name}_total_writes\": ${det_metrics[total_writes]}")
  json_entries+=("\"${short_name}_total_reads\": ${det_metrics[total_reads]}")
  json_entries+=("\"${short_name}_write_insert\": ${det_metrics[num_write_insert]}")
  json_entries+=("\"${short_name}_write_rebal\": ${det_metrics[num_write_rebal]}")
  json_entries+=("\"${short_name}_write_resize\": ${det_metrics[num_write_resize]}")
  json_entries+=("\"${short_name}_read_insert\": ${det_metrics[num_read_insert]}")
  json_entries+=("\"${short_name}_read_rebal\": ${det_metrics[num_read_rebal]}")
  json_entries+=("\"${short_name}_read_resize\": ${det_metrics[num_read_resize]}")
  json_entries+=("\"${short_name}_num_rebalance\": ${det_metrics[num_rebalance]}")
  json_entries+=("\"${short_name}_num_resize\": ${det_metrics[num_resize]}")
done

# Write baseline.json
baseline_json="$SCRIPT_DIR/baseline.json"
{
  echo "{"
  for ((i=0; i<${#json_entries[@]}; i++)); do
    if [ $i -lt $(( ${#json_entries[@]} - 1 )) ]; then
      echo "  ${json_entries[$i]},"
    else
      echo "  ${json_entries[$i]}"
    fi
  done
  echo "}"
} > "$baseline_json"

echo ""
log "Baseline saved to: $baseline_json"
cat "$baseline_json"
echo ""

# ---------------------------------------------------------------------------
# Step 3: Launch OpenEvolve
# ---------------------------------------------------------------------------
log "Step 3: Launching OpenEvolve ($ITERATIONS iterations)"

EVOLVE_ARGS=(
  "$SCRIPT_DIR/src/graph.h"
  "$SCRIPT_DIR/evaluator.py"
  --config "$SCRIPT_DIR/config.yaml"
  --output "$OUTPUT_DIR"
  --iterations "$ITERATIONS"
  --log-level INFO
)

# Resume from checkpoint if requested
if [ "$RESUME_FLAG" = "--resume" ]; then
  # Find the latest output directory and its latest checkpoint
  latest_output=$(ls -d "$SCRIPT_DIR"/openevolve_output_* 2>/dev/null | sort | tail -1)
  if [ -n "$latest_output" ]; then
    latest_ckpt=$(ls -d "$latest_output"/checkpoints/checkpoint_* 2>/dev/null | sort -t_ -k2 -n | tail -1)
    if [ -n "$latest_ckpt" ]; then
      OUTPUT_DIR="$latest_output"
      CHECKPOINT_DIR="$OUTPUT_DIR/checkpoints"
      log "  Resuming from: $latest_ckpt"
      EVOLVE_ARGS=(
        "$SCRIPT_DIR/src/graph.h"
        "$SCRIPT_DIR/evaluator.py"
        --config "$SCRIPT_DIR/config.yaml"
        --output "$OUTPUT_DIR"
        --iterations "$ITERATIONS"
        --log-level INFO
        --checkpoint "$latest_ckpt"
      )
    else
      log "  No checkpoint found in $latest_output, starting fresh"
    fi
  else
    log "  No previous output found, starting fresh"
  fi
fi

log "  Initial program: $SCRIPT_DIR/src/graph.h"
log "  Evaluator:       $SCRIPT_DIR/evaluator.py"
log "  Config:          $SCRIPT_DIR/config.yaml"
log "  Output:          $OUTPUT_DIR"
echo ""

openevolve-run "${EVOLVE_ARGS[@]}"

# ---------------------------------------------------------------------------
# Step 4: Print final summary
# ---------------------------------------------------------------------------
echo ""
log "=========================================="
log " Experiment Complete"
log "=========================================="

BEST_INFO="$OUTPUT_DIR/best/best_program_info.json"
if [ -f "$BEST_INFO" ]; then
  log "Best program info:"
  python -c "
import json
with open('$BEST_INFO') as f:
    info = json.load(f)
metrics = info.get('metrics', {})
print(f'  Combined score: {metrics.get(\"combined_score\", \"N/A\")}')
print()
for ds in ['mathoverflow', 'enron', 'amazon', 'stackoverflow']:
    sc = metrics.get(f'{ds}_score', None)
    if sc is None:
        continue
    print(f'  {ds}:')
    print(f'    score: {sc:.4f}')
    for comp in ['write_rebal', 'write_insert', 'num_rebalance', 'time', 'write_resize']:
        imp = metrics.get(f'{ds}_{comp}_improvement', None)
        bl = metrics.get(f'{ds}_baseline_{comp}', None)
        cd = metrics.get(f'{ds}_candidate_{comp}', None)
        if imp is not None:
            bl_s = f'{bl:.4f}' if isinstance(bl, float) and bl < 1000 else f'{bl:.0f}' if bl else 'N/A'
            cd_s = f'{cd:.4f}' if isinstance(cd, float) and cd < 1000 else f'{cd:.0f}' if cd else 'N/A'
            print(f'    {comp:16s}  bl={bl_s:>14s}  cand={cd_s:>14s}  impr={imp:.4f}')
    print()
"
fi

BEST_PROGRAM="$OUTPUT_DIR/best/best_program.h"
if [ -f "$BEST_PROGRAM" ]; then
  log "Best program saved to: $BEST_PROGRAM"
fi

log "Full log: $LOG_FILE"
log "To resume: bash run_experiment.sh $ITERATIONS --resume"
