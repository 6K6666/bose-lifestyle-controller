# Verification evidence

Each file here is the sanitized record of one live check of a command on one M1 (one command, one send, one reply, one observed effect). A command is listed as Verified in [commands](../commands.md) only when a file here matches its entry in the promotion table, with the same hash & banner.

- Only public fields are kept: the command name & bytes, the expected & actual reply, the outcome, the date (no time of day), the M1 banner, the observed effect, the setup note, the health & fault before & after, & the bridge's transmit & receive lines for the send.
- Bridge identifiers, host names, paths, & raw state are never kept.
- A `verified` file promotes its command. A `reply_only` file means the reply was captured but no change was observed, so the command stays Experimental.
- Files are canonical JSON (sorted keys, two-space indent) & their names are `COMMAND-DATE.json`.

The file's sha256 is recorded next to the command in the promotion table, so an edited file no longer matches.
