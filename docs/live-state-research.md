# Live state research: equipment and contributors wanted

**Live state is currently unsupported.** The project sends commands but cannot read each room's current power, source, volume, or mute state. This includes changes made with the original P1 remote. A successful serial acknowledgment means the command was acknowledged; it does not prove the resulting room state. Counting volume commands cannot reliably establish absolute volume.

We want to close that gap. We are seeking donor **M1 interfaces, P1 remotes, Lifestyle equipment and parts**, along with **RF test equipment, equipment loans, or lab access**. Several RF approaches evaluated so far required equipment prohibitively expensive for the project's current budget. Lower-cost methods and existing captures or research could help substantially. We cannot promise that donated equipment will lead to a working implementation.

## What we have researched so far

The following summarizes research through October 16, 2026. These are leads and unresolved questions, not working live-state implementations.

### 1. Serial feedback through the M1

The existing serial connection would be the simplest place to obtain state. We have not identified a verified, read-only M1 query for room power, source, absolute volume, or mute. The `BO` response identifies firmware; captured command acknowledgments echo commands rather than returning room state.

We found several commercial-driver leads:

- [iRidi's Duotecno documentation](https://doc.iridi.com/SWDL/dev-iridi/Duotecno/) describes Lifestyle 50 volume feedback through a Duotecno gateway. The available examples describe gateway traffic, not the underlying M1 query bytes.
- [Duotecno's DT10-BOE](https://www.duotecno.be/fr/produits/interfaces/audio-video/dt10?variant=16636) was marketed as a bidirectional Bose interface. The datasheet linked during our investigation described an Integra interface instead, leaving the Bose protocol unresolved.
- [MyURemote's Lifestyle 40/50 M1 integration notes](https://www.myuremote.com/website/docs/integrations/bose/lifestyle-40-50-m1/) mention configuration-dependent feedback, without publishing the raw query needed here.
- An older [Crestron Lifestyle 50 module](https://applicationmarket.crestron.com/content/Help/Bose/lifestyle_50.pdf) describes no feedback and uses a different serial arrangement. It does not settle what is possible through our M1 interface.

Useful contributions would include documented, lawful command/reply examples from a working feedback integration or manufacturer documentation explaining a read-only state query. Vendor inquiries were considered but have not been sent. We have not established that feedback is impossible.

Passive observation of serial traffic while changing state with the P1 is another proposed experiment. It has not been performed. A second reader on the bridge's serial port could consume replies and disrupt normal operation; observation needs an appropriate passive capture setup. Blindly probing undocumented service commands is unsuitable: related Bose service protocols include commands that can alter settings or EEPROM.

### 2. Observing the P1 radio link

The P1 and M1 communicate over a two-way 2.4 GHz radio link. The [P1 FCC filing](https://fccid.io/A94-P1) identifies operation in the 2403.4–2480 MHz range. This makes their traffic a plausible research target, but we have not decoded its messages or established that all required state is available in them.

We considered borrowing or renting a software-defined radio for an initial **receive-only** survey: identify bursts, correlate them with known P1 actions, then investigate message structure, pairing, timing, and responses. A [HackRF One](https://greatscottgadgets.com/hackrf/one/) was considered as a survey tool because its tuning range covers 2.4 GHz. Its maximum 20 million samples per second cannot observe that entire roughly 77 MHz span at once. Actual channel use and any hopping behavior remain unverified, so this is not a guarantee that one particular radio is sufficient.

Reliable state recovery could require wider-band capture, more capable RF analysis equipment, or specialist lab access. Cost blocked several approaches within the current project budget. We have acquired no RF research equipment and performed no RF captures, transmission, replay, or P1 impersonation experiments. Advice that reduces the equipment requirement would be valuable.

The public [M1](https://fccid.io/A94-M1) and P1 FCC material provides manuals, photos, and test reports. Some technical exhibits are marked confidential or are not publicly downloadable, so the filings have not supplied a complete protocol description.

### 3. Donor hardware, internal signals, and firmware

The [Bose Lifestyle 40/50 service manual](https://audiocircuit.dk/downloads/bose/Bose-LifestyleLS40-mc-sm.pdf) provides board and component information. Earlier research identified these possible routes:

- The M1's programmed controller is listed as a Toshiba TMP87PM40F, with regional variants. A [older programmer device list](https://j-fsg.com/support/dl/catalog/pdf/D_LIST130719.pdf) lists that part and a specialized adapter, suggesting a possible donor-chip readout route. Compatibility with the exact Bose silicon, the required profile, and any readout protection are unverified. No Bose firmware read has been performed.
- The related [Toshiba TMP87PH40A/PM40A datasheet](https://toshiba.semicon-storage.com/info/TMP87PH40ANG_datasheet_en_20080306.pdf?did=11438&prodName=TMP87PH40ANG) describes PROM operations, but it is not proof that the M1's exact installed part can be read successfully.
- The P1's programmed controller needs a confirmed full chip marking and board revision before selecting a programmer. The service-manual abbreviation alone is insufficient.
- The 24C02A EEPROMs hold settings or registration data; reading them would not be a dump of the controller firmware.

Donor boards could allow inspection of internal radio/controller signals and investigation of firmware without putting the household's working system at risk. Our earlier searches found no public M1/P1 firmware dump or complete P1 RF implementation that solves this problem. Older Lifestyle RF projects and firmware archives for later DVD systems do not establish compatibility with M1/P1.

## Equipment and expertise that could help

- Working or repairable M1 interfaces and P1 remotes, especially a matching pair, with board revision, regional model, known faults, and compatible power supplies identified where possible.
- Lifestyle systems and parts generally. Other models may also support the separate [compatibility research](help-wanted.md#other-lifestyle-systems-compatibility-research).
- Access to suitable 2.4 GHz SDRs, spectrum analysis or capture equipment, antennas, and RF expertise. Please discuss suitability before purchasing or shipping anything.
- Logic analyzers, oscilloscopes, and experience observing internal digital buses.
- Compatible chip programmers and adapters, after confirming the exact controller marking and a suitable read-only profile.
- Protocol documentation, reproducible captures, firmware-analysis expertise, or code implementing a verified state source.

Please [open an issue](https://github.com/6K6666/bose-lifestyle-controller/issues/new) describing what you can offer, its condition, and any relevant experience. Coordinate acceptance and shipping privately before sending equipment; do not post an address or other personal details in a public issue. This request does not ask anyone to perform destructive experiments on their working system.

## What a successful contribution would establish

We need reproducible evidence that a source reports actual room state, including changes made with the P1, rather than only remembering commands sent by this project. Reports should identify the hardware and firmware, capture method, actions taken, replies observed, and any unknowns. Remove credentials, private identifiers, and personal information before sharing evidence, and share only material you have permission to distribute.

An implementation must handle missing or stale feedback honestly. The bridge and Home Assistant deliberately leave unsupported room-state values unknown; their shared data format and validation would need coordinated changes when a verified state source exists.

## Recognition

Accepted equipment donors and contributors of successful, reproducible research or code for live state will receive prominent recognition in the repository. With permission, we will credit their preferred name or handle in README acknowledgments and on this research page, explain their contribution, and recognize it in the release notes when the resulting capability ships. Anonymous contributions are welcome too. No donors or results are being claimed before a contribution is received and confirmed.

[Back to Help Wanted](help-wanted.md) · [Back to the README](../README.md)
