# Bose Lifestyle Controller

![Bose Lifestyle Controller](docs/assets/banner.png)

This project is for Bose Lifestyle systems that are controlled over the serial port of the Bose control unit (the ETAP serial interface). A small bridge program runs on a Linux computer, the bridge computer. You control the system from Home Assistant. The command line on the bridge computer configures and checks the bridge; it does not send daily power, source or volume commands. Those come from Home Assistant; a learned infrared remote provides volume and mute only, and the original Bose remote covers everything else.

This project covers Lifestyle systems over the serial port only; newer Bose speakers use a different protocol and are not supported.

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
