# Bose Lifestyle Controller changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Volume steps per press (1 to 5): one volume up or volume down press can send several checked volume steps. Setting: `bose-lifestyle volume-steps N`, or the card's settings.

### Changed

- Volume presses in Rooms A, B and C share the same quarter-second pacing.

## [1.0.0] - 2026-10-15

### Added

- Serial bridge for Bose Lifestyle systems, with a command line and a Home Assistant integration.
- Room and source control, volume, mute, and infrared remote learning.
- Installer, uninstall and backup documentation, and the release packaging for HACS.
