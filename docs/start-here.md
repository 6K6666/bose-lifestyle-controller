# Start here

This project connects a Bose Lifestyle system to Home Assistant, or to a command line, through the serial port of the Bose control unit (the M1). A bridge program runs on a Linux computer, which this guide calls the bridge computer.

Unofficial community project. Not affiliated with or endorsed by Bose Corporation. "Bose" and "Lifestyle" are trademarks of Bose Corporation, used here only to identify compatible products.

Follow these rules throughout setup and use:

- Never power-cycle the M1 control unit. It may fail to re-link the remote.
- Do not connect a serial adapter to the M1 until you have checked its signal levels (see [Hardware](hardware.md)).

## Which page to read

| You are | Read |
|---|---|
| Installing the bridge, with or without Home Assistant | [Install](install.md) |
| Using the daily controls in a room | [Everyday use](everyday-use.md) |
| Something is not working | [Fix a problem](fix-a-problem.md) |
| Updating, backing up or recovering the bridge | [Update, back up and recover](update-backup-recover.md) |
| Removing the bridge or the integration | [Uninstall](uninstall.md) |
| Looking up a command, a file or a message | [Reference](reference.md) |
| Adding an infrared (IR) receiver | [Receivers](receivers.md) |
| Checking what hardware you need | [Hardware](hardware.md) |
| Following one complete setup, start to finish | [Worked example](example-raspberry-pi.md) |

## What you need

- A Linux computer with systemd and udev, Python 3.9 or newer, and an SSH server.
- A serial adapter between the bridge computer and the M1 (see [Hardware](hardware.md)).
- Optional: a key-reporting infrared receiver with a stable `/dev/input/by-id/` path.
- Optional: Home Assistant, if you want the room controls in Home Assistant.

Newer Bose speakers are not covered here. For those, see the link in the [README](../README.md).
