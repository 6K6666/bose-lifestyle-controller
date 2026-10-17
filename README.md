# Bose Lifestyle Assistant

> **Disclaimer:** This project was made using Codex and Claude Code. Usage has been extensively tested on Zones A, B, and C, but malfunctions and other issues are possible. Use it at your own risk. I do not assume liability for loss, damage, or other consequences resulting from its use.

![Bose Lifestyle Assistant](docs/assets/banner.png)

This project currently targets the Bose Lifestyle 50/M1 through the control unit's ETAP serial interface. A small bridge program runs on a Linux computer, called the bridge computer. Home Assistant provides the system controls. The command line on the bridge computer configures and checks the bridge; it does not send daily power, source or volume commands. Those come from Home Assistant; a learned infrared remote provides volume and mute only, and the original Bose remote covers everything else.

Only serial-connected Lifestyle systems are covered. Newer Bose speakers use a different protocol and are not supported.

## Help Wanted

We need owners with an active, working Zone D to help test and improve support. We also welcome research into other Lifestyle systems with documented serial control. See [Help Wanted](docs/help-wanted.md) for testing requests, compatibility leads, and how to contribute.

We also seek donor M1/P1/Lifestyle equipment, RF test equipment, and research help for [live state support](docs/live-state-research.md); donors and successful contributors will be prominently credited.

## Documentation

- [Start here](docs/start-here.md): what this is, and which page to read next.
- [Install](docs/install.md): one fresh-install route, with Home Assistant or command line only.
- [Everyday use](docs/everyday-use.md): daily controls for household members.
- [Fix a problem](docs/fix-a-problem.md): symptoms, with separate household and installer sections.
- [Update, back up and recover](docs/update-backup-recover.md)
- [Uninstall](docs/uninstall.md)
- [Reference](docs/reference.md): commands, configuration, paths, messages and the security model.
- [Receivers](docs/receivers.md): optional infrared receivers.
- [Hardware](docs/hardware.md): what you need, and safety cautions.
- [Worked example](docs/example-raspberry-pi.md): one complete setup, start to finish.

The project files are at https://github.com/6K6666/bose-lifestyle-controller.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

---

Unofficial community project. Not affiliated with or endorsed by Bose Corporation. "Bose" and "Lifestyle" are trademarks of Bose Corporation, used here only to identify compatible products.
