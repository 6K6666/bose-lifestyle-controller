# Help Wanted

## Zone D testing

We need contributors with Zone D already active and working on their Bose Lifestyle system. Usage has been extensively tested on Zones A, B, and C; we need results from owners who can test Zone D on real hardware.

Confirm that Zone D works with your original Bose controls, then report what physically happens when using Home Assistant for:

- Power on and off.
- Input selection.
- Volume up and down.
- Mute and unmute.

If you use a learned infrared remote, feedback on its Zone D volume and mute controls is welcome too. Note any difference between what Home Assistant reports and what the system does.

## Live state research and equipment donations

**Live state is currently unsupported.** We cannot read the current power, source, volume, or mute state for each room, including changes made with the P1 remote. Command acknowledgments and volume estimates do not establish the actual state.

We need **donor M1 interfaces, P1 remotes, Lifestyle systems and parts, and RF test equipment or lab access** to investigate this. Several RF research approaches we evaluated required equipment beyond the project's current budget. Affordable approaches, equipment loans, and reproducible research are welcome.

See [Live state research](live-state-research.md) for what we have investigated, the remaining questions, useful equipment, and how to help. Donors and contributors who provide successful code or research for this capability will receive prominent credit in the repository, with their permission.

## Other Lifestyle systems: compatibility research

Serial control is documented on additional Lifestyle systems. This project's tested target is the Lifestyle 50/M1; compatibility with the following systems remains unverified.

| Research lead | Evidence | Help needed |
|---|---|---|
| Lifestyle 40 Multi-Room Interface | Bose's [LS40/50 service manual, page 30](https://audiocircuit.dk/downloads/bose/Bose-LifestyleLS40-mc-sm.pdf#page=30) describes external serial control using TTL signaling at 4800 baud, 8 data bits, no parity, and 1 stop bit. | Compare the exact commands and replies with the M1 implementation, and document the control unit and firmware used. These serial settings alone do not establish command compatibility. |
| AV20 / AV35 consoles | Bose's [external control guidance](https://support.bose.ca/en-CA/article/ls28ii-speakerht5-programming-your-remote---ka08c000000hygmaau) identifies these consoles for external control through an RS232 serial adapter. | Investigate each console's interface and protocol, and whether a separate backend is needed. The current M1 ETAP driver has not been validated on these consoles. |

Contributions of manufacturer documentation, references for working control drivers, and command/reply evidence are welcome. Check the target model's connector and electrical requirements before connecting an adapter; the M1 cable and signaling levels cannot be assumed to apply to other consoles.

## Share results or contribute

[Open an issue](https://github.com/6K6666/bose-lifestyle-controller/issues/new) with:

- Your Bose system and control-unit model, plus firmware version if known.
- The project release, Home Assistant version, and bridge hardware and operating system.
- For Zone D reports, confirmation that it works with the original Bose controls.
- The actions you tested, the physical results, and any errors or relevant logs.

Remove SSH keys, tokens, passwords, and other private details from anything you share.

Code fixes, regression tests, and documentation improvements are welcome through a pull request. Describe your hardware setup and how you tested the change.

[Back to the README](../README.md)
