# Reference

Installers and maintainers can look up commands, configuration, files and paths, status fields, messages and the security model here. Household members do not need this page.

## Conventions

- The command is `bose-lifestyle`. The installer does not put it on your search path. In examples it is run as the full path, `/opt/bose-lifestyle/bin/bose-lifestyle`, which is where the default installer puts it.
- Run it as the bridge user, `bose` by default, so it can read and write the bridge's state folder and reach the command socket:

  ```
  sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
  ```

  Run as root, a change can leave the saved configuration owned by root. The bridge then cannot read it. Use the bridge user.
- Exit codes: `0` means success. `1` means something failed, was canceled (Ctrl-C or a declined save), or the bridge could not confirm a change, and nothing was retried. `2` means the command was refused before anything was sent (a usage error, a name that is not 1 to 40 characters, or a flag that is required for Room D).

## Command line

| Command | What it does |
|---|---|
| `status` | Shows whether the bridge is running, the zones, the inputs, the unverified-command setting and the infrared target. |
| `fault clear` | Clears only the bridge's in-memory fault latch. It sends no Bose bytes and does not test whether the M1 responds. It fails if the bridge is unreachable; there is no offline fallback. |
| `config show` | Prints the saved zones, inputs, unverified settings and infrared target as JSON. |
| `init` | Creates the starting configuration (every room off, no input connected). It refuses to change an existing configuration. |
| `events [--count N]` | Shows the most recent bridge events. `N` is 1 to 200; the default is 20. |
| `zone a on` | Turns on a room zone: `a`, `b`, `c` or `d`. Room D also needs `--yes-i-understand`. |
| `zone a off` | Turns a room zone off. |
| `source aux on` | Says that an input is connected. Inputs: `am`, `fm`, `cd`, `aux`, `video-1`, `video-2`, `tape`. |
| `source aux off` | Says that an input is not connected. |
| `experimental b on` | Allows unverified commands for a zone. Room D also needs `--yes-i-understand`. |
| `experimental b off` | Stops allowing unverified commands for a zone. |
| `volume-steps 3` | Sets how many volume steps one volume up or volume down press sends, 1 to 5 (the default is 1). It applies to Rooms A, B and C, whether the press comes from Home Assistant, the infrared remote or the card. The bridge writes the steps one at a time, a quarter second apart, checks every reply and stops at the first problem. |
| `ir target a` | Chooses the room the infrared remote controls. Forgets the last mute state. |
| `state check` | Says whether `ir-state.json` is readable and, when it is damaged, why and whether the pairing identity can still be read. It changes nothing. |
| `state restore --from BACKUP --yes` | With the bridge stopped, replaces a damaged `ir-state.json` with a backup that loads cleanly. The damaged file is kept beside it. |
| `state reset --keep-identity --yes` or `--new-identity --yes` | With the bridge stopped, starts with no learned remotes. `--keep-identity` keeps the pairing identity read from the damaged file and refuses when it cannot be read with certainty, including when a damaged byte falls inside the identity. `--new-identity` makes a new one and Home Assistant must be paired again. The damaged file is kept beside it. |
| `ir list` | Lists receivers with a stable by-id path, then the learned remotes with their IDs and button state. It never prints codes. Without a running bridge the remotes are not shown. |
| `ir verify PATH` | Listens for 15 seconds and prints `Key codes seen:`, and `Scan codes seen:` when the receiver sends scan codes. It does not teach buttons. |
| `ir select PATH --volume-up N --volume-down N --mute N` | Chooses the receiver device and saves it with three key codes to `ir-config.json`. It does not teach buttons. See [Receivers](receivers.md). |
| `ir learn --name NAME` | Teaches a remote its Volume up, Volume down and Mute buttons, then runs a ready check. The name is 1 to 40 characters. `--yes` skips the prompts. `--technical` also shows raw scan codes. Needs the bridge running. See [Receivers](receivers.md). |
| `ir remote ID on` or `ir remote ID off` | Turns a learned remote on or off. Needs the bridge running. |
| `ir button ID KEY on` or `ir button ID KEY off` | Turns one learned button on or off. `KEY` is `volume-up`, `volume-down` or `mute`. Needs the bridge running. |
| `ir forget ID` | Removes a learned remote from the bridge. Needs the bridge running. |
| `identify --yes` | Sends one manual identify command to a running bridge and prints its reply. This is the only command that sends anything to the Bose unit. Without `--yes`, it prints an explanation and sends nothing. |

