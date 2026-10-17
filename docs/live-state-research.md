# Live state research: equipment and contributors wanted

**Live state is currently unsupported.** The project sends commands but can't read each room's current power, source, volume, or mute state. This includes changes made with the original P1 remote. A successful serial acknowledgment means the command was acknowledged; it doesn't prove the resulting room state. Counting volume commands can't reliably establish absolute volume.

To investigate live state, we need donor **M1 interfaces, P1 remotes, Lifestyle equipment and parts**, along with **RF test equipment, equipment loans, or lab access**. Several RF approaches evaluated so far required equipment beyond the project's current budget. Lower-cost methods & existing captures or research could help.

We can't promise that donated equipment will lead to a working implementation.

## What we have researched so far

This page summarizes research through October 16, 2026. These are leads & unresolved questions, not working live-state implementations.

### 1. Serial feedback through the M1

The existing serial connection would be the simplest place to obtain state. We haven't identified a verified, read-only M1 query for room power, source, absolute volume, or mute. The `BO` response identifies firmware; captured command acknowledgments echo commands rather than returning room state.

We found several commercial-driver leads:

- [iRidi's Duotecno documentation](https://doc.iridi.com/SWDL/dev-iridi/Duotecno/) describes Lifestyle 50 volume feedback through a Duotecno gateway. The available examples describe gateway traffic, not the underlying M1 query bytes.
- [Duotecno's DT10-BOE](https://www.duotecno.be/fr/produits/interfaces/audio-video/dt10?variant=16636) was marketed as a bidirectional Bose interface. The datasheet linked during our investigation described an Integra interface instead, leaving the Bose protocol unresolved.
- [MyURemote's Lifestyle 40/50 M1 integration notes](https://www.myuremote.com/website/docs/integrations/bose/lifestyle-40-50-m1/) mention configuration-dependent feedback, without publishing the raw query needed here.
- An older [Crestron Lifestyle 50 module](https://applicationmarket.crestron.com/content/Help/Bose/lifestyle_50.pdf) describes no feedback & uses a different serial arrangement. It doesn't settle what is possible through our M1 interface.

Useful contributions would include documented, lawful command/reply examples from a working feedback integration or manufacturer documentation explaining a read-only state query. Vendor inquiries were considered but haven't been sent. We haven't established that feedback is impossible.

Passive observation of serial traffic while changing state with the P1 is another proposed experiment. It hasn't been performed. A second reader on the bridge's serial port could consume replies & disrupt normal operation; observation needs an appropriate passive capture setup. Blindly probing undocumented service commands is unsuitable: related Bose service protocols include commands that can alter settings or EEPROM.

### 2. Observing the P1 radio link

The P1 & M1 communicate over a two-way 2.4 GHz radio link. The [P1 FCC filing](https://fccid.io/A94-P1) identifies operation in the 2403.4–2480 MHz range. This makes their traffic a plausible research target, but we haven't decoded its messages or established that all required state is available in them.

We considered borrowing or renting a software-defined radio for an initial **receive-only** survey: identify bursts, correlate them with known P1 actions, then investigate message structure, pairing, timing, & responses. A [HackRF One](https://greatscottgadgets.com/hackrf/one/) was considered as a survey tool because its tuning range covers 2.4 GHz.

Its maximum 20 million samples per second can't observe that entire roughly 77 MHz span at once. Actual channel use & any hopping behavior remain unverified, so this isn't a guarantee that one particular radio is sufficient.

Reliable state recovery could require wider-band capture, more capable RF analysis equipment, or specialist lab access. Cost blocked several approaches within the current project budget.

We've acquired no RF research equipment & performed no RF captures, transmission, replay, or P1 impersonation experiments. We welcome advice that reduces the equipment requirement.

The public [M1](https://fccid.io/A94-M1) & P1 FCC material provides manuals, photos, & test reports. Some technical exhibits are marked confidential or aren't publicly downloadable, so the filings haven't supplied a complete protocol description.

### 3. Donor hardware, internal signals, and firmware

The [Bose Lifestyle 40/50 service manual](https://audiocircuit.dk/downloads/bose/Bose-LifestyleLS40-mc-sm.pdf) provides board & component information. Earlier research identified these possible routes:

- The M1's programmed controller is listed as a Toshiba TMP87PM40F, with regional variants. An [older programmer device list](https://j-fsg.com/support/dl/catalog/pdf/D_LIST130719.pdf) lists that part & a specialized adapter, suggesting a possible donor-chip readout route. Compatibility with the exact Bose silicon, the required profile, & any readout protection are unverified. No Bose firmware read has been performed.
- The related [Toshiba TMP87PH40A/PM40A datasheet](https://toshiba.semicon-storage.com/info/TMP87PH40ANG_datasheet_en_20080306.pdf?did=11438&prodName=TMP87PH40ANG) describes PROM operations, but it isn't proof that the M1's exact installed part can be read successfully.
- The P1's programmed controller needs a confirmed full chip marking & board revision before selecting a programmer. The service-manual abbreviation alone is insufficient.
- The 24C02A EEPROMs hold settings or registration data; reading them would not be a dump of the controller firmware.

Donor boards could allow inspection of internal radio/controller signals & investigation of firmware without putting the household's working system at risk. Our earlier searches found no public M1/P1 firmware dump or complete P1 RF implementation that solves this problem. Older Lifestyle RF projects & firmware archives for later DVD systems do not establish compatibility with M1/P1.

## Equipment and expertise that could help

- Working or repairable M1 interfaces & P1 remotes, especially a matching pair, with board revision, regional model, known faults, & compatible power supplies identified where possible.
- Lifestyle systems & parts generally. Other models may also support the separate [compatibility research](help-wanted.md#other-lifestyle-systems-compatibility-research).
- Access to suitable 2.4 GHz SDRs, spectrum analysis or capture equipment, antennas, & RF expertise. Please discuss suitability before purchasing or shipping anything.
- Logic analyzers, oscilloscopes, & experience observing internal digital buses.
- Compatible chip programmers & adapters, after confirming the exact controller marking & a suitable read-only profile.
- Protocol documentation, reproducible captures, firmware-analysis expertise, or code implementing a verified state source.

Please [open an issue](https://github.com/6K6666/bose-lifestyle-controller/issues/new) describing what you can offer, its condition, & any relevant experience. Coordinate acceptance & shipping privately before sending equipment; do not post an address or other personal details in a public issue. This request doesn't ask anyone to perform destructive experiments on their working system.

## What a successful contribution would establish

We need reproducible evidence that a source reports actual room state, including changes made with the P1, rather than only remembering commands sent by this project. Reports should identify the hardware & firmware, capture method, actions taken, replies observed, & any unknowns. Remove credentials, private identifiers, & personal information before sharing evidence, & share only material you have permission to distribute.

An implementation must handle missing or stale feedback honestly. The bridge & Home Assistant leave unsupported room-state values unknown; their shared data format & validation would need coordinated changes when a verified state source exists.

## Recognition

Accepted equipment donors & contributors of successful, reproducible research or code for live state will receive prominent recognition in the repository. With permission, we will credit their preferred name or handle in README acknowledgments & on this research page, explain their contribution, & recognize it in the release notes when the resulting capability ships. Anonymous contributions are welcome too. No donors or results are being claimed before a contribution is received & confirmed.

[Back to Help Wanted](help-wanted.md) · [Back to the README](../README.md)
