#!/bin/bash
# ==========================================================================
#  bench_all.sh — Benchmark Baseline / Py-in-C++ / C++ Evolved on ALL datasets
#
#  Usage:  bash bench_all.sh [RUNS]    (default 3 runs per dataset)
#
#  Data directories:
#    - DATA_DIR  (default: /mnt/nvme/dataset-dgap/evolve-test) for original datasets
#    - REPO_DATA (default: ../data) for TGB-sx processed datasets
#  Three versions:
#    1) Baseline     — original graph.h
#    2) Py-in-C++    — graph_py.h  (Python-evolved algo translated to C++)
#    3) C++ Evolved  — openevolve best_program.h
#
#  SAFETY: original graph.h is backed up and restored via trap on exit.
# ==========================================================================
set -euo pipefail

RUNS="${1:-3}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="/mnt/nvme/dataset-dgap/evolve-test"
REPO_DATA="$(cd "$SCRIPT_DIR/.." && pwd)/data"
SRC_DIR="$SCRIPT_DIR/src"

GRAPH_H="$SRC_DIR/graph.h"
GRAPH_PY_H="$SRC_DIR/graph_py.h"
BEST_PROGRAM_H="$SRC_DIR/graph_cpp.h"
BACKUP="$SRC_DIR/.graph.h.bench_backup"

# ---- Colors ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---- Auto-detect datasets from multiple directories ----
DATASETS=()        # dataset prefix (used as key)
declare -A DATASET_DIR  # maps prefix -> directory containing the files

