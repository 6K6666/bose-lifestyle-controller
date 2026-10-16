# Commands

Generated from the bridge catalog by `tools/gen_commands_doc.py`. Edit the catalog and regenerate; do not edit this page.

41 commands are Verified and 13 are Experimental. A command is Verified only with evidence: original commissioning, or a promotion with a sanitized record in `docs/evidence/` (see [evidence](evidence/README.md)). Evidence is from one M1, at the banner shown.

Experimental commands are sent only when their room allows unverified commands (`experimental` in the CLI). Experimental, reply captured only means the M1 replied with the exact frame, but no change was observed.

**Room D is untested hardware.** There is no Room D unit to test against, so every Room D command is Experimental and ships opt-in.

| Command | Room | Function | Status | Verification | Notes |
| --- | --- | --- | --- | --- | --- |
| `room-a-mute` | A | Mute | Verified | Original commissioning | - |
| `room-a-power-off` | A | Power off | Verified | Original commissioning | - |
| `room-a-power-on` | A | Power on | Verified | Original commissioning | - |
| `room-a-source-am` | A | Source: AM | Verified | Original commissioning | - |
| `room-a-source-aux` | A | Source: Auxiliary | Verified | Original commissioning | - |
| `room-a-source-cd` | A | Source: CD | Verified | Evidence `room-a-source-cd-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-a-source-fm` | A | Source: FM | Verified | Evidence `room-a-source-fm-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-a-source-tape` | A | Source: Tape | Verified | Evidence `room-a-source-tape-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-a-source-video-1` | A | Source: Video 1 | Verified | Original commissioning | - |
| `room-a-source-video-2` | A | Source: Video 2 | Verified | Original commissioning | - |
| `room-a-unmute` | A | Unmute | Verified | Original commissioning | - |
| `room-a-volume-up` | A | Volume up | Verified | Original commissioning | - |
| `room-b-mute` | B | Mute | Verified | Original commissioning | - |
| `room-b-power-off` | B | Power off | Verified | Original commissioning | - |
| `room-b-power-on` | B | Power on | Verified | Original commissioning | - |
| `room-b-source-am` | B | Source: AM | Verified | Original commissioning | - |
| `room-b-source-aux` | B | Source: Auxiliary | Verified | Original commissioning | - |
| `room-b-source-cd` | B | Source: CD | Verified | Evidence `room-b-source-cd-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-b-source-fm` | B | Source: FM | Verified | Evidence `room-b-source-fm-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-b-source-tape` | B | Source: Tape | Verified | Evidence `room-b-source-tape-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-b-source-video-1` | B | Source: Video 1 | Verified | Original commissioning | - |
| `room-b-source-video-2` | B | Source: Video 2 | Verified | Evidence `room-b-source-video-2-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-b-unmute` | B | Unmute | Verified | Original commissioning | - |
| `room-b-volume-down` | B | Volume down | Verified | Original commissioning | - |
| `room-b-volume-up` | B | Volume up | Verified | Original commissioning | - |
| `room-c-mute` | C | Mute | Verified | Original commissioning | - |
| `room-c-power-off` | C | Power off | Verified | Original commissioning | - |
| `room-c-power-on` | C | Power on | Verified | Original commissioning | - |
| `room-c-source-am` | C | Source: AM | Verified | Original commissioning | - |
| `room-c-source-aux` | C | Source: Auxiliary | Verified | Original commissioning | - |
| `room-c-source-cd` | C | Source: CD | Verified | Evidence `room-c-source-cd-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-c-source-fm` | C | Source: FM | Verified | Evidence `room-c-source-fm-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-c-source-tape` | C | Source: Tape | Verified | Evidence `room-c-source-tape-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-c-source-video-1` | C | Source: Video 1 | Verified | Original commissioning | - |
| `room-c-source-video-2` | C | Source: Video 2 | Verified | Evidence `room-c-source-video-2-2026-10-11.json`, banner `identified ETAP Interface V1.5; device 0001; read-only`, 2026-10-11 | - |
| `room-c-unmute` | C | Unmute | Verified | Original commissioning | - |
| `room-c-volume-down` | C | Volume down | Verified | Original commissioning | - |
| `room-c-volume-up` | C | Volume up | Verified | Original commissioning | - |
| `room-d-mute` | D | Mute | Experimental | Not verified | Untested hardware |
| `room-d-power-off` | D | Power off | Experimental | Not verified | Untested hardware |
| `room-d-power-on` | D | Power on | Experimental | Not verified | Untested hardware |
| `room-d-source-am` | D | Source: AM | Experimental | Not verified | Untested hardware |
| `room-d-source-aux` | D | Source: Auxiliary | Experimental | Not verified | Untested hardware |
| `room-d-source-cd` | D | Source: CD | Experimental | Not verified | Untested hardware |
| `room-d-source-fm` | D | Source: FM | Experimental | Not verified | Untested hardware |
| `room-d-source-tape` | D | Source: Tape | Experimental | Not verified | Untested hardware |
| `room-d-source-video-1` | D | Source: Video 1 | Experimental | Not verified | Untested hardware |
| `room-d-source-video-2` | D | Source: Video 2 | Experimental | Not verified | Untested hardware |
| `room-d-unmute` | D | Unmute | Experimental | Not verified | Untested hardware |
| `room-d-volume-down` | D | Volume down | Experimental | Not verified | Untested hardware |
| `room-d-volume-up` | D | Volume up | Experimental | Not verified | Untested hardware |
| `system-power-off` | A | Power off | Verified | Original commissioning | Alias (room A) |
| `system-power-on` | A | Power on | Verified | Original commissioning | Alias (room A) |
| `volume-down` | A | Volume down | Verified | Original commissioning | Alias (room A) |
