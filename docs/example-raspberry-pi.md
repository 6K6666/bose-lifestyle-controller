# Example: a Raspberry Pi bridge computer

This worked example follows the install route from start to finish on a Raspberry Pi. A Raspberry Pi is not required: any Linux computer that meets [Hardware](hardware.md) works. See [Install](install.md) for the general steps.

The example uses:

- A Raspberry Pi with a network connection, a USB serial adapter for the Bose control unit, and optionally a USB infrared receiver.
- A 64-bit Raspberry Pi OS image, which includes systemd and udev.
- The default bridge user name, `bose`.
- The host name `bridge.example.invalid`. Use your own address.

Values in capitals, such as `PLACEHOLDER`, are yours to replace.

## Step 1: Write the image

Where: your own computer, with the Raspberry Pi Imager.

In the Imager's settings, set a host name, enable SSH, and create the user `bose` with a password or an SSH key. Write the image to the microSD card and let the Imager verify it.

Expected: the Imager reports that writing and verification succeeded.

If not: try the write again with a different card, or check the card for errors.

## Step 2: Start the Pi and log in

Where: your own computer, with the Pi connected to the network. Connect the serial adapter and the receiver, if you have one, before you power on.

```
ssh -l bose bridge.example.invalid
```

Expected: a shell prompt on the Pi, for the user `bose`.

If not: check the network connection, the host name, and that SSH was enabled in step 1.

## Step 3: Check Python and find the device paths

Where: the Pi.

```
python3 --version
ls -l /dev/serial/by-id/
ls -l /dev/input/by-id/
```

Expected: the Python version is 3.9 or newer. The serial adapter appears in the first listing with a name that starts with `usb-`. The receiver appears in the second listing, and only if one is attached.

If not: if the serial adapter is missing, check the cable and the adapter, then run the listing again. If the Python version is older than 3.9, install a newer Python.

## Step 4: Install the Python packages

Where: the Pi.

On Raspberry Pi OS, which is based on Debian, the packages are normally `python3-serial` and, with a receiver, `python3-evdev`. Package names differ on other systems.

```
sudo apt install python3-serial python3-evdev
```

Expected: the packages install without errors.

If not: check the package names with your system's package tools. The installer does not install these packages.

## Step 5: Get the project files

Where: the Pi, in the `bose` user's home folder.

Download the project files from the [GitHub releases page](https://github.com/6K6666/bose-lifestyle-controller/releases) and unpack them so that the folder `bose-lifestyle` is in your home folder.

Expected: the folder contains `deploy/install-bridge.sh`.

```
ls /home/bose/bose-lifestyle/deploy/install-bridge.sh
```

If not: check the download and the unpacked folder name.

## Step 6: Get the receiver's vendor and product IDs (only with a receiver)

Where: the Pi.

```
udevadm info --query=property --name=/dev/input/by-id/usb-PLACEHOLDER-event-kbd
```

Expected: the output includes `ID_VENDOR_ID` and `ID_MODEL_ID`. Use them as the four-digit values in step 9. `lsusb` also lists them as `vendor:product`.

If not: check that the receiver path is the one from step 3.

## Step 7: Pair with Home Assistant, and get the public key (Home Assistant route)

Where: Home Assistant. Follow [Install](install.md), Route A, step A2. Copy the public key shown on the pairing screen. Keep the screen open.

Expected: the screen shows the command with the public key.

## Step 8: Run the installer

Where: the Pi, in the `deploy` folder.

```
cd /home/bose/bose-lifestyle/deploy
sudo sh install-bridge.sh --public-key 'PASTE-THE-PUBLIC-KEY' --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0
```

Expected: the output includes `installed under /opt/bose-lifestyle`, a line that starts with `Created /var/lib/bose-control/config.json`, the heading `Bridge host key fingerprint (compare it with the setup screen):` and a fingerprint line. The `Next step: sudo systemctl enable --now bose-bridge.service` line comes near the end, and the final line is `installed under /opt/bose-lifestyle`. Keep the fingerprint.

If not: read the line that starts with `install-bridge:`, fix the problem, and run the same command again. A second run is safe.

## Step 9: Add the receiver (only with a receiver)

Where: the Pi.

Run the installer again with the receiver options, then save the receiver before the service starts. See [Receivers](receivers.md) for `ir list`, `ir verify` and `ir select`.

```
sudo sh install-bridge.sh --public-key 'PASTE-THE-PUBLIC-KEY' --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0 --receiver-device /dev/input/by-id/usb-PLACEHOLDER-event-kbd --receiver-vendor 0000 --receiver-product 0000
```

Replace `0000` with the IDs from step 6.

Expected: the installer completes, and `ir select` prints `Saved receiver`.

## Step 10: Start the bridge

Where: the Pi.

```
sudo systemctl enable --now bose-bridge.service
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
```

Expected: the service is active, and `status` shows `Bridge: running (live mode)` with the serial port open.

If not: read `journalctl -u bose-bridge.service -n 50`, and see [Fix a problem](fix-a-problem.md).

## Step 10a: Teach the remote's buttons (only with a receiver)

Where: the Pi, while the bridge is running (step 10). Skip this step if you do not use an infrared receiver.

Expected: the command asks for each button in turn. Point the remote at the receiver, press Enter, then press and release the button three times about one second apart. `Three matching presses verified.` appears for each button, and the review lists each button as Learned or Skipped. Type `y` to save. Learning sends no Bose commands. The saved buttons start disabled.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle ir learn --name "Den remote"
```

If not: press Ctrl-C to stop. Nothing is saved until you type `y` at the review. "The bridge is not running" means step 10 is not finished. See [Install](install.md), step B8, for the full list of messages. Turn the buttons on in step B9 of that page.

## Step 11: Finish pairing in Home Assistant (Home Assistant route)

Where: Home Assistant. Enter `bridge.example.invalid`, the user `bose` and port 22. Compare the fingerprint on the screen with the `SHA256:` value from step 8, then confirm.

Expected: the integration is created. Continue with [Install](install.md), step A7 to add the card, then step A8 to set up rooms and inputs.

## Step 12: Turn on a room and check it

Where: Home Assistant, or the command line (`sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle zone a on`, then `source aux on`).

Expected: the room responds as it does to the original Bose remote.

## Checkpoints

| After | You should see |
|---|---|
| Step 2 | A shell prompt on the Pi. |
| Step 3 | Python 3.9 or newer, and the serial adapter under `/dev/serial/by-id/`. |
| Step 5 | `deploy/install-bridge.sh` in the project folder. |
| Step 8 | The installer's `Created ... config.json` line and a fingerprint. |
| Step 9 | `Saved receiver` from `ir select`. |
| Step 10 | `Bridge: running (live mode)` from `status`. |
| Step 10a | `Three matching presses verified.` for each button, then the saved remote from `ir learn`. |
| Step 11 | The integration appears in Home Assistant. |
| Step 12 | The room responds. |

Nothing is sent to the Bose unit when the bridge starts. The first command the bridge sends is one you ask for.
