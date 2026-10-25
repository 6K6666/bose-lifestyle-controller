# Recover from missed status replies

The bridge must recover from a failed read-only volume or input query without an operator clearing a permanent fault. The owner chose automatic recovery for status polling only; control commands retain their existing uncertain-result latch and are never replayed.

## Approach

Close the serial handle after a failed status exchange and let the existing adapter loop reopen it after five seconds. Reopening clears the old receive buffer before another exchange. Keep the last valid readings and their original timestamps so they age normally. Log the failed exchange as a recoverable serial event. Repeated failures repeat this bounded cooldown rather than permanently disabling controls.

Immediately retrying on the same handle risks mixing a late reply with another exchange. Clearing every fault would also relax control-command safety. Neither is needed.

An output drain that remains blocked in its worker thread still latches: reopening while an old operation is running is not safe. This is a transport failure requiring investigation, not an ordinary missing or malformed status reply.

## Scope and verification

Change only bridge telemetry recovery and its documentation/tests; preserve the status schema, pairing, permissions, room controls, and HA integration. Tests use fake serial ports to cover empty, malformed, partial and delayed replies, repeated failures, successful recovery, and unchanged control-command and stalled-drain latching. No physical commands are part of verification.

Prepare and test the patch locally, then deploy the bridge file with a backup and one bridge-service restart when authorized. Verify normal polling and HA health separately from physical control acceptance.
