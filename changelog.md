# Changelog

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
