# Signal Filter for NVDA

An NVDA add-on that quietens [Signal Desktop](https://signal.org/download/) for
screen-reader users. It is a Windows/NVDA counterpart to the Orca Signal Filter
add-on for Linux.

Signal Desktop is a web (Chromium/Electron) app, so NVDA reads its constantly
updating `aria-live` regions and announces a lot of noise: a voice message's
playback timer reading out second by second, "Now" and clock timestamps after
messages, and the same message double-spoken. This add-on removes that noise while
leaving the announcements you actually want.

## What it does

While Signal is the focused application, the add-on takes over Signal's "live"
announcements and replaces the noise with clean speech and braille, exactly like
the Orca add-on does on Linux:

- `Message sent.` when you send a message.
- `Alex: <text>` (or `<sender>: <text>` in groups, `Message received: <text>`
  in 1:1 chats) when one arrives.
- `Alex is typing.` when the other person is typing — and in groups,
  `Alex and Sam are typing.` / `Alex and others are typing.` (experimental,
  see below).
- `Alex stopped typing.` when they stop without sending.

The noise — a voice note's playback timer, relative and clock timestamps
("5m", "15m", "Now", "11:42"), the per-message double-speak — is gone. Each
message is announced once. Conversation switches and history scroll-back are
absorbed silently, so old messages are never re-announced.

When Signal is not the focused application — or when you switch the add-on off —
nothing is changed, so no other program is affected. Because the add-on fully
owns Signal's live announcements, it works whether or not NVDA's "report dynamic
content changes" option is on, and there is never any double-speaking.

## Group chats (experimental)

Group support is included but **very experimental and lightly tested**:

- **Incoming messages** in a group are announced with the sender, e.g.
  `Alex: <text>` — this works the same way as 1:1.
- **Typing in groups** attempts to name the people typing, read from the typing
  indicator's avatars: `Alex is typing.`, `Alex and Sam are typing.`,
  `Alex, Sam and Jo are typing.`, or `Alex and others are typing.` If the
  names can't be read it falls back to "Someone is typing." This path has had
  little real-world testing — please report anything odd.

## Other limitations

- **Typing attribution:** the typing announcement relates to whichever
  conversation is currently open. In the rare case the indicator relates to a
  different chat, the name(s) spoken will be from the open one.

## Requirements

- NVDA 2024.1 or later
- Signal Desktop

## Settings

Open NVDA menu → Preferences → Settings → **Signal Filter** to toggle: announcing
on/off, ignoring timestamps and playback timers, ignoring repeated announcements,
the duplicate window (seconds), and a debug log (written to the NVDA log).

## How it works

For web content, NVDA does not announce ARIA live regions through the usual
object event — its injected in-process helper detects the change and calls back
into NVDA through `nvdaControllerInternal_reportLiveRegion(text, politeness)`,
which speaks the text. That callback is the single place every live-region
announcement passes through.

This add-on (a global plugin) replaces that callback with its own. While Signal is
in front, the callback never lets NVDA speak Signal's raw live regions; instead it
uses each change merely as a cheap trigger. It ignores obvious noise (timestamps,
timers) by text on the spot, and for anything that might be a real message it
queues a small, bounded read of just the newest message(s) onto NVDA's main
thread. That read inspects Signal's rendered DOM markers (`module-message__text`,
the `--incoming`/`--outgoing` direction class, the per-message id, the group
sender) to produce the clean announcement above — the same markers the Orca
add-on reads.

The DOM read happens only for non-noise events, is bounded to the tail of the
message list, and reuses cached objects, so it does not cause the cross-process
lag that an earlier scan-everything design did. For every other application the
original handler is called unchanged. The original callback is restored when the
add-on is unloaded.

## Credits

Built collaboratively with AI assistance, designed and tested by a daily Signal
user. Inspired by the Orca Signal Filter add-on for Linux.

## License

GNU General Public License version 2 — see COPYING.txt.
