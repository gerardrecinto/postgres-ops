#!/bin/bash
# Bring the stack up, run every lab, save the output, tear down.
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
docker compose down -v -t 2 >/dev/null 2>&1 || true
docker compose up -d --wait
trap 'docker compose down -v -t 2 >/dev/null 2>&1' EXIT
.venv/bin/python -m labs.run_all | tee /tmp/labs.out
echo "## point-in-time recovery"
docker run --rm --user postgres -v "$PWD/scripts:/scripts:ro" postgres:16 bash /scripts/pitr.sh | tee /tmp/pitr.out
{ cat /tmp/labs.out; echo "## point-in-time recovery"; cat /tmp/pitr.out; } > docs/DEMO_OUTPUT.txt
