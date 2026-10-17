# Hardware

Check the hardware requirements & safety cautions before connecting the bridge.

## What you need

- **A bridge computer.** Any Linux computer with systemd & udev. It needs Python 3.9 or newer, & an SSH server if you use Home Assistant. The installer checks for systemd, udev & Python. Without systemd or udev, it installs the files & prints the manual steps.
- **A serial adapter** between the bridge computer & the Bose control unit (the M1). The adapter must appear under `/dev/serial/by-id/`. See the warnings below before you connect anything.
- **Python packages.** `pyserial` (imported as `serial`) is needed for the serial port. `evdev` is needed for a receiver. The installer doesn't install them.
- **Optional: an infrared receiver** that reports key presses & has a stable `/dev/input/by-id/` path. See [Receivers](receivers.md).
- **Optional: Home Assistant**, for the room controls & guided pairing.

A Raspberry Pi is one example of a bridge computer. The software doesn't require one. See [the example](example-raspberry-pi.md).

## Serial adapter facts

### Wiring used on a working setup

The maintainer's working setup uses a USB-to-TTL FTDI cable (FT232RNL chip, 6-pin header) feeding a 3.5 mm plug through a three-position screw terminal. Three of the six wires are used. The adapter's wire colors are those of the standard FTDI TTL-232R cable; check your own adapter's documentation, because yours may differ.

| Adapter wire | Signal | 3.5 mm plug (screw terminal label) |
|---|---|---|
| Black | Ground | Sleeve (G) |
| Orange | Adapter transmit (TXD) | Ring (R) |
| Yellow | Adapter receive (RXD) | Tip (L) |

The red (5 V), brown (CTS) & green (RTS) wires aren't connected. Keep them apart from everything else.

The plug goes into the Bose control unit's 3.5 mm serial jack. The adapter's transmit wire feeds the unit's receive line, & the adapter's receive wire listens to the unit's transmit line. Do not swap a power wire into a signal position.

**Measured TXD idle voltage:** approximately 4.9 V, measured from ground to ring (R) with the adapter USB powered & the Bose disconnected.

## Safety cautions

**Check the signal level before you connect.** The serial interface on the M1 uses TTL-level signals, as far as the project's research shows. Confirm the adapter's logic level & pinout against its documentation before you connect it to the Bose unit. **Never connect an RS-232 level device or adapter.** RS-232 voltages can damage the control unit.

**Never power-cycle the M1 control unit.** It may fail to re-link the remote. Do not turn the Bose control unit off & on to clear a problem.

**The software can drive amplifiers to high volume.** The bridge can turn rooms on & raise volume. Before you test, set the amplifiers to a low level, & keep them low while you check each command.

**New-install defaults.** New installs start with every room off, every input disconnected & unverified commands off. The bridge sends nothing when it starts. It never retries or replays a command. Turn on only what you need.

## Known-working example

Serial adapter: a USB-to-TTL FTDI cable with an FT232RNL chip (sold as 5 V, 6 pin, 6 ft, brand DTECH), used with a 3.5 mm stereo (balanced male jack) to 3-pin AV screw terminal cable (brand Poyiccot). Its pinout, connector orientation & measured logic level are listed under [Serial adapter facts](#serial-adapter-facts).

Infrared receiver: HP Media Center IR Receiver, model OVU400103/00 (infrared receiver sensor, silver). It is one known-working example, not a requirement.
