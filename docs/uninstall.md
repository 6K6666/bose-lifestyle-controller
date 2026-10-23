# Uninstall

Remove everything created by the installer & the Home Assistant integration in this order: Home Assistant first, then the bridge computer. Back up first if you may want the system back later (see [Update, back up and recover](update-backup-recover.md)).

Removing the bridge stops room control. Tell the household first, & make sure they have the original Bose remote.

## Part 1: Home Assistant

Where: Home Assistant and its configuration folder.

1. Delete the integration. In Home Assistant, open Settings, Devices and services, Bose Lifestyle Assistant, & choose Delete. This removes the integration's entries, its entities & its remote entries.
2. Delete the integration's files: the `custom_components/bose_lifestyle` folder in the configuration folder. If you installed the integration with HACS, remove it there instead: open HACS, choose Bose Lifestyle Assistant, then Remove. You can also remove the custom repository entry from HACS's three-dot menu.
3. Delete the pairing files in the `.ssh` folder of the configuration folder:
   - `.ssh/bose_ed25519`, the private pairing key.
   - `.ssh/bose_known_hosts`, the pinned host key.
   - Any `.ssh/bose-` control socket files left over. These are short-lived. Leave every other file in `.ssh` alone.
4. Delete `.storage/bose_lifestyle.rooms`. Deleting the integration doesn't remove this file. It holds the room names, input nicknames & icons.
5. Restart Home Assistant itself.

Expected: the Bose Lifestyle Assistant integration no longer appears under Devices and services.

If not: if the integration is still listed, delete it again from its page. Do not restart or power-cycle the Bose unit.

## Part 2: Bridge computer

Where: the bridge computer. Run the commands as an administrator, in this order.

1. Stop the service & stop it from starting again. Stopping sends nothing to the Bose unit.

   ```
   sudo systemctl disable --now bose-bridge.service
   ```

2. Remove the service file, then reload systemd.

   ```
   sudo rm /etc/systemd/system/bose-bridge.service
   sudo systemctl daemon-reload
   ```

3. Remove the receiver rule, if you installed one, then reload udev rules.

   ```
   sudo rm -f /etc/udev/rules.d/70-bose-receiver.rules
   sudo udevadm control --reload-rules
   ```

4. Remove the restricted key line. Open `/home/bose/.ssh/authorized_keys` in an editor as an administrator & delete the line that begins with `command="/opt/bose-lifestyle/bose_request.py",restrict`. Save the file. If it has no other lines, delete the file. Delete the `.ssh` folder only if it is empty & you didn't have one before.

5. Remove the program files & the Python cache folders that the service may have created inside them:

   ```
   sudo rm -r /opt/bose-lifestyle
   ```

6. Remove the state folder. This deletes the configuration, the receiver file, the infrared state & the event log, including `events.jsonl.1` if it exists. Make a backup first if you may want them.

   ```
   sudo rm -r /var/lib/bose-control
   ```

7. Check the runtime folder. Systemd removes `/run/bose-control` when the service stops. If it still exists, remove it:

   ```
   sudo rm -r /run/bose-control
   ```

8. Remove the key pair you made for the command-line route, if you made one. Run this as the administrator who created it:

   ```
   rm -f ~/bose-cli-key ~/bose-cli-key.pub
   ```

The installer doesn't create the `bose` user, doesn't add anyone to a group, & doesn't install Python packages or the SSH server. Uninstalling doesn't remove them either. Remove any of them yourself only if you added them for this project alone.

## What the installer created

These are all the paths & entries the installer creates, with their removal steps:

| Created by the installer | Removed in step |
|---|---|
| `/opt/bose-lifestyle/` and its files (`bose_bridge.py`, `bose_cli.py`, `bose_commands.py`, `bose_config.py`, `bose_events.py`, `bose_ir.py`, `bose_receivers.py`, `bose_request.py`, `bose_serial.py`, `bose_state.py`, `bin/bose-lifestyle`) | 5 |
| `/var/lib/bose-control/` and `config.json` | 6 |
| `/var/lib/bose-control/ir-config.json` (written by `ir select`) | 6 |
| `/var/lib/bose-control/ir-state.json` (written by the bridge) | 6 |
| `/var/lib/bose-control/events.jsonl` and `events.jsonl.1` (written by the bridge) | 6 |
| `/run/bose-control/command.sock` (runtime, while the bridge runs) | 7 |
| `/etc/systemd/system/bose-bridge.service` | 2 |
| `/etc/udev/rules.d/70-bose-receiver.rules` (receiver only) | 3 |
| `/home/bose/.ssh/` and `/home/bose/.ssh/authorized_keys`, with the line `command="/opt/bose-lifestyle/bose_request.py",restrict` | 4 |
| Python cache folders (`__pycache__`) created by the service inside `/opt/bose-lifestyle/` | 5 |
| `~/bose-cli-key` and `~/bose-cli-key.pub` (made by you for the command-line route) | 8 |

On Home Assistant: `custom_components/bose_lifestyle/`, `.ssh/bose_ed25519`, `.ssh/bose_known_hosts`, `.ssh/bose-` control socket files, & `.storage/bose_lifestyle.rooms`.

## Check that it is gone

Where: the bridge computer.

```
systemctl status bose-bridge.service
ls /opt/bose-lifestyle /var/lib/bose-control
```

Expected: systemd reports that the unit could not be found, and both folders report `No such file or directory`.