Changes made while the bridge is running apply at once. Changes made while it is stopped are saved and apply when it next starts. The command never restarts the service and never edits `config.json` by hand.

## Configuration

The bridge reads one configuration file, `config.json`, in the state folder. It has exactly these top-level parts:

- `zones`: for each room `a`, `b`, `c` and `d`, a flag `enabled` (true or false).
- `sources`: for each input, a true or false flag. True means the input is connected.
- `unverified`: for each room, a true or false flag that allows unverified commands for that room.
- `ir`: `zone`, the room the infrared remote controls (`a`, `b`, `c` or `d`).
- `volume_steps`: optional, a whole number from 1 to 5, how many volume steps one volume up or volume down press sends. It is written only when it is not 1, so a file that never set it keeps the four parts above.

The file also has one marker field that the bridge checks. Leave it as the installer wrote it.

`config show` prints the parts above that are in the file. The bridge refuses a file that is larger than 4096 bytes, contains duplicate keys, has a byte-order mark, or has a value of the wrong type. The bridge sends nothing when it refuses a file. The bridge's status reports `config-invalid`, and the command line says there is no valid saved configuration. Do not edit the file by hand. Use the commands above. See [Update, back up and recover](update-backup-recover.md) if the file is damaged.

## Files and paths

| Path | What it is |
|---|---|
| `/opt/bose-lifestyle/` | The bridge's Python modules and the `bin/bose-lifestyle` command. |
| `/var/lib/bose-control/` | The state folder, owned by the bridge user, mode `0700`. |
| `/var/lib/bose-control/config.json` | The configuration. |
| `/var/lib/bose-control/ir-config.json` | The saved receiver: its path, name and key codes. Mode `0600`. Written by `ir select`. |
| `/var/lib/bose-control/ir-state.json` | The infrared state: the learned remote buttons and the last mute state. |
| `/var/lib/bose-control/events.jsonl` | The event log. When it grows large, the older part is kept as `events.jsonl.1`. |
| `/run/bose-control/command.sock` | The command socket, mode `0600`. It exists only while the bridge runs. |
| `/etc/systemd/system/bose-bridge.service` | The service. |
| `/etc/udev/rules.d/70-bose-receiver.rules` | The receiver rule, if you gave receiver vendor and product. |
| `~bose/.ssh/authorized_keys` | The one restricted key line the installer writes. |

The serial adapter is opened by its path under `/dev/serial/by-id/` at 4800 baud, 8 data bits, no parity and 1 stop bit. The bridge does not open it until it is configured, and it never sends anything at startup.

Home Assistant side:

| Path, in the Home Assistant configuration folder | What it is |
|---|---|
| `custom_components/bose_lifestyle/` | The integration. |
| `.ssh/bose_ed25519` | The private pairing key. Keep it secret. Mode `0600`. |
| `.ssh/bose_known_hosts` | The pinned bridge computer host key. |
| `.storage/bose_lifestyle.rooms` | Room names, input nicknames and icons, the bridge computer nickname, and the infrared receiver label. |

## Status

The `status` command shows whether the bridge runs and the last command transmitted. It does not report current room power, source, volume or mute; the project has no verified query for live room state. `Last command sent` is a command record, not a claim that the room is currently in that state.

- `Bridge: running (live mode); serial port open; fault: none`, or `Bridge: not running (saved configuration)`.
- `Last command sent: room-b-volume-up (transmitted-with-matching-response)` shows the last recorded transmission and its response outcome, or `none recorded` / `unavailable (bridge stopped)` when there is no live transmission record.
- `Unverified commands allowed for:`, `IR target:`, `Volume steps per press:`.

