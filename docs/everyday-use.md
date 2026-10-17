# Everyday use

Daily use requires no settings changes. If a room or input is missing, ask the person who installed the system.

## Pick a room

Each room has its own Home Assistant card, with its name at the top. A room that the installer has not turned on is not shown on the daily card. The installer turns rooms on and off in the card's settings.

## Turn a room on or off

Press **On** or **Off** on the room's card.

## Change the volume

Press **Volume up** or **Volume down**. At the default setting a tap sends one step, and holding the button asks for a step about three times a second until you let go. The bridge accepts a step at most every quarter second in the same room (Rooms A, B and C alike), so a hold changes the volume that fast.

If the installer set more than one volume step per press, each press sends that many steps one after another, and the card ignores a repeat while the previous press is still being sent. A press ends with all of its steps, or stops at the first problem; if a press takes too long or the bridge is shutting down, the remaining steps are not sent and the card says it could not confirm.

Presses are not queued: anything pressed while another press is being sent, or faster than the system accepts, is dropped, so tap or hold again. Mute is one press per tap.

Power and input buttons wait about two and a half seconds after each other, and are dimmed while they wait.

## Mute and unmute

Press **Mute** to mute the room, and **Unmute** to turn the sound back on.

## Choose an input

Each input has a tile with its nickname and an icon. Only inputs that the installer connected appear in the daily controls, so an input that is not on the card is not connected. Press a tile to send that input to the room. Input nicknames are set in Home Assistant, not on the bridge, so a nickname may differ from the jack label on the Bose unit.

Some inputs are experimental. The bridge sends them only when that room allows unverified commands. [Commands](commands.md) shows which, and how each was verified.

## The "Last sent" label

After a successful press, the card shows **Last sent** with the input or control and how long ago it was sent.

**Last sent is not the current room state.** The bridge cannot read the room back from the Bose unit. The label shows what the bridge last sent. It does not show whether the room is on, what volume it is at, or whether it is muted. Check the room itself.

## The infrared remote

If an infrared remote is set up, its Volume up, Volume down and Mute buttons work on one room at a time. The installer chooses that room in the card's settings, under **Infrared remote**, where the card says **This remote controls** and the room's name.

After the bridge restarts, it forgets the mute state it last sent. The remote's Mute button then sends Mute, not Unmute. If the room is already muted, press Unmute on the card, or the Bose remote, to check it. Changing the room that the remote controls also forgets the mute state, and the settings warn you before you save.

## What the messages mean

Messages next to the control or at the top of the card explain what happened to the press.

| Message | What it means | What to do |
|---|---|---|
| **Not sent** and "That wasn't sent. Nothing was changed." | The bridge did not send that press, usually because it came too soon after the last one. | Wait a moment, then press again. |
| "That control is turned off." | The room or input is not enabled, or the command is not available for this room. | Ask the installer to turn it on, if you need it. |
| "We couldn't confirm that command. Check the room before pressing again." | The command may have reached the Bose unit, but the bridge did not get a clear reply. | Check the room. Do not press the same control again until you have. |
| "Can't reach the bridge computer right now." | Home Assistant cannot reach the bridge computer. It may be off, or the bridge may be stopped. | Wait a few minutes and try again. If it continues, ask the installer. |
| **FAULTED — controls paused; use the P1 remote** | The bridge has stopped app controls while a fault is latched. | Use the P1 remote. Read any supplemental message on the card and ask the installer to check the bridge. |
| "The Bose control unit declined that command. Nothing was changed." | The Bose unit refused that command. | Try the room with the original remote to check. |
| "The bridge's settings couldn't be confirmed. Ask the person who installed this system for help." | The bridge's saved settings could not be checked. | Use the original Bose remote. Ask the installer for help. |

If you see a message not listed here, note the exact words and the time, and ask the installer.

Clearing the fault latch only removes the bridge's in-memory stop. It sends no Bose command and does not test whether the M1 responds.

## Nicknames and names

Room names, input nicknames and icons are a Home Assistant feature. They change only what you see in Home Assistant. They do not change which room or input a command goes to.

## Outages

If the system stops responding, first check that the room is on and that the original Bose remote works. Then read the message on the card. Do not repeatedly press a control while a message says the command may not have been sent. Ask the installer for help if the system does not recover.
