#!/bin/bash
# Benchmark script: runs BFS on all 4 datasets, 10 trials each, reports median.

RUNS=10
DATA_DIR="../data"
DATASETS=(
  "sx-mathoverflow-unique-undir"
  "enron-unique-undir"
  "amazon0601"
  "sx-unique-undir"
)

cd "$(dirname "$0")"
make -j 2>/dev/null

echo "========================================"
echo " VCSR Benchmark — ${RUNS} runs per dataset"
echo "========================================"
echo ""

declare -A ALL_TIMES

for ds in "${DATASETS[@]}"; do
  times=()
  for ((r=1; r<=RUNS; r++)); do
    t=$(./bfs -B "${DATA_DIR}/${ds}.base.el" \
              -D "${DATA_DIR}/${ds}.dynamic.el" \
              -s -n 1 -r 0 2>&1 \
        | grep "D-Graph Build Time" | awk '{print $4}')
    times+=("$t")
    printf "\r  [%s] run %2d/%d  %.4fs" "$ds" "$r" "$RUNS" "$t"
  done
  echo ""

  # sort times numerically
  sorted=($(printf '%s\n' "${times[@]}" | sort -g))

  # median (index 4 for 10 elements, i.e. lower-median)
  mid=$(( (RUNS - 1) / 2 ))
  median=${sorted[$mid]}

  # min / max
  min=${sorted[0]}
  max=${sorted[$((RUNS-1))]}

  # mean
  mean=$(printf '%s\n' "${times[@]}" | awk '{s+=$1} END {printf "%.6f", s/NR}')

  ALL_TIMES["${ds}_times"]="${times[*]}"
  ALL_TIMES["${ds}_median"]="$median"
  ALL_TIMES["${ds}_min"]="$min"
  ALL_TIMES["${ds}_max"]="$max"
  ALL_TIMES["${ds}_mean"]="$mean"
done

echo ""
echo "========================================"
echo " Results (D-Graph Build Time, seconds)"
echo "========================================"
printf "%-35s %10s %10s %10s %10s\n" "Dataset" "Median" "Mean" "Min" "Max"
echo "------------------------------------------------------------------------"
for ds in "${DATASETS[@]}"; do
  printf "%-35s %10s %10s %10s %10s\n" \
    "$ds" \
    "${ALL_TIMES[${ds}_median]}" \
    "${ALL_TIMES[${ds}_mean]}" \
    "${ALL_TIMES[${ds}_min]}" \
    "${ALL_TIMES[${ds}_max]}"
done

echo ""
echo "========================================"
echo " Raw times (all ${RUNS} runs)"
echo "========================================"
for ds in "${DATASETS[@]}"; do
  echo "  $ds:"
  echo "    ${ALL_TIMES[${ds}_times]}"
done
