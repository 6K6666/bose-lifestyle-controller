# Infrared receivers

An infrared (IR) receiver lets a remote control send its buttons to the bridge. A receiver is optional. The bridge does not depend on one model: it works with any Linux input device that reports key presses and that has a stable path under `/dev/input/by-id/`.

Receiver identity is stored only in the bridge's `ir-config.json`. No receiver brand is built into the software.

Known-working example: HP Media Center IR Receiver, model OVU400103/00 (infrared receiver sensor, silver). This is one example, not a requirement.

## The by-id contract

A receiver can be used only when all of these are true:

- The device reports key presses to Linux.
- It has a path under `/dev/input/by-id/`. This path does not change when you plug the device into another port.
- The bridge computer's user can read it, through the group that owns the device.

A device without a by-id path is listed as unsupported. Find out whether your receiver creates one with `ir list`.

## Find and check the receiver

Where: the bridge computer. Run these as the bridge user, with the receiver's group added for the command. The bridge's service user takes the receiver's group from its service settings, so these commands run with the same group: `-g RECEIVER-GROUP` on `list`, `verify`, `select` and `learn`. Find the group that owns the device with `ls -l` on its path, then substitute it for `RECEIVER-GROUP` below. The exact `sudo` form for adding a group depends on your system and was not tested on every distribution.

Expected: `ir list` prints one line per receiver: its by-id path and its name.

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir list
```

If the list is empty, the output is `No key-reporting receiver with a stable /dev/input/by-id/ path was found.` Check the cable, the device's group, and that `evdev` is installed.

Then check what the receiver sends. The command listens for 15 seconds. Press each button on the remote once, one at a time. It prints `Key codes seen:` and, when the receiver sends them, `Scan codes seen:`. Verify only shows what arrives: teaching the buttons is a separate step (see [Teach the remote's buttons](#teach-the-remotes-buttons)).

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir verify /dev/input/by-id/usb-PLACEHOLDER-event-kbd
```

Expected: `Key codes seen:` and a list of numbers, and `Scan codes seen:` when the receiver sends scan codes. If no key presses are seen, the output says `Nothing was changed: no key presses seen in 15 seconds.` and the exit code is 1. Run the command again and press each button once within the 15 seconds.

## Save the receiver

Where: the bridge computer, before you start the bridge the first time.

`ir select` chooses the receiver. It saves the receiver's path and name and three key codes to `ir-config.json`, in the bridge's state folder. It does not teach the remote's buttons. The file is written to a temporary file, checked, and then moved into place. It is never changed if the check fails.

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir select /dev/input/by-id/usb-PLACEHOLDER-event-kbd --volume-up 115 --volume-down 114 --mute 113
```

Expected: `Saved receiver` followed by the name and the path of `ir-config.json`, the line `The bridge uses it after its next start.`, and a line that names the next step, `ir learn`. Then choose the room the remote controls:

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir target a
```

**How presses are matched.** The bridge opens the receiver named in `ir-config.json`. It matches each press's scan code against the buttons you taught: Volume up, Volume down and Mute. You teach them with `ir learn` on the command line, or with **Add remote** in the Bose Lifestyle Assistant integration. Both use the same steps. A taught button does nothing until it is turned on. The three key codes saved by `ir select` are stored, but the bridge does not use them to match presses.

## Teach the remote's buttons

Where: the bridge computer, while the bridge is running. Run it as the bridge user. Learning talks to the running bridge only, so it does not need the receiver's group.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir learn --name "Den remote"
```

The command first says that other remotes you have set up stop working while you learn this one. It then asks for each of the three buttons. For each button, point the remote at the receiver, press Enter to listen, then press and release the button three times about one second apart. Held-button repeats do not count. Learning sends no Bose commands, and it never transmits anything to the Bose unit.

Each button must verify, with three matching presses, before it is kept. If the presses do not match, the command says so and offers Retry or Skip. A code that another remote also uses is marked as shared: identical infrared signals cannot tell two physical remotes apart. At the review, type `y` to save. Nothing is saved until you do. Press Ctrl-C before the review to cancel; the session is canceled and nothing is saved. After the save, Ctrl-C only stops the ready check, and the remote stays saved with its buttons off. Add `--yes` only for a non-interactive run: it skips the prompts, keeps each button that verifies, and stops on the first button that does not.

After the save, a ready check asks you to press each learned button once. The saved buttons start disabled. Turn them on one at a time:

```
sudo -u bose -g RECEIVER-GROUP /opt/bose-lifestyle/bin/bose-lifestyle ir list
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir button REMOTE-ID volume-up on
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir remote REMOTE-ID on
```

`ir list` shows each learned remote with its ID and its buttons, never their codes. `ir forget REMOTE-ID` removes a remote from the bridge.

If the remote does nothing after that, see [Fix a problem](fix-a-problem.md).

## Messages from the receiver commands

| Message | Meaning |
|---|---|
| `ir_unsupported` text: "Unsupported: ... has no stable /dev/input/by-id/ path" | The device reports keys but has no by-id path, so the bridge cannot use it. |
| "That receiver path was not found. Run `bose-lifestyle ir list` to see the choices." | The path you typed does not exist. |
| "This user cannot read the receiver. Run as the bridge's user or ask the installer for help." | The bridge user cannot read the device. Check its group. |
| "The key reader (evdev) is not installed on this bridge host, so receivers cannot be checked." | Install `evdev`, then try again. |
| "Only a receiver with a /dev/input/by-id/ path can be saved. Pick one from `bose-lifestyle ir list`." | `ir select` needs the by-id path. |
| "That device does not report key presses. Pick a receiver from `bose-lifestyle ir list`." | The device cannot send key codes. |
| "The receiver was unplugged or stopped responding. Nothing was changed." | The receiver was lost during the check. Check the connection, then run the check again. |
| "Another learning session is active." | Finish or cancel the other session, or wait five minutes, then run `ir learn` again. |
| "The learning session ran out of time after five minutes." | Nothing was saved. Run `ir learn` again and go through the buttons without long pauses. |
| "This code belongs to a different button." | The code was already taught to another button. Retry, or skip this button. |
| "That is not a remote ID." | Copy the ID that `ir list` shows. |
| "The bridge is not running, so nothing was changed." | `ir learn`, `ir remote`, `ir button` and `ir forget` need the bridge running. |

## Unsupported cases

- A receiver with no by-id path. Some devices appear only under `/dev/input/eventN`. The bridge will not use them.
- A receiver that does not report key presses.
- A receiver that needs its infrared protocol to be enabled in the kernel first. The installer's udev rule only sets the group that can read the receiver and its permissions. It does not enable any infrared protocol.
- More than one receiver. The project supports one receiver with one target room.

## The udev rule

With `--receiver-vendor` and `--receiver-product`, the installer writes one udev rule. The rule matches only that vendor and product (and the serial, if you give `--receiver-serial`). It sets the group of the device to the group that owns the receiver device when you ran the installer, and its mode to `0640`. The rule file is `/etc/udev/rules.d/70-bose-receiver.rules`.

The rule does not change which user is in which group. The bridge service gets the group through its service settings. Interactive commands, such as `ir list`, `ir verify`, `ir select` and `ir learn`, need the receiver's group added for the command as shown above.
