# Update, back up and recover

Keep the bridge computer & Home Assistant on the same release. If they differ, the integration can report "The bridge computer sent something this version can't read".

## Update the bridge computer

Where: the bridge computer.

1. Back up first (see below).
2. Copy the new project files to the bridge computer, in a new folder.
3. Run the installer from the new `deploy` folder with the same options you used before. The installer copies the new program files & rewrites the service file. It keeps `config.json`, the receiver file & the state folder.

   ```
   cd PATH/TO/NEW-bose-lifestyle/deploy
   sudo sh install-bridge.sh --public-key 'PASTE-THE-SAME-PUBLIC-KEY' --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0
   ```

   If this bridge uses an infrared receiver, add the receiver option too, as you did the first time:

   ```
   sudo sh install-bridge.sh --public-key 'PASTE-THE-SAME-PUBLIC-KEY' --user bose --serial-device /dev/serial/by-id/usb-PLACEHOLDER-if00-port0 --receiver-device /dev/input/by-id/usb-PLACEHOLDER-event-ir
   ```

   The installer stops before changing anything when the installed service already uses a serial port or a receiver & you leave that option out, so a copied command can't silently drop it. The saved receiver mappings are kept either way.

   Expected: the output names the install folder, and the configuration line says `config.json left unchanged`.

4. Restart the service so it runs the new files:

   ```
   sudo systemctl restart bose-bridge.service
   sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
   ```

   Expected: `Bridge: running (live mode)`, with the zones and inputs you had before.

If not: if the service does not start, run `journalctl -u bose-bridge.service -n 50` and see [Fix a problem](fix-a-problem.md).

## Update Home Assistant

If you installed with HACS, update from HACS (it lists new releases), then restart Home Assistant. Otherwise, replace the `custom_components/bose_lifestyle` folder in the Home Assistant configuration folder with the new release, then restart Home Assistant. Keep the `.ssh` files, so pairing continues to work.

## Back up

Back up these items before an update, before an uninstall, or on a schedule you choose. They contain your configuration, names & pairing.

On the bridge computer:

- The state folder, `/var/lib/bose-control/`. It holds `config.json`, `ir-config.json`, `ir-state.json` & the event log.
- The restricted key line in `/home/bose/.ssh/authorized_keys`. It isn't secret, but keeping it makes a reinstall simpler.

```
sudo tar -czf bose-control-backup.tar.gz -C / var/lib/bose-control
```

In Home Assistant's configuration folder:

- `.ssh/bose_ed25519`. This is the private pairing key. Keep it secret. Anyone who has it can reach the bridge's restricted command.
- `.ssh/bose_known_hosts`. The pinned bridge host key.
- `.storage/bose_lifestyle.rooms`. Room names, input nicknames & icons, & the bridge computer's nickname.

Store the backup files privately, with the same care as a password. Do not share them.

## Check the configuration

Where: the bridge computer.

```
sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle config show
```

Expected: a JSON object with four parts: `zones`, `sources`, `unverified` and `ir`. Compare it with what you expect.

If not: `There is no valid saved configuration` means the file is missing or refused. Follow the recovery steps below.

## Recover a damaged or missing configuration

Where: the bridge computer.

1. Stop the bridge service. Stopping sends nothing to the Bose unit.

   ```
   sudo systemctl stop bose-bridge.service
   ```

2. Preserve the damaged file by moving it aside:

   ```
   sudo mv /var/lib/bose-control/config.json /var/lib/bose-control/config.json.damaged
   ```

   Skip this step if there is no file.

3. Choose one:
   - Restore your backup copy of `config.json` into `/var/lib/bose-control/`, then make the bridge user its owner: `sudo chown bose /var/lib/bose-control/config.json`.
   - Or create the starting configuration, then set your rooms & inputs again:

     ```
     sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle init
     ```

4. Start the service & check it:

   ```
   sudo systemctl start bose-bridge.service
   sudo -u bose /opt/bose-lifestyle/bin/bose-lifestyle status
   ```

   Expected: `Bridge: running (live mode)`, and the zones and inputs you expect.

## Recover pairing

If Home Assistant can no longer reach the bridge, or you deleted the pairing key, pair again: follow [Install](install.md), Route A, from step A2. Pairing creates a new key, so run the installer again with the new public key shown on the setup screen. Then confirm the fingerprint.

## Recover a pause

A pause isn't a configuration problem. Follow the installer section of [Fix a problem](fix-a-problem.md) before restarting anything.
