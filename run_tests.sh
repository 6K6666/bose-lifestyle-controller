#!/bin/sh
# Run every Bose test. HA-dependent suites run in a throwaway venv, never in production HA.
# Usage: ./run_tests.sh [--stdlib-only] [--venv-python PATH]    PY314=/path/to/python3.14 ./run_tests.sh
# --venv-python reuses an existing HA venv (no venv creation, no pip, no network).
# The release gate also sets BOSE_PRIVATE_LITERALS to the owner's literal list (kept outside the repo).
# Every run deletes build/test-results.json first; a run where every suite passed writes it last
# (mode, HEAD and dirty state at the start, the planned suite count, and the suites that ran).
# tools/release_gate.py reads it and recomputes the expected suites from this file at HEAD, so the
# suite lists below stay plain NAME="..." lines.
set -e
cd "$(dirname "$0")"
export BOSE_RUNNER=run_tests.sh
PY314=${PY314:-python3}
STDLIB_SUITES="test_bose_catalog.py test_bose_promotions.py test_bose_config.py test_bose_config_commit.py test_bose_reply.py test_bose_events.py test_bose_rooms.py test_bose_room_d.py test_bose_ir.py test_bose_ir_target.py test_bose_post_command_state.py test_bose_ir_state_damage.py test_bose_state.py test_bose_ir_learn.py test_bose_scan.py test_bose_bridge.py test_bose_bridge_gate.py test_bose_serve_gate.py test_bose_manage_ops.py test_bose_hardening.py test_bose_serial.py test_bose_serial_port_arg.py test_bose_serial_drain.py test_bose_telemetry_recovery.py test_bose_volume_steps.py test_bose_second_bridge.py test_bose_status4.py test_bose_cli.py test_bose_cli_change.py test_bose_receivers.py test_bose_schema4_only.py test_bose_fresh_install.py test_bose_ha_static.py test_private_scan.py test_build_artifact.py test_release_build.py test_packaging.py test_terminology.py test_neutrality.py test_docs.py test_install.py test_release_gate.py test_set_identity.py test_test_runner.py"
CARD_SUITE="test_bose_card.js"
VENV_SUITES="test_bose_integration.py test_bose_transport.py test_bose_compat.py test_bose_pairing.py test_bose_pairing_flow.py test_build_artifact.py"
MAINTAINER_GLOB="maintainer/*/test_*.py"
MAINTAINER_VENV_GLOB="maintainer/*/check_*.py"   # maintainer suites that need the Home Assistant environment
MODE=full
VENV_PY=
while [ $# -gt 0 ]; do
  case "$1" in
    --stdlib-only) MODE=stdlib ;;
    --venv-python)
      if [ $# -lt 2 ]; then echo "--venv-python needs a path" >&2; exit 2; fi
      VENV_PY=$2
      shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

RESULTS=build/test-results.json
python3 -B tools/results_record.py clear --results "$RESULTS"
STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
HEAD_SHA=$(git rev-parse HEAD)
DIRTY=false
if [ -n "$(git status --porcelain)" ]; then DIRTY=true; fi
SUITES=
PYX=python3
pyt() {
  case "$1" in
    maintainer/*) test_file=$1 ;;
    *) test_file="tests/$1" ;;
  esac
  "$PYX" -B "$test_file"
  SUITES="$SUITES $1"
}
words() { set -- $1; echo $#; }
# Planned count: every suite this mode runs, the card test included whether or not node is present.
PLANNED=$(( $(words "$STDLIB_SUITES") + $(words "$CARD_SUITE") ))
MAINT_COUNT=0
if [ -d maintainer ]; then
  for t in $MAINTAINER_GLOB; do
    if [ -f "$t" ]; then MAINT_COUNT=$((MAINT_COUNT + 1)); fi
  done
fi
PLANNED=$((PLANNED + MAINT_COUNT))
if [ "$MODE" = full ]; then PLANNED=$((PLANNED + $(words "$VENV_SUITES"))); fi
if [ "$MODE" = full ] && [ -d maintainer ]; then
  for t in $MAINTAINER_VENV_GLOB; do
    if [ -f "$t" ]; then PLANNED=$((PLANNED + 1)); fi
  done
fi
record() {
  python3 -B tools/results_record.py write --results "$RESULTS" --mode "$MODE" --started "$STARTED" \
    --head "$HEAD_SHA" --dirty "$DIRTY" --planned "$PLANNED" --suites $SUITES
}

for t in $STDLIB_SUITES; do
  pyt "$t"
done
if [ -d maintainer ]; then
  for t in $MAINTAINER_GLOB; do pyt "$t"; done
fi
if command -v node >/dev/null 2>&1; then
  node tests/test_bose_card.js
  SUITES="$SUITES $CARD_SUITE"
else
  echo "node not found; the required card test cannot run" >&2
  exit 1
fi
if [ "$MODE" = stdlib ]; then
  record
  exit 0
fi

if [ -n "$VENV_PY" ]; then
  PYX=$VENV_PY
else
  venv=$(mktemp -d)
  trap 'rm -rf "$venv"' EXIT
  "$PY314" -m venv "$venv"
  "$venv/bin/pip" install -q -r requirements-ha-test.txt
  PYX="$venv/bin/python"
fi
for t in $VENV_SUITES; do
  pyt "$t"
done
if [ -d maintainer ]; then
  for t in $MAINTAINER_VENV_GLOB; do
    if [ -f "$t" ]; then pyt "$t"; fi
  done
fi
record
echo "All Bose test suites passed"
