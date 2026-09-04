# Changelog

## 1.0.3

- **Fixed: the add-on was silent when BrowserNav (or any other add-on that hooks
  NVDA's live-region callback) was also installed.** Such an add-on reads
  `NVDAHelper.nvdaControllerInternal_reportLiveRegion` to find the handler it
  should chain through. Signal Filter only overwrote the DLL function pointer
  and left that attribute alone, so an add-on loading after it saved NVDA's own
  reporter as "the original" and overwrote the pointer -- cutting Signal Filter
  out of the chain completely. It now publishes its callback under that
  attribute too, so the two chain in either load order.
- Unhooking no longer rips out an add-on that hooked on top of this one: the DLL
  pointer is only taken back when it is still ours, and the callback becomes a
  pass-through once the plugin has terminated.
- Chaining on to a handler that is another add-on's plain Python function, rather
  than a ctypes callback, no longer logs an exception per live region.
- The diagnostic report (NVDA+control+shift+S) now names the other add-ons that
  hook the same callback, and no longer reports being hooked over as a fault when
  calls are still arriving. Note that with BrowserNav installed, "still the
  outermost hook: False" is the healthy state, not a fault.
- The report also gains a "last message scan" section: the conversation id, how
  the message list was acquired, whether the scan was the conversation's first
  (which absorbs silently by design), and a per-child account of what the newest
  children looked like. This distinguishes normal priming from a renamed Signal
  marker or the wrong element being found as the list.
- Fixed a misleading verdict: "Signal's markers could not be read" was reported
  even when the message list had been found and read perfectly well.

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