add_datasets_from() {
    local dir="$1"
    [ -d "$dir" ] || return
    for f in "$dir"/*.base.el; do
        [ -f "$f" ] || continue
        local prefix
        prefix=$(basename "$f" .base.el)
        local dyn="$dir/${prefix}.dynamic.el"
        if [ -f "$dyn" ] && [ -z "${DATASET_DIR[$prefix]:-}" ]; then
            DATASETS+=("$prefix")
            DATASET_DIR["$prefix"]="$dir"
        fi
    done
}

# Scan primary data dir (original datasets)
add_datasets_from "$DATA_DIR"
# Scan repo data dir (TGB-sx processed datasets — only *-sx files)
for f in "$REPO_DATA"/*-sx.base.el; do
    [ -f "$f" ] || continue
    prefix=$(basename "$f" .base.el)
    dyn="$REPO_DATA/${prefix}.dynamic.el"
    if [ -f "$dyn" ] && [ -z "${DATASET_DIR[$prefix]:-}" ]; then
        DATASETS+=("$prefix")
        DATASET_DIR["$prefix"]="$REPO_DATA"
    fi
done

if [ ${#DATASETS[@]} -eq 0 ]; then
    echo -e "${RED}ERROR: No datasets found in $DATA_DIR or $REPO_DATA${NC}"
    echo "  Expected files like: <name>.base.el and <name>.dynamic.el"
    exit 1
fi

# ---- Versions to benchmark ----
declare -A VERSION_FILES
VERSION_FILES[baseline]="$GRAPH_H"
VERSION_FILES[py_in_cpp]="$GRAPH_PY_H"
VERSION_FILES[cpp_evolved]="$BEST_PROGRAM_H"
VERSION_ORDER=(baseline py_in_cpp cpp_evolved)
VERSION_LABELS=(
    "C++ Baseline"
    "Py-in-C++ (Python Evolved)"
    "C++ Evolved"
)

# ---- Pre-flight checks ----
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD} VCSR Full Benchmark${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""
echo -e "  Data dirs:  ${CYAN}$DATA_DIR${NC}"
echo -e "              ${CYAN}$REPO_DATA${NC} (TGB-sx)"
echo -e "  Runs:       ${CYAN}$RUNS${NC} per dataset"
echo -e "  Datasets:   ${CYAN}${#DATASETS[@]}${NC}"
for ds in "${DATASETS[@]}"; do
    ds_dir="${DATASET_DIR[$ds]}"
    base_size=$(du -h "$ds_dir/${ds}.base.el" | cut -f1)
    dyn_size=$(du -h "$ds_dir/${ds}.dynamic.el" | cut -f1)
    echo -e "    - $ds  (base: ${base_size}, dynamic: ${dyn_size})"
done
echo -e "  Versions:   ${CYAN}3${NC} (Baseline, Py-in-C++, C++ Evolved)"
echo ""

# Check source files exist
for v in "${VERSION_ORDER[@]}"; do
    src="${VERSION_FILES[$v]}"
    if [ ! -f "$src" ]; then
        echo -e "${RED}ERROR: Missing source file for $v: $src${NC}"
        exit 1
    fi
done

# ---- Backup & restore via trap ----
cp "$GRAPH_H" "$BACKUP"
restore_graph_h() {
    if [ -f "$BACKUP" ]; then
        cp "$BACKUP" "$GRAPH_H"
        rm -f "$BACKUP"
        echo ""
        echo -e "${GREEN}[CLEANUP] graph.h restored to original.${NC}"
    fi
}
trap restore_graph_h EXIT

# ---- Helper: compute mean of array ----
mean() {
    printf '%s\n' "$@" | awk '{ s += $1 } END { printf "%.6f", s / NR }'
}

# ---- Results storage ----
# results_time[version|dataset]="t1 t2 t3"
# results_mean[version|dataset]="median_val"
# results_metrics[version|dataset|metric]="val"
declare -A results_time
declare -A results_mean
declare -A results_metrics

METRICS_LIST=(total_writes total_reads num_write_insert num_read_insert num_write_rebal num_read_rebal num_write_resize num_read_resize num_rebalance num_resize)

# ---- Main benchmark loop ----
cd "$SCRIPT_DIR"

for vi in "${!VERSION_ORDER[@]}"; do
    v="${VERSION_ORDER[$vi]}"
    label="${VERSION_LABELS[$vi]}"
    src="${VERSION_FILES[$v]}"

    echo -e "${BOLD}--------------------------------------------${NC}"
    echo -e "${BOLD} Version: ${YELLOW}${label}${NC}"
    echo -e "${BOLD}--------------------------------------------${NC}"

    # Swap graph.h (skip if same file, i.e. baseline)
    if [ "$src" != "$GRAPH_H" ]; then
        cp "$src" "$GRAPH_H"
    fi

    # Build
    echo -n "  Building... "
    make clean > /dev/null 2>&1
    if ! make bfs > /dev/null 2>&1; then
        echo -e "${RED}FAILED${NC}"
        echo "  Build failed for $v, skipping."
        continue
    fi
    echo -e "${GREEN}OK${NC}"

    for ds in "${DATASETS[@]}"; do
        ds_dir="${DATASET_DIR[$ds]}"
        base_file="$ds_dir/${ds}.base.el"
        dyn_file="$ds_dir/${ds}.dynamic.el"

        echo -ne "  ${CYAN}${ds}${NC} "

        times=()
        for ((r=1; r<=RUNS; r++)); do
            output=$(taskset --cpu-list 0-70:2 ./bfs -B "$base_file" -D "$dyn_file" -s -n 1 -r 0 2>&1) || {
                echo -ne "${RED}X${NC}"
                continue
            }
            t=$(echo "$output" | grep "D-Graph Build Time" | awk '{print $4}')
            if [ -z "$t" ]; then
                echo -ne "${RED}?${NC}"
                continue
            fi
            times+=("$t")
            echo -ne "."

            # Extract metrics from last run (deterministic)
            for m in "${METRICS_LIST[@]}"; do
                val=$(echo "$output" | grep "^${m} " | awk '{print $2}')
                if [ -n "$val" ]; then
                    results_metrics["${v}|${ds}|${m}"]="$val"
                fi
            done
        done

        if [ ${#times[@]} -gt 0 ]; then
            avg=$(mean "${times[@]}")
            results_time["${v}|${ds}"]="${times[*]}"
            results_mean["${v}|${ds}"]="$avg"
            echo -e " mean=${BOLD}${avg}s${NC}"
        else
            echo -e " ${RED}ALL RUNS FAILED${NC}"
        fi
    done
    echo ""
done

# ---- Print results ----
echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD} RESULTS${NC}"
echo -e "${BOLD}============================================${NC}"

# ---- Table 1: Runtime ----
echo ""
echo -e "${BOLD}=== Runtime (seconds, mean of $RUNS runs) ===${NC}"
echo ""
printf "%-30s  %12s  %12s  %8s  %12s  %8s\n" \
    "Dataset" "Baseline" "Py-in-C++" "vs BL" "C++ Evolved" "vs BL"
printf "%-30s  %12s  %12s  %8s  %12s  %8s\n" \
    "------------------------------" "------------" "------------" "--------" "------------" "--------"

for ds in "${DATASETS[@]}"; do
    bl="${results_mean[baseline|${ds}]:-N/A}"
    py="${results_mean[py_in_cpp|${ds}]:-N/A}"
    ce="${results_mean[cpp_evolved|${ds}]:-N/A}"

    # compute vs baseline %
    py_vs="N/A"
    ce_vs="N/A"
    if [ "$bl" != "N/A" ] && [ "$py" != "N/A" ]; then
        py_vs=$(awk "BEGIN { printf \"%+.1f%%\", ($py - $bl) / $bl * 100 }")
    fi
    if [ "$bl" != "N/A" ] && [ "$ce" != "N/A" ]; then
        ce_vs=$(awk "BEGIN { printf \"%+.1f%%\", ($ce - $bl) / $bl * 100 }")
    fi

    printf "%-30s  %12s  %12s  %8s  %12s  %8s\n" \
        "$ds" "$bl" "$py" "$py_vs" "$ce" "$ce_vs"
done

# ---- Table 2: Total Writes ----
echo ""
echo -e "${BOLD}=== Total Writes ===${NC}"
echo ""
printf "%-30s  %16s  %16s  %8s  %16s  %8s\n" \
    "Dataset" "Baseline" "Py-in-C++" "vs BL" "C++ Evolved" "vs BL"
printf "%-30s  %16s  %16s  %8s  %16s  %8s\n" \
    "------------------------------" "----------------" "----------------" "--------" "----------------" "--------"

for ds in "${DATASETS[@]}"; do
    bl="${results_metrics[baseline|${ds}|total_writes]:-N/A}"
    py="${results_metrics[py_in_cpp|${ds}|total_writes]:-N/A}"
    ce="${results_metrics[cpp_evolved|${ds}|total_writes]:-N/A}"

    py_vs="N/A"
    ce_vs="N/A"
    if [ "$bl" != "N/A" ] && [ "$py" != "N/A" ]; then
        py_vs=$(awk "BEGIN { printf \"%+.1f%%\", ($py - $bl) / $bl * 100 }")
    fi
    if [ "$bl" != "N/A" ] && [ "$ce" != "N/A" ]; then
        ce_vs=$(awk "BEGIN { printf \"%+.1f%%\", ($ce - $bl) / $bl * 100 }")
    fi

    printf "%-30s  %16s  %16s  %8s  %16s  %8s\n" \
        "$ds" "$bl" "$py" "$py_vs" "$ce" "$ce_vs"
done

# ---- Table 3: Write Breakdown ----
echo ""
echo -e "${BOLD}=== Write Breakdown (rebal / insert / rebalance_count) ===${NC}"
echo ""
printf "%-30s  %-12s  %16s  %16s  %12s\n" \
    "Dataset" "Version" "write_rebal" "write_insert" "num_rebalance"
printf "%-30s  %-12s  %16s  %16s  %12s\n" \
    "------------------------------" "------------" "----------------" "----------------" "------------"

for ds in "${DATASETS[@]}"; do
    first=1
    for v in "${VERSION_ORDER[@]}"; do
        wr="${results_metrics[${v}|${ds}|num_write_rebal]:-N/A}"
        wi="${results_metrics[${v}|${ds}|num_write_insert]:-N/A}"
        nr="${results_metrics[${v}|${ds}|num_rebalance]:-N/A}"

        if [ $first -eq 1 ]; then
            printf "%-30s  %-12s  %16s  %16s  %12s\n" "$ds" "$v" "$wr" "$wi" "$nr"
            first=0
        else
            printf "%-30s  %-12s  %16s  %16s  %12s\n" "" "$v" "$wr" "$wi" "$nr"
        fi
    done
    echo ""
done

# ---- Table 4: Combined Score (0.6*write_ratio + 0.4*time_ratio) ----
echo -e "${BOLD}=== Combined Score (0.6 * BL_writes/cand_writes + 0.4 * BL_time/cand_time) ===${NC}"
echo ""
printf "%-30s  %12s  %12s\n" "Dataset" "Py-in-C++" "C++ Evolved"
printf "%-30s  %12s  %12s\n" "------------------------------" "------------" "------------"

py_sum=0
ce_sum=0
count=0

for ds in "${DATASETS[@]}"; do
    bl_w="${results_metrics[baseline|${ds}|total_writes]:-}"
    py_w="${results_metrics[py_in_cpp|${ds}|total_writes]:-}"
    ce_w="${results_metrics[cpp_evolved|${ds}|total_writes]:-}"
    bl_t="${results_mean[baseline|${ds}]:-}"
    py_t="${results_mean[py_in_cpp|${ds}]:-}"
    ce_t="${results_mean[cpp_evolved|${ds}]:-}"

    py_score="N/A"
    ce_score="N/A"

    if [ -n "$bl_w" ] && [ -n "$py_w" ] && [ -n "$bl_t" ] && [ -n "$py_t" ]; then
        py_score=$(awk "BEGIN { printf \"%.4f\", 0.6 * ($bl_w / $py_w) + 0.4 * ($bl_t / $py_t) }")
        py_sum=$(awk "BEGIN { print $py_sum + $py_score }")
    fi
    if [ -n "$bl_w" ] && [ -n "$ce_w" ] && [ -n "$bl_t" ] && [ -n "$ce_t" ]; then
        ce_score=$(awk "BEGIN { printf \"%.4f\", 0.6 * ($bl_w / $ce_w) + 0.4 * ($bl_t / $ce_t) }")
        ce_sum=$(awk "BEGIN { print $ce_sum + $ce_score }")
    fi

    count=$((count + 1))
    printf "%-30s  %12s  %12s\n" "$ds" "$py_score" "$ce_score"
done

if [ $count -gt 0 ]; then
    py_avg=$(awk "BEGIN { printf \"%.4f\", $py_sum / $count }")
    ce_avg=$(awk "BEGIN { printf \"%.4f\", $ce_sum / $count }")
    printf "%-30s  %12s  %12s\n" "------------------------------" "------------" "------------"
    printf "%-30s  ${BOLD}%12s  %12s${NC}\n" "AVERAGE" "$py_avg" "$ce_avg"
fi

# ---- Raw times ----
echo ""
echo -e "${BOLD}=== Raw Times (all $RUNS runs) ===${NC}"
echo ""
for ds in "${DATASETS[@]}"; do
    echo -e "  ${CYAN}${ds}${NC}:"
    for v in "${VERSION_ORDER[@]}"; do
        raw="${results_time[${v}|${ds}]:-N/A}"
        printf "    %-14s %s\n" "$v" "$raw"
    done
done

echo ""
echo -e "${GREEN}Done. graph.h will be restored on exit.${NC}"
