# Changelog

## 1.0.1

- **Fixed: the add-on did nothing at all on NVDA 2025.3 and earlier.** NVDA
  2026.1 moved the helper library: `NVDAHelper.localLib` used to be the DLL
  itself and is now a module whose `dll` attribute holds it. 1.0.0 only knew the
  new layout, so on any older NVDA the live-region hook failed to install and
  the add-on stayed completely silent while looking installed and enabled (the
  failure was only visible as one line in the NVDA log). Both layouts are now
  detected, so the add-on works on NVDA 2024.1 through 2026.x.
- Other setups where 1.0.0 could be silent:
  - the live-region callback is now hooked under either the decorated or the
    undecorated symbol name, instead of failing silently on an NVDA build that
    exports only one of them;
  - Signal Beta, Signal Desktop, Signal Dev, Signal Nightly and Signal Staging
    are recognised as Signal, not just an executable named exactly "Signal";
  - live regions delivered through UI Automation (when NVDA is set to use UIA
    for Chromium) are now handled by a Signal app module, and Signal's markers
    are read from the UIA class name / automation id in that case. Previously
    that path bypassed the add-on entirely and it announced nothing.
- New diagnostics command, **NVDA+control+shift+S**: speaks why the add-on is or
  is not announcing and copies a full report to the clipboard. Also available as
  a button in the add-on's settings. It reports the hook state (including
  whether something else has replaced the callback), how many live-region events
  have arrived and by which path, the recent live-region texts, whether NVDA is
  exposing Signal over IAccessible2 or UIA, whether Signal's markers can be read,
  and the relevant NVDA settings.

## 1.0.0

- First release. Reproduces the Orca Signal Filter add-on's clean announcements
  on Windows: "Message sent.", "Sender: text" / "Message received: text",
  "Sender is typing." -- read from Signal's rendered DOM markers, once each.
- While Signal is in front, takes over Signal's live announcements at NVDA's web
  live-region callback (nvdaControllerInternal_reportLiveRegion): noise (timers,
  timestamps) is ignored, and each change is used only as a cheap trigger for a
  bounded, cached, main-thread read of the newest message. Announcements use
  ui.message, so they appear in both speech and braille.
- Conversation switches and history scroll-back are absorbed silently.
- Announces when the other person stops typing ("Alex stopped typing"), without
  saying so when their typing simply became a message.
- No full-timeline scanning, so it does not lag NVDA. Never double-speaks; no
  other application is affected; works regardless of the "report dynamic content
  changes" setting.
- Settings category in NVDA's Settings dialog.
- **Experimental group-chat support:** incoming messages are announced with the
  sender's name, and the typing announcement attempts to name who is typing
  ("Alex and Sam are typing"). Lightly tested.
