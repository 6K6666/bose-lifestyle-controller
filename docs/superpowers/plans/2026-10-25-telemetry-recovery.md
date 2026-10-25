# Telemetry Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this single task inline, with a final independent review.

**Goal:** Recover from failed read-only status polls without a permanent control outage.

**Architecture:** Reuse the bridge's serial-close/reopen loop with a five-second cooldown. Preserve the status schema and existing command fault handling.

**Tech Stack:** Python, existing subprocess/fake-serial tests, Home Assistant status schema 5.

**Spec:** docs/superpowers/specs/2026-10-25-telemetry-recovery-design.md

## Global Constraints

- Automatic recovery applies only to status polling; never replay room commands.
- Keep last valid telemetry timestamps; preserve pairing and configuration.
- A blocked output-drain worker still latches.
- Test with fake serial only; push a public branch only after tests pass. Do not push private repository history.

## Review Focus

- Late query replies must not cross into a control exchange after recovery.
- Repeated failing status requests must respect the five-second cooldown.
- Dirty telemetry refreshes must use the same recovery path.
- Control-command faults must remain latched and never replayed.
- Stalled output drains must not produce overlapping serial workers.

### Task 1: Recover telemetry exchanges

**Files:** Modify bose_bridge.py, docs/fix-a-problem.md, run_tests.sh; create tests/test_bose_telemetry_recovery.py.

**Interfaces:** Existing status snapshot and socket requests remain unchanged. Recovery uses existing health values so the deployed HA validator needs no update.

- [ ] Add subprocess/fake-serial regressions for empty, short, malformed, wrong-query and trailing replies, pre-input, IO failure and partial write, repeated failures, successful polling after cooldown, preserved last readings, dirty refreshes, and unchanged command/drain latching.
- [ ] Run the new suite against the baseline; confirm failure because a telemetry error permanently latches.
- [ ] Change only telemetry exception handling to close the port, log recoverable failure, and defer reopen for five seconds; retain a fault for write-drain-timeout.
- [ ] Run the regression suite and full project test runner; inspect and report failures.
- [ ] Update troubleshooting guidance to distinguish recoverable telemetry failures from uncertain control failures.
- [ ] Obtain an independent code review, fix material findings, then commit and transplant only these public changes to the public repository's current branch baseline.
- [ ] Run the public repository test runner and privacy checks; push a codex/telemetry-recovery branch and verify the remote commit.
