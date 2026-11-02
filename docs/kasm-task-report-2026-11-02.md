# Kasm repository task report — 2026-11-02

## Task

Validate the Bose Lifestyle Controller's room-status, room-configuration, and fresh-install checks in a real Kasm desktop workspace.

## Run context

- Repository: `6K6666/bose-lifestyle-controller`
- Tested base commit: `97edd7b0d0498cd1b4083318f4fba38d4a1b0b40` (`main` at clone time)
- Kasm workspace: `AI Agent Development`
- Profile mode: disposable / disabled
- Runtime: Python 3.12.3, Git 2.43.0
- Clone path: `/home/kasm-user/bose-sdk2-task-20261102`

## Commands and results

### `python3 -B tests/test_bose_status4.py`

Passed (exit 0). Output confirmed:

- Schema 5 requires the exact key set; invalid and uncertain stores publish null config and empty actions.
- Allowed status actions match live actions after excluding identify and switched-off sources.
- Schema 6 and malformed schema 5 variants are rejected by the shared validator.
- Last-transmission values are checked against command names.
- Unknown fault, recovery, and config values degrade to unknown only.
- Schemas 2 and 3 are rejected; schemas 4 and 5 validate.
- Forced-command exit behavior and status passthrough match the expected cases.
- Actions are disabled on fault or dry-run and enabled only when healthy and live.

### `python3 -B tests/test_bose_rooms.py`

Passed (exit 0). Output confirmed:

- Room names normalize safely; zone state is not read from Home Assistant storage.
- Fault and dry-run reasons report bridge state.
- Schema 4 room changes use `set_zone` then `set_unverified`; errors preserve bridge host codes.
- Setup helpers cover naming, source labels, fresh-install detection, and setup state.
- Stored room names override generic defaults; experimental reasons remain visible until enabled.
- Input nickname/icon fallbacks and IR receiver label rules passed.

### `python3 -B tests/test_bose_fresh_install.py`

Passed: 4 tests in 15.255 seconds.

- CLI-only install routing matches the documented commands.
- Fresh install reaches a configured bridge in a temporary root.
- Rerunning the installer changes nothing.
- Uninstall documentation paths are removable.

## Result

All three focused checks passed. No source changes were needed. The test run did not exercise a physical Bose receiver or Home Assistant instance.

The report is the durable task output. The repository clone was clean after the tests; this report is stored on a separate branch and does not change `main`.
