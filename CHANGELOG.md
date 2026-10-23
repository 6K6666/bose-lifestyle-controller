# Bose Lifestyle Assistant changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), & the project uses
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-10-23

### Added

- Live volume & selected-input indicators for Rooms A, B & C. The bridge reads each room's reported volume & selected input over its serial connection, only when a status read asks (at most one full poll every 5 seconds). The card shows "Volume" and "Selected input" for each room, including while its power is off. Status moves to schema 5; Home Assistant accepts schemas 4 and 5. Power & mute are not reported. Room D is not read. Thanks to [MyURemote](https://www.myuremote.com/website/) for helping us discover these queries.
- Install: HACS custom repository steps, so the integration can be added before it is in the default HACS catalog.

- Volume steps per press (1 to 5): one volume up or volume down press can send several checked volume steps. Setting: `bose-lifestyle volume-steps N`, or the card's settings.

### Changed

- The card follows Home Assistant theme variables & fits sections and masonry dashboards.
- Volume presses in Rooms A, B & C share the same quarter-second pacing.

## [1.0.0] - 2026-10-16

### Added

- Serial bridge for Bose Lifestyle systems, with a command line & a Home Assistant integration.
- Room & source control, volume, mute, & infrared remote learning.
- Installer, uninstall & backup documentation, & the release packaging for HACS.
