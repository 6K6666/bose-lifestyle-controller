# Fix a problem

Find the symptom, then read the section for your role. The household section uses only the daily controls. The installer section uses the command line and the service logs.

## Household

These steps need no settings and no command line.

| What you see | What to do |
|---|---|
| A room does not respond to the card, but the original Bose remote works. | Check that the room appears on the card. A room that is turned off is not shown on the daily card; ask the installer to turn it on in the card's settings. |
| **Not sent** appears on a control. | Wait a moment, then press once. Presses are not queued. |
| "We couldn't confirm that command. Check the room before pressing again." | Check the room. Do not press the same control until you have. |
| "Can't reach the bridge computer right now." | Wait a few minutes and try again. If it continues, ask the installer. |
| **FAULTED — controls paused; use the P1 remote** | Use the P1 remote. Ask the installer to check the bridge before resuming app control. |
| "The Bose control unit declined that command. Nothing was changed." | The unit refused the command. Use the original remote to check the room. |
| "The bridge's settings couldn't be confirmed. Ask the person who installed this system for help." | Use the original remote. Ask the installer for help. |
| An input is missing from the card. | It is not connected. Ask the installer to connect it. |
| The infrared remote does nothing. | Ask the installer to check which room the remote controls (the card's settings show it under **Infrared remote**: "This remote controls"), and to check that the remote's buttons are taught and turned on. |
| The infrared remote's Mute press did the wrong thing after a restart. | The bridge forgets the last mute state when it restarts, so the next Mute press sends Mute. Check the room. |

Clearing the fault latch removes only the bridge's in-memory stop. It sends no Bose command and does not test whether the M1 responds. Do not repeat a press that may not have been sent.

## Installer

Read the messages with the commands in [Reference](reference.md). Run the command line as the bridge user, `sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle`. Never restart or power-cycle the Bose unit to fix a problem. Restart only the bridge service, and only where a step says so.

### The bridge is not running

Where: the bridge computer.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
systemctl status bose-bridge.service
journalctl -u bose-bridge.service -n 50
```

Look for these causes in the journal:

- **A missing Python package.** An error about `serial` or `evdev` means the package is not installed. Install `pyserial` and, with a receiver, `evdev`, then start the service again. The installer checks for both before it writes anything, so a run that stopped with `the Python package ... is not installed` changed nothing.
- **A missing receiver file.** If the installer was given receiver options, the service expects `ir-config.json`. Save the receiver with `ir select` before you start the service. See [Receivers](receivers.md).
- **A restart loop.** If the service keeps restarting, the journal shows the same error each time. Fix that error first.
- **A damaged remote state file.** The journal says `IR state file ... is damaged` and the service stops with status 20 instead of restarting. The file, `ir-state.json`, holds the bridge's pairing identity and the learned remotes, so the bridge never overwrites it. Check it with `sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle state check`. To recover, stop the service, then either restore a backup with `state restore --from BACKUP --yes`, or start over with `state reset --keep-identity --yes`, which keeps Home Assistant paired and forgets the learned remotes, or `state reset --new-identity --yes`, which also needs Home Assistant to be paired again. Every recovery keeps a copy of the damaged file next to it. Then start the service.

Start the service with `sudo systemctl enable --now bose-bridge.service` once the cause is fixed.

### The remote does nothing

Work through these in order. Each command runs on the bridge computer, as the bridge user.

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir list
```

The receiver group is needed here because `ir list` also checks the receiver. Without it, the receiver can look missing even when it is saved.

- **No learned remote is listed.** The buttons were never taught. Teach them with `ir learn --name NAME` (see [Receivers](receivers.md)), or in Home Assistant with Add remote.
- **The remote is listed with `remote off`.** Turn it on with `ir remote ID on`, then turn on each button you want with `ir button ID KEY on`.
- **A button shows `not learned`.** That button was skipped. Learn the remote again.
- **Everything is on, but nothing happens.** Check that the receiver is saved and that the bridge is running. The bridge matches presses only for buttons that are taught and turned on.

The receiver is listed and the bridge runs, but the remote still does nothing: run `ir verify` with the receiver's path and press a button. Key codes shown there mean the receiver works. Learning maps buttons; verify does not.

### The serial port is closed

`status` shows `serial port closed`, and `events` shows `serial closed` or `serial port not configured`.

- **Not configured.** The installer was run without `--serial-device`. Run it again with the adapter's by-id path. The installer does not change the saved configuration.
- **Configured but not open.** The bridge tries to open the exact path every few seconds. Check that the adapter is connected, and that its path under `/dev/serial/by-id/` is the same as in the service. Run `ls -l /dev/serial/by-id/` to see the current name.

### A pause is latched

`status` shows a fault, and `events` shows a `fault` entry. The bridge stopped sending commands after an unexpected reply or a serial failure, so the household sees the pause message.

1. Read `events` and the journal for the fault entry. Do not send commands from the card while you investigate.
2. Check the Bose unit with the original remote, and check the cable and the adapter.
3. After the investigation, clear only the bridge's in-memory fault latch. In Home Assistant, open the Controller device and press **Clear fault latch**, or run `bose-lifestyle fault clear` on the bridge computer.

Clearing sends no Bose bytes, does not retry a command, and does not verify that the M1 responds. If the bridge is unreachable, clearing fails without an offline fallback. The bridge's existing loop continues trying to reopen the serial port. Check `status`, but verify bridge-to-M1 communication separately before relying on app controls. If the fault returns, stop and ask for help.

Restart the bridge service only for separate service maintenance: `sudo systemctl restart bose-bridge.service`. This also resets the in-memory latch, but does not verify M1 communication. Never restart or power-cycle the Bose unit.

### Messages from the command line

| Message | Cause and fix |
|---|---|
| Can't reach the bridge: run this as the bridge's user or ask the installer | Run the command as the bridge user, with `sudo -u bose`. |
| The bridge is not running. This is the saved configuration. | The changes you make now are saved and apply at the next start. Start the service when you are ready. |
| There is no valid saved configuration. Ask the person who installed this system for help. | The configuration file is missing or refused. See [Update, back up and recover](update-backup-recover.md). |
| No configuration yet. Run: bose-lifestyle init | Run `init` (as the bridge user). It creates the starting configuration. |
| A configuration already exists. Nothing was changed. | Nothing is wrong. The configuration is already there. |
| Room D commands are unverified and experimental. Run again with --yes-i-understand to confirm. | Room D needs the flag, and only after you have read the warning. |
| The bridge is not running, so nothing was sent. Start the bridge and try again. | `identify --yes` only works with a running bridge. |
| Something went wrong. Nothing was changed. | Read `journalctl -u bose-bridge.service`, then try again. |
| We couldn't confirm the save. Check the configuration with config show before trying again; nothing was retried. | The save may have happened. Run `config show` before you change anything else. |

### Receiver messages

| Message | Cause and fix |
|---|---|
| That receiver path was not found. | The path is wrong. Run `ir list` and copy the path it shows. |
| This user cannot read the receiver. | Run the command with the receiver's group added, as shown in [Receivers](receivers.md). |
| The key reader (evdev) is not installed on this bridge host. | Install `evdev`. |
| Only a receiver with a /dev/input/by-id/ path can be saved. | Pick a path from `ir list`. |
| That device does not report key presses. | Pick a receiver from `ir list`. |
| The receiver was unplugged or stopped responding. Nothing was changed. | Check the connection to the receiver, then run the check again. |
| Nothing was changed: no key presses seen in 15 seconds. | Run `ir verify` again and press each button once within the 15 seconds. |
| Another learning session is active. | Finish or cancel it, or wait five minutes, then run `ir learn` again. |
| The learning session ran out of time after five minutes. Nothing was saved. | Run `ir learn` again. |
| This code belongs to a different button. | Retry that button, or skip it. Learn one button at a time. |
| No complete set of three matching presses arrived within 30 seconds. | Run `ir learn` again and press the button three times in a row. |
| That is not a remote ID. | Copy the ID from `ir list`. |
| That button has no saved code. | Learn the remote again; that button was skipped. |
| The bridge is not running, so nothing was changed. | Start the bridge, then run the command again. |

### Home Assistant pairing messages

These messages appear in the Bose Lifestyle Assistant setup in Home Assistant. Nothing is saved when a message says "Nothing was saved" or "No command was retried".

| Message (short form) | Cause and fix |
|---|---|
| The ssh program isn't available | Home Assistant cannot run `ssh`. See [Home Assistant without ssh](install.md#home-assistant-without-ssh). |
| The ssh-keyscan program isn't available | Home Assistant cannot run `ssh-keyscan`. See [Home Assistant without ssh](install.md#home-assistant-without-ssh). |
| Home Assistant can't read the saved pairing key | Check the permissions of the `.ssh` folder in the Home Assistant configuration folder, then start pairing again. |
| Home Assistant can't write to its configuration folder | Check the folder's permissions, then start pairing again. |
| A pairing key exists but Home Assistant couldn't reuse it | Remove the `bose_ed25519` file from the `.ssh` folder in the Home Assistant configuration folder, then start pairing again. This creates a new key, so you must run the installer again with the new public key. |
| The fingerprint you typed doesn't match | Nothing was saved. Compare the `SHA256:` value from the installer's output with the one on the screen, then try again. |
| Home Assistant can't reach the bridge computer, or it doesn't accept this key yet | Check that the installer finished, that the bridge service is running and that the address and user are right. |
| The bridge computer answered in a way the bridge doesn't expect | Run the full installer command exactly as shown, then try again. |
| Home Assistant couldn't read the bridge computer's host key | Check the address, that the bridge computer is on, and that its SSH service is running. |
| Enter the bridge computer's host name or IP address | Enter the address only, with no spaces. |
| Enter the SSH user the installer created | Use the account you passed to the installer with `--user`. |
| Enter a port number from 1 to 65535 | Use 22 unless your SSH service uses another port. |

### Installer messages

The installer prints each problem on one line that starts with `install-bridge:`. The most common ones are these.

| Message | Fix |
|---|---|
| `--public-key is required` | Copy the public key from the Home Assistant setup screen, or from your generated key pair. |
| `--serial-device must be a path under /dev/serial/by-id/` | Use the adapter's by-id path. |
| `serial device ... not found` | The path does not exist. Run `ls -l /dev/serial/by-id/`. |
| `user ... does not exist` | Create the account, or pass `--user` with the account you have. |
| `the Python package pyserial is not installed` | Install `pyserial` with your system package manager or pip, then run the installer again. Nothing was written. |
| `the Python package evdev is not installed` | Install `evdev` with your system package manager or pip, then run the installer again. It is needed only with a receiver. Nothing was written. |
| `Python 3.9 or newer is required` | Install a newer Python. |
| `refusing to write through a symbolic link` | A path the installer writes is a link. Replace the link with a real file or folder, then run again. |
| `the public key or --from value was refused` | Check the key is a single ed25519 public key line. Check each `--from` value is an address or a CIDR range. |
| `config.json left unchanged` | Nothing is wrong. The configuration already exists and the installer keeps it. |
| `Missing: no --serial-device given` | The bridge cannot find its serial adapter. Run the installer again with `--serial-device`. |

Running the installer again is always safe.

### Checking events

`events` shows the last recent entries, with the time, the kind, the action and the detail. Kinds include `start`, `serial`, `ir`, `manage`, `outcome`, `tx`, `rx`, `refused` and `fault`.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle events --count 50
```