The status returned by the bridge's socket is a JSON object with these parts:

- `bridge`: `mode` (live or dry-run), `serial_open`, `fault` (the reason for a pause, or null), `recovery`, `config` (`ok`, `config-invalid` or `config-uncertain`), `ir_configured`, `ir_attached`, `last_outcome` and `last_transmission`.
- `config`: the four configuration parts, or null if the configuration is not valid.
- `actions`: each command name, its room, its function, whether it is verified, and whether it is allowed now. `allowed` is false for every action while the bridge is faulted or in dry-run, even if the configuration would allow it.
- `ir`: the receiver and remote state.
- `rooms`: power, source, volume and mute for each room. The bridge cannot read these from the Bose unit, so each value is unknown.

## Commands the bridge can send

The bridge sends only catalogued commands. A command is allowed only when its room is turned on, its input is connected (for source commands), and it is either verified or the room allows unverified commands.

The bridge's catalog marks each command as verified or not. [Commands](commands.md) lists every command with its status and how it was verified. Rooms A, B and C have 41 verified commands: power, volume, mute, and every input (AM, auxiliary, Video 1, Video 2, FM, CD and Tape). Room D has no verified commands. Unverified commands are allowed only through `experimental` for the room, and Room D also needs the explicit confirmation.

The identify command is read-only. It is sent only by `identify --yes` to a running bridge.

## Messages

Messages from the command line:

| Message | Meaning |
|---|---|
| The bridge is not running. This is the saved configuration. | The bridge is stopped. Changes are saved and apply at the next start. |
| Can't reach the bridge: run this as the bridge's user or ask the installer | The command could not reach the socket. Run it as the bridge user. |
| There is no valid saved configuration. Ask the person who installed this system for help. | The configuration is missing or refused. |
| No configuration yet. Run: bose-lifestyle init | There is no configuration file. Run `init`. |
| A configuration already exists. Nothing was changed. | `init` found a configuration and left it alone. |
| Saved. / Saved. It applies when the bridge next starts. | The change was saved, now or at the next start. |
| That change wasn't valid. Nothing was changed. | The bridge refused the request. |
| Nothing was changed. The settings could not be saved. | The configuration could not be written. |
| We couldn't confirm the save. Check the configuration with config show before trying again; nothing was retried. | The save may have happened. Check before you change it again. |
| The bridge did not confirm the change. Check the configuration with config show before trying again; nothing was retried. | Same as above, for an unexpected reply. |
| Room D commands are unverified and experimental. Run again with --yes-i-understand to confirm. | Room D needs the explicit flag. Nothing was changed. |
| Warning: unverified commands may not do what the label says. | Shown when you allow unverified commands for Room A, B or C. |
| The last-commanded mute state will be forgotten. | Shown when you change the infrared target. |
| Another learning session is active. | Learning or a change is already in progress. Wait five minutes or finish it, then try again. |
| The learning session ran out of time after five minutes. Nothing was saved. | Run `ir learn` again. |
| Three matching presses have not been verified. / This code belongs to a different button. | Learning rejected a button. Retry it, or skip it. Nothing is saved for that button. |
| No complete set of three matching presses arrived within 30 seconds. | The presses did not arrive in time. Run `ir learn` again. |
| The check was stopped. The remote is saved and its buttons stay disabled. | The ready check ended early. Turn the buttons on with `ir button` and `ir remote`, then test them with the room. |
| The bridge is not running, so nothing was sent. Start the bridge and try again. | `identify --yes` needs a running bridge. |
| The bridge did not identify the controller. Nothing was changed. | The identify reply was not the expected one. |
| Something went wrong. Nothing was changed. | An unexpected error. Nothing was changed. |

Messages from the bridge's command replies, which the card and Home Assistant show in plain words:

