# Install

For a fresh install, choose one of two options:

- **With Home Assistant:** Home Assistant pairs with the bridge computer over SSH & shows the room controls.
- **Command line only:** the bridge computer runs the bridge & you control it with `bose-lifestyle` in a terminal. Home Assistant isn't used. You teach an infrared remote's buttons from the command line too (step B8).

Both options use the same installer on the bridge computer. Re-running the installer is safe: it never overwrites an existing configuration, & it never adds a duplicate key line.

Replace values written in capitals, such as `PLACEHOLDER`, with your own. Commands in code blocks run in a terminal on the bridge computer unless the step says otherwise.

## Before you start

- The bridge computer is Linux with systemd & udev, has Python 3.9 or newer, & runs an SSH server. See [Hardware](hardware.md).
- The serial adapter is connected to the bridge computer, & you know its path under `/dev/serial/by-id/`. List the choices with `ls -l /dev/serial/by-id/`.
- A user account exists for the bridge. The default name is `bose`. The installer doesn't create the account, so create it first with your system's tools. The installer stops with a message if the account is missing.
- The project files are on the bridge computer. Download them from the [GitHub releases page](https://github.com/6K6666/bose-lifestyle-controller/releases) & unpack them in a folder of your choice.
- The bridge needs two Python packages: `pyserial` (imported as `serial`) and, if you use a receiver, `evdev`. Install them with your distribution's packages or with pip. The installer doesn't install them, but it checks for them before it writes anything & stops with a message if one is missing. Without them the bridge exits at startup & systemd keeps restarting it.

## Route A: With Home Assistant

### Step A1: Install the integration in Home Assistant

Where: Home Assistant. Choose HACS (recommended) or a manual copy.

This project isn't in the default HACS catalog yet, so you add it to HACS as a custom repository first. It is an integration, not a Home Assistant add-on, so don't look for it in the add-on store. The bridge program runs on the bridge computer (step A3).

**With HACS (recommended)**

1. Install [HACS](https://hacs.xyz/docs/use/download/download/) if you haven't. It's a separate community integration, and its setup needs a free GitHub account.
2. In Home Assistant, open HACS. Select the three-dot menu at the top right, then Custom repositories.
3. In Repository, paste `https://github.com/6K6666/bose-lifestyle-controller`. In Type, choose Integration. Select Add, then close the dialog.
4. Search HACS for "Bose Lifestyle Assistant", open it, and select Download. Accept the latest release.
5. Restart Home Assistant itself; never restart or power-cycle the Bose unit.

A shortcut for steps 2 and 3: [open the repository in your Home Assistant](https://my.home-assistant.io/redirect/hacs_repository/?owner=6K6666&repository=bose-lifestyle-controller&category=integration). It asks HACS to add it for you.

HACS notifies you of new releases. Update from HACS, then update the bridge computer to the same release (see [Update, back up and recover](update-backup-recover.md)).

**Manually**

Copy the `custom_components/bose_lifestyle` folder from the project into the Home Assistant configuration folder, then restart Home Assistant.

Expected: Settings, Devices and services, Add integration, and searching for "Bose Lifestyle Assistant" finds the integration.

If not: with HACS, check that the repository was added as type Integration & that you restarted Home Assistant after the download. For a manual copy, check that the folder is at `custom_components/bose_lifestyle` inside the configuration folder, then restart Home Assistant. Restart Home Assistant itself; never restart or power-cycle the Bose unit.

### Step A2: Start pairing and copy the key command

Where: Home Assistant, Add integration, Bose Lifestyle Assistant.

Expected: the screen "Pair the bridge computer" shows a command that starts with `sh install-bridge.sh --public-key`. Keep this screen open. Use its public key in step A3. The displayed command has placeholders for the user and the serial path, and it has no `sudo` and no receiver options. Use the command in step A3, which includes all of them.

If not: if Home Assistant shows "The ssh program isn't available" or "The ssh-keyscan program isn't available", read [Home Assistant without ssh](#home-assistant-without-ssh) before you continue. Other messages are explained in [Fix a problem](fix-a-problem.md).

### Step A3: Run the installer on the bridge computer

Where: the bridge computer, in the `deploy` folder of the project files.

Expected: the output includes a line that starts with `Created /var/lib/bose-control/config.json`, and the heading `Bridge host key fingerprint (compare it with the setup screen):` with a fingerprint line under it. The line `Next step: sudo systemctl enable --now bose-bridge.service` comes near the end, and the final line is `installed under /opt/bose-lifestyle`. Keep the fingerprint: you need it in step A6.

```
cd PATH/TO/bose-lifestyle/deploy
sudo sh install-bridge.sh --public-key 'PASTE-THE-PUBLIC-KEY-FROM-STEP-A2' --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0
```

If not: the installer prints one line that starts with `install-bridge:` and names the problem. Fix that problem and run the same command again. A second run does not damage anything already written.

### Step A4: Set up the receiver (only if you have one)

Where: the bridge computer. Skip this step if you do not use an infrared receiver.

Expected: the receiver is saved before the bridge starts. The commands below run with `-g RECEIVER-GROUP` because the bridge's service user takes the receiver's group from its service settings. Do these in order, following [Receivers](receivers.md) for the details: first `ir list` to find the receiver's path, then `ir select` to choose it (this only chooses the device; it does not teach buttons), then run the installer again with the receiver options, which adds the receiver to the service and writes its rule. The receiver commands need the receiver's group, shown as `RECEIVER-GROUP` below; see [Receivers](receivers.md) for how to find it.

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir list
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir select /dev/input/by-id/usb-PLACEHOLDER-event-kbd --volume-up 115 --volume-down 114 --mute 113
sudo sh install-bridge.sh --public-key 'PASTE-THE-PUBLIC-KEY-FROM-STEP-A2' --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0 --receiver-device /dev/input/by-id/usb-PLACEHOLDER-event-kbd --receiver-vendor 0000 --receiver-product 0000
```

Replace `0000` with the receiver's vendor & product IDs, & use the receiver path from `ir list`. Save the receiver before you start the bridge, because the service expects the saved receiver file at startup. Then step A9 teaches the remote's buttons (or step B8 on the command line route).

If not: if `ir select` says `Only a receiver with a /dev/input/by-id/ path` or `That device does not report key presses`, pick a receiver from `ir list`. See [Receivers](receivers.md).

### Step A5: Start the bridge

Where: the bridge computer.

Expected: `systemctl status` shows `active (running)`. The status command then reports `Bridge: running (live mode)` and a serial port line. The bridge sends nothing to the Bose unit when it starts.

```
sudo systemctl enable --now bose-bridge.service
systemctl status bose-bridge.service
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
```

If not: if the service is not active, run `journalctl -u bose-bridge.service -n 50` and look for the last error. If the status command says `The bridge is not running`, the service did not start. Both cases are covered in [Fix a problem](fix-a-problem.md).

### Step A6: Finish pairing in Home Assistant

Where: Home Assistant, on the Bose Lifestyle Assistant pairing screens from step A2. The bridge must be running (step A5) before you finish this step.

Expected: enter the bridge computer's address, the SSH user (`bose` by default) and the SSH port (22 is usual). Home Assistant then shows a fingerprint. Compare it with the fingerprint line from step A3. The integration is created when the two match.

Type only the `SHA256:` value from the installer's fingerprint line into the field. The installer prints the full `ssh-keygen` line, with a bit count before the value & a key type after it. Only the part that starts with `SHA256:` matches what Home Assistant computes. The check is exact. An extra character stops pairing with a fingerprint mismatch.

If the installer printed `Bridge host key fingerprint: on the bridge host run ssh-keygen -lf` instead of a fingerprint line, the bridge computer has no ed25519 host key file. Run `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the bridge computer & use the `SHA256:` value from its output.

If not: a mismatch means nothing was saved. Run the fingerprint check again from step A3 and try once more. A connection error means the bridge computer is not reachable or the bridge is not running. See [Fix a problem](fix-a-problem.md).

### Step A7: Add the card to a dashboard

Where: Home Assistant, a dashboard in edit mode.

Expected: the card is listed as "Bose Lifestyle Assistant" in the card picker. A Home Assistant restart and a dashboard reload may be needed before it appears. Add it, or add a card with this YAML:

```
type: custom:bose-control-card
```

The card takes no YAML options. It ignores any other setting in the card configuration, so do not add a title or an entity list. Room names, inputs, & the room the infrared remote controls are set from the card's own settings screens. The remote's buttons are taught in Home Assistant with Add remote (step A9), or on the command line with `ir learn`.

If the card is already on the dashboard, continue.

If not: if Bose Lifestyle Assistant is missing from the picker, check that step A1 finished, then restart Home Assistant. The integration adds the card file to the browser when Home Assistant starts, so reload the dashboard page after the restart. If it still does not appear, check the Home Assistant log for a warning from the Bose Lifestyle Assistant integration.

### Step A8: Set up rooms and inputs

Where: Home Assistant, the Bose Lifestyle Assistant dashboard. On a fresh install, a Finish setup button is shown to administrators. Otherwise use the settings control, then Set up rooms and inputs.

Expected: the screen "Rooms and inputs" lists each room with a name field and an "Enable app control for this room" switch. It also lists each input with a switch named after its jack, a nickname field and an icon. Turn on the rooms you have and connect the inputs they use. The screen says that only connected inputs appear in the daily controls. Room on/off and connected inputs are stored on the bridge computer. Names, nicknames and icons are stored in Home Assistant.

If not: a message that starts with "Nothing was changed" means the save was refused, so try again. A message that says "We couldn't confirm that change" means the save may have been stored: check the settings before you change them again. See [Fix a problem](fix-a-problem.md).

### Step A9: Teach the remote's buttons (only if you have a receiver)

Where: Home Assistant, on the Bose Lifestyle Assistant integration, choosing Add remote.

Expected: Home Assistant asks you to name the remote and then walks through its buttons. Volume up, Volume down and Mute are the only buttons that can be learned. For each button, point the remote at the receiver, select Listen, and press and release the button three times about one second apart. Learning sends no Bose commands. When you save, the remote and its learned buttons start disabled. Open Configure remote, turn on the remote's Enabled switch, then enable one learned button at a time and test each one with the room.

If not: "Three matching presses have not been verified" means the presses did not match. Try again, pressing one button three times in a row. "This code belongs to a different button" means two buttons were pressed as one. Learn the buttons again, one at a time. Until a button is enabled, the remote does nothing for it.

The command line teaches the same buttons with the same steps (step B8). See [Receivers](receivers.md) for how presses are matched.

### Step A10: Check one room

Where: Home Assistant, the room you turned on in step A8, and the room itself.

Expected: turn the room on with the card, and the Bose unit responds as it does to its own remote. The card shows a "Last sent" label after a successful press.

If not: messages on the card are listed in [Everyday use](everyday-use.md) and [Fix a problem](fix-a-problem.md). Do not press the same control repeatedly.

## Route B: Command line only

### Step B1: Check the bridge user

Where: the bridge computer.

Expected: `id bose` prints the user's IDs.

```
id bose
```

If not: create the user with your system's tools, then run this step again.

### Step B2: Create a key pair for the installer

Where: the bridge computer, in your own home folder.

Expected: two files are created: `bose-cli-key` and `bose-cli-key.pub`. The installer needs a public key, but the command line route does not use the SSH path, so any key you generate for this purpose is fine. Remove these two files when you uninstall (see [Uninstall](uninstall.md)).

```
ssh-keygen -t ed25519 -N "" -f ~/bose-cli-key
```

If not: install the OpenSSH client with your system's tools, then run this step again.

### Step B3: Run the installer

Where: the bridge computer, in the `deploy` folder of the project files.

Expected: the same output as step A3, including the fingerprint line and the `Next step` line. Ignore the fingerprint on this route.

```
cd PATH/TO/bose-lifestyle/deploy
sudo sh install-bridge.sh --public-key "$(cat ~/bose-cli-key.pub)" --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0
```

If not: read the line that starts with `install-bridge:`, fix the problem and run the command again.

The installer refuses a home folder that doesn't exist, & a home folder that isn't the account's own, unless you add `--create-home` (creates a missing home folder) or `--allow-custom-home` (confirms a different one). Both are rarely needed.

### Step B4: Check the saved configuration

Where: the bridge computer.

Expected: the bridge is not running yet, so the output starts with `Bridge: not running (saved configuration)`. Every room is off and every input is off.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
```

If not: if it says `Can't reach the bridge` or `There is no valid saved configuration`, check that step B3 finished and that you ran the command as the bridge user. See [Fix a problem](fix-a-problem.md).

### Step B4a: Create the starting configuration (only if status says "No configuration yet")

Where: the bridge computer. The installer creates the configuration, so skip this step unless step B4 reported that no configuration exists.

Expected: `Created the starting configuration: every room is off and no source is connected.`

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle init
```

If not: `A configuration already exists. Nothing was changed.` means you can skip this step.

### Step B5: Turn on a room and connect an input

Where: the bridge computer. Use a room and input you have. These examples use room A and the auxiliary input.

Expected: each command prints `Saved. It applies when the bridge next starts.` before the bridge runs. If the bridge is already running, the line says `Saved.` and the change applies at once.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle zone a on
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle source aux on
```

Room D isn't turned on by these steps. Room D is unverified & needs an explicit confirmation. See [Reference](reference.md) before you enable it.

If not: `Nothing was changed` means the settings could not be saved. Check the message and run the command again. Exit code 2 means the command was refused before anything was sent.

### Step B6: Save the receiver (only if you have one)

Where: the bridge computer, before you start the bridge in step B7. Skip this step if you do not use an infrared receiver.

Expected: `ir select` prints `Saved receiver` followed by the name and path, says that it chooses the receiver only, and names the next step, `ir learn`. `ir target` prints `Saved.` with the mute warning. The receiver's group is shown as `RECEIVER-GROUP` below; see [Receivers](receivers.md).

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir list
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir verify /dev/input/by-id/usb-PLACEHOLDER-event-kbd
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir select /dev/input/by-id/usb-PLACEHOLDER-event-kbd --volume-up 115 --volume-down 114 --mute 113
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir target a
sudo sh install-bridge.sh --public-key "$(cat ~/bose-cli-key.pub)" --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0 --receiver-device /dev/input/by-id/usb-PLACEHOLDER-event-kbd --receiver-vendor 0000 --receiver-product 0000
```

Run the installer last, to add the receiver to the service. Replace `0000` with the receiver's IDs.

**The remote's buttons are not taught yet.** Saving the receiver only chooses the device. Teach the buttons in step B8, after the bridge starts in step B7. The receiver & target room stay saved, so a later move to Home Assistant needs only its Add remote step.

If not: if `ir select` says `That device does not report key presses`, pick a receiver from `ir list`. See [Receivers](receivers.md).

### Step B7: Start the bridge

Where: the bridge computer.

Expected: `systemctl status` shows `active (running)`, and the status command now reports `Bridge: running (live mode)`. Room A and the auxiliary input show `on`.

```
sudo systemctl enable --now bose-bridge.service
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
```

If not: run `journalctl -u bose-bridge.service -n 50` and see [Fix a problem](fix-a-problem.md).

### Step B8: Teach the remote's buttons (only if you have a receiver)

Where: the bridge computer, while the bridge is running (step B7). Skip this step if you do not use an infrared receiver.

Expected: the command first says that other remotes you have set up stop working while you learn this one. It then asks for each button in turn. Point the remote at the receiver, press Enter to listen, then press and release the button three times about one second apart. Held-button repeats do not count. `Three matching presses verified.` appears for each button, then you keep it. The review lists each button as Learned or Skipped, and you type `y` to save. A ready check then asks you to press each learned button once. Learning sends no Bose commands. The saved buttons start disabled; step B9 turns them on.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir learn --name "Den remote"
```

If not: press Ctrl-C to stop. Before the review, Ctrl-C cancels and nothing is saved. Nothing is saved until you type `y` at the review; after that, Ctrl-C only stops the ready check, and the remote stays saved with its buttons off. "Three matching presses have not been verified" or "No complete set of three matching presses arrived" means the presses did not match: run the command again and press the button three times in a row. "This code belongs to a different button" means two buttons were pressed as one: learn again, one button at a time. "The bridge is not running" means step B7 is not finished. Add `--yes` to skip the prompts only when you have already tried the steps.

### Step B9: Turn on the buttons (only if you taught any)

Where: the bridge computer, while the bridge is running. A new remote and its buttons start off, so turn on one button at a time and test it with the room before you turn on the next.

Expected: `ir list` shows the remote with its ID, `remote off`, and each button as `learned, off` or `not learned`. After you turn a button on and the remote on, that button shows `learned, on`. Changes apply at once.

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir list
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir button REMOTE-ID volume-up on
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir remote REMOTE-ID on
```

Replace `REMOTE-ID` with the ID that `ir list` shows. Press the button on the remote & check the room before you turn on the next one.

If not: "That is not a remote ID" means the ID was mistyped: copy it from `ir list`. "That button has no saved code" means the button was skipped: learn the remote again in step B8. "The bridge is not running, so nothing was changed" means start the bridge in step B7 first.

### Step B10: Check the bridge

Where: the bridge computer.

Expected: `status` shows the zones and inputs you set, and `events` lists recent start and serial events.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle events --count 10
```

If not: read [Fix a problem](fix-a-problem.md), which has the installer section for these messages.

## Home Assistant without ssh

The Home Assistant integration pairs with the bridge computer by running two programs inside the Home Assistant environment: `ssh` & `ssh-keyscan`. If either program is missing, pairing stops at its first step with a message that names the missing program & points to this section.

The integration checks for both programs when pairing starts. It can't pair without them, & it doesn't pair through any other program.

To install the missing programs:

1. Check which Home Assistant installation type you use. Some installation types do not let you install an extra program. Check that type's documentation for installing the OpenSSH client.
2. If your installation lets you install an OpenSSH client, install it in the same environment where Home Assistant runs. On many Linux systems, the OpenSSH client package provides both programs.
3. If your installation doesn't let you add programs, use the route B command line steps above. The bridge works without Home Assistant, & you configure it with `bose-lifestyle`. That command doesn't send power, source or volume commands, so daily control without Home Assistant needs the infrared remote route (volume & mute only) or your original Bose remote. You can add Home Assistant later.

After you install the client, restart Home Assistant & start pairing again from step A2. Restart Home Assistant itself, never the Bose unit.
