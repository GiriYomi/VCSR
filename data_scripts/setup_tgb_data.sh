#!/bin/bash
# ==========================================================================
#  setup_tgb_data.sh — One-click: install TGB, download, convert to DGAP format
#
#  Usage:
#    bash setup_tgb_data.sh                                    # default datasets
#    bash setup_tgb_data.sh --datasets tgbl-wiki tgbl-coin     # specific datasets
#    bash setup_tgb_data.sh --list                              # list available
#    bash setup_tgb_data.sh --all                               # all 9 datasets
#    bash setup_tgb_data.sh --sx                                # sx_dir.py processing (default)
#    bash setup_tgb_data.sh --no-sx                             # download_tgb.py processing
#
#  Env: conda activate dgap_evolve
#  Output:
#    data/raw_tgb/           — raw TGB cache
#    data/<name>-sx.base.el  — base graph edges  (sx_dir.py processing)
#    data/<name>-sx.dynamic.el — dynamic edges    (sx_dir.py processing)
# ==========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDA_ENV="dgap_evolve"

# ---- Parse flags ----
USE_SX=true
EXTRA_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --all)
            EXTRA_ARGS+=(--datasets tgbl-uci tgbl-enron tgbl-wiki tgbl-subreddit tgbl-lastfm tgbl-review tgbl-coin tgbl-comment tgbl-flight)
            ;;
        --sx)
            USE_SX=true
            ;;
        --no-sx)
            USE_SX=false
            ;;
        *)
            EXTRA_ARGS+=("$arg")
            ;;
    esac
done

# ---- Activate conda ----
echo "=== Activating conda env: $CONDA_ENV ==="
eval "$(conda shell.bash hook 2>/dev/null)"
conda activate "$CONDA_ENV"
echo "  Python: $(which python)"
echo ""

# ---- Install if needed ----
if ! python -c "import tgb" 2>/dev/null; then
    echo "=== Installing py-tgb ==="
    pip install py-tgb
    echo ""
fi

# ---- Run conversion ----
if [ "$USE_SX" = true ]; then
    echo "=== Processing TGB datasets (sx_dir.py logic, no self-loop removal) ==="
    python "$SCRIPT_DIR/process_tgb_sxdir.py"
else
    echo "=== Downloading and converting TGB datasets (with self-loop removal) ==="
    python "$SCRIPT_DIR/download_tgb.py" "${EXTRA_ARGS[@]}"
fi