| Reply | Plain words shown to people |
|---|---|
| Not allowed (`rejected: not-allowed`) | "That control is turned off." |
| Rejected or rate-limited | "That wasn't sent. Nothing was changed." |
| Uncertain (`uncertain; no retry`) | "We couldn't confirm that command. Check the room before pressing again." |
| Refused by the Bose unit | "The Bose control unit declined that command. Nothing was changed." |
| Paused (fault) | **FAULTED — controls paused; use the P1 remote** |
| Settings not confirmed | "The bridge's settings couldn't be confirmed. Ask the person who installed this system for help." |
| Cannot reach the bridge computer | "Can't reach the bridge computer right now." |

Pairing messages from the Home Assistant integration are listed in [Fix a problem](fix-a-problem.md).

## Security model

- **Restricted SSH.** Home Assistant reaches the bridge computer over SSH with one ed25519 key. The installer writes that key with a forced command: `command="/opt/bose-lifestyle/bose_request.py",restrict`. `restrict` turns off forwarding, terminals and other extras. The command runs only `status`, `events`, `manage`, `fault-clear` and the catalogued Bose commands. Anything else is refused.
- **Source limits.** You can limit which addresses may use the key. Give the installer's `--from` option once for each address or CIDR range. The installer checks each value before writing anything.
- **Home folder.** The key goes in the `.ssh` folder of the account's home folder. The installer uses that home only if it exists and belongs to the account, root or the person running the installer. Give `--allow-custom-home` to use a different home folder, and `--create-home` to create a missing one (mode 0700, owned by the account). The installer does not follow symbolic links for the home folder, `.ssh`, `authorized_keys` or the state folder (`/var/lib/bose-control` under the root being installed).
- **Client side.** Home Assistant runs `ssh` with key-only authentication, the pinned host key, `StrictHostKeyChecking=yes`, one connection attempt, a five-second limit and capped output. It never retries a command.
- **The bridge.** Apart from the loopback diagnostic below, it is the only program that opens the serial port. It sends a command only if the configuration allows it. It never sends anything at startup, never replays a command and never retries one. With `volume_steps` above 1, one volume press is several separate commands, each one checked by the same gate and the same reply check and at least a quarter second after the previous one. A press has a time budget of about three and a half seconds on the bridge computer, measured from its first step: no step is started that could not finish inside it, and the serial operations of each step are limited to the time left, so a slow adapter ends the press early (as uncertain, or with a latched fault if the adapter's reply was cut short). A bridge that is shutting down sends no further steps after the one in progress. The budget is the bridge's own; Home Assistant's five-second request timer also includes SSH set-up. Input that arrives while a multi-step press is running is dropped, not run afterward. The first problem stops the press and it is reported as uncertain: an unexpected reply latches the fault as usual, and a command that cannot be written (the adapter was lost, or the room was switched off mid-press) stops it without a fault. Steps already sent are never undone. If the Bose unit replies in an unexpected way, the bridge pauses its commands until someone investigates.
- **Identify.** Of the supported `bose-lifestyle` commands, only `identify --yes` sends the identify command, and it sends only to a running bridge. The restricted SSH command also accepts the catalogued `identify` action, which Home Assistant does not use.
- **Loopback diagnostic.** `bose_serial.py loopback` is the one other program that can open the serial port. By default it is a dry run; it opens a port only with `--live-loopback`, on the port selected by `--port PATH`, and it is meant for a serial adapter that is not connected to the Bose unit.
- **What is read back.** The bridge checks each command's reply against the reply it expects. It does not read the rooms' power, volume, source or mute from the Bose unit. Those values are shown as unknown.
- **Keys and secrets.** The private pairing key, the pinned host key and the authorized key line are not shown in logs or messages.

## Technical notes

- `ir verify` prints the raw scan codes as `Scan codes seen:`. They arrive as Linux input events of type MSC with code MSC_SCAN (4), before the key code.
- `ir learn --technical` prints each press's scan code as `Scan code:`, and the code mapped to each button.
