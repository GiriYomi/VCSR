#!/bin/bash
# ==========================================================================
#  install_tgb.sh — Install TGB + TGX into the dgap_evolve conda env
#
#  Usage:  bash install_tgb.sh
# ==========================================================================
set -euo pipefail

CONDA_ENV="dgap_evolve"

echo "=== Activating conda env: $CONDA_ENV ==="
eval "$(conda shell.bash hook 2>/dev/null)"
conda activate "$CONDA_ENV"
echo "  Python: $(which python) ($(python --version))"
echo ""

echo "=== Installing py-tgb ==="
pip install py-tgb
echo ""

echo "=== Installing py-tgx ==="
pip install py-tgx
echo ""

echo "=== Verifying ==="
python -c "import tgb; print(f'  tgb OK: {tgb.__version__}')" 2>/dev/null || \
python -c "import tgb; print('  tgb OK')"
python -c "import tgx; print(f'  tgx OK: {tgx.__version__}')" 2>/dev/null || \
python -c "import tgx; print('  tgx OK')"

echo ""
echo "Done."
