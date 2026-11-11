#!/usr/bin/env bash
# The gates every worker runs. Standard library only.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d kit ]; then python3 -m compileall -q kit; fi
if ls tests/test_*.py >/dev/null 2>&1; then python3 -m unittest discover -s tests -p 'test_*.py'; fi
echo "GATES OK"
