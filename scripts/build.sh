#!/bin/bash
set -e

# CENTRAL build script
# This script checks the health of the CENTRAL codebase

echo "Running CENTRAL build script..."

# 1. Smoke test - basic Python syntax check on key scripts
echo "Step 1: Smoke test - syntax validation"
python3 -m py_compile scripts/central_task_db.py
python3 -m py_compile scripts/central_runtime.py
python3 -m py_compile scripts/dispatcher_control.py
echo "✓ Python syntax validation passed"

# 2. Basic CLI command validation
echo "Step 2: CLI command validation"
python3 scripts/central_task_db.py --help > /dev/null
python3 scripts/central_runtime.py --help > /dev/null
python3 scripts/dispatcher_control.py --help > /dev/null
echo "✓ CLI commands functional"

# 3. Database schema validation (if DB exists)
echo "Step 3: Database validation"
if [ -f "db/central.db" ]; then
    python3 scripts/central_task_db.py view-summary > /dev/null
    echo "✓ Database connectivity and basic queries working"
else
    echo "✓ No database found, skipping DB checks"
fi

echo "✅ CENTRAL build completed successfully"