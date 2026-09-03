# Signal Filter -- diagnostics.
# Copyright (C) 2026 Tobias
# This file is covered by the GNU General Public License (version 2).
# See the file COPYING.txt for more details.

"""Builds a one-shot report explaining why the add-on is (or is not) announcing.

The add-on works by intercepting the callback NVDA's in-process helper uses to
report web live regions (the IAccessible2 path), then reading Signal's DOM
markers.  Several things outside the add-on can break that chain, and every one
of them looks identical to the user -- "installed, enabled, silent".  This report
names the broken link:

  * the plugin never loaded, or the callback could not be hooked (NVDA build);
  * the callback is hooked but is never called (Signal is being exposed through
    UI Automation instead of IAccessible2, or the app is not recognised as
    Signal);
  * the callback fires but the DOM markers cannot be read (UIA content, or a
    Signal release that renamed its CSS classes).
"""

import sys
import time

import api
import config

try:
	import buildVersion
except ImportError:  # pragma: no cover -- always present in NVDA
	buildVersion = None

CONFIG_SECTION = "signalFilter"

SETTING_KEYS = (
	"enabled",
	"announceReceived",
	"announceSent",
	"announceTyping",
	"announceTypingStopped",
	"debug",
)


def _safe(fn, default="<error>"):
	try:
		v = fn()
		return default if v is None else v
	except Exception as e:
		return "<error: %s>" % e


def _objDescription(obj):
	if obj is None:
		return "None"
	kinds = [c.__name__ for c in type(obj).__mro__]
	if any("UIA" in k for k in kinds):
		flavour = "UIA"
	elif any("IAccessible" in k for k in kinds):
		flavour = "IAccessible/IA2"
	else:
		flavour = "other"
	return "%s (api=%s, role=%s, windowClass=%s)" % (
		type(obj).__name__,
		flavour,
		_safe(lambda: str(obj.role)),
		_safe(lambda: obj.windowClassName),
	)


def _markerProbe(focus, markers):
	"""Report whether Signal's DOM markers can be read from focus upwards.

	This is the check that separates "NVDA sees Signal through IA2" (markers
	readable, so the add-on can work) from "NVDA sees Signal through UIA" or a
	redesigned Signal (markers unreadable, so it cannot)."""
	lines = []
	cur = focus
	for i in range(8):
		if cur is None:
			break
		node = cur
		ia2 = _safe(lambda: dict(node.IA2Attributes or {}), default={})
		if not isinstance(ia2, dict):
			ia2 = {}
		lines.append(
			"    [%d] role=%s ia2class=%r ia2id=%r uiaClass=%r uiaAutomationId=%r"
			% (
				i,
				_safe(lambda: str(node.role)),
				ia2.get("class", ""),
				ia2.get("id", ""),
				_safe(lambda: node.UIAElement.CurrentClassName, default=""),
				_safe(lambda: node.UIAElement.CurrentAutomationId, default=""),
			)
		)
		if _safe(lambda: markers.isMessageList(node), default=False) is True:
			lines.append("    ^ this is Signal's message list")
			break
		cur = _safe(lambda: node.parent, default=None)
	if not lines:
		lines.append("    <no focus object>")
	return lines


def buildReport(plugin, markers):
	"""Return a multi-line diagnostic report for the given plugin instance."""
	now = time.time()
	lines = []
	add = lines.append

	add("=== Signal Filter diagnostics ===")
	add("add-on version: %s" % plugin.addonVersion)
	add(
		"NVDA: %s (%d-bit python)"
		% (
			_safe(lambda: buildVersion.version) if buildVersion else "<unknown>",
			64 if sys.maxsize > 2**32 else 32,
		)
	)

	add("")
	add("-- settings --")
	for key in SETTING_KEYS:
		add("  %s = %s" % (key, _safe(lambda k=key: config.conf[CONFIG_SECTION][k])))
	add(
		"  NVDA report dynamic content changes = %s"
		% _safe(lambda: config.conf["presentation"]["reportDynamicContentChanges"])
	)
	add(
		"  NVDA UIA in Chromium = %s (0 = default, 1 = only when necessary, 2 = yes, 3 = no)"
		% _safe(lambda: config.conf["UIA"]["allowInChromium"])
	)

	add("")
	add("-- live-region hook --")
	add("  hooked: %s" % plugin.hookInstalled)
	add("  symbol: %s.%s" % (plugin.hookLayout or "<none>", plugin.hookSymbol or "<none>"))
	add("  still installed (not replaced by anything else): %s" % _safe(plugin.hookStillOurs))
	if plugin.hookError:
		add("  error: %s" % plugin.hookError)
	add("  callbacks received (helper / IAccessible2 path): %d" % plugin.helperEvents)
	add("  events received (object / UIA path): %d" % plugin.objectEvents)
	if plugin.lastEventTime:
		add("  last event: %.1fs ago" % (now - plugin.lastEventTime))
	else:
		add("  last event: never")
	add("  recent live-region texts (newest last):")
	if plugin.recentTexts:
		for eventTime, source, text in plugin.recentTexts:
			add("    %6.1fs ago [%s] %r" % (now - eventTime, source, text[:120]))
	else:
		add("    <none received>")

	add("")
	add("-- announcements --")
	add("  made this session: %d" % plugin.announceCount)
	if plugin.lastAnnounce:
		announceTime, text = plugin.lastAnnounce
		add("  last: %.1fs ago %r" % (now - announceTime, text[:120]))
	else:
		add("  last: <none>")

	add("")
	add("-- foreground application --")
	foreground = _safe(api.getForegroundObject, default=None)
	appName = _safe(lambda: foreground.appModule.appName, default="<none>")
	signalFocused = _safe(plugin.isSignalForeground, default=False) is True
	add("  appName: %r" % appName)
	add("  recognised as Signal: %s" % signalFocused)
	add("  foreground object: %s" % _objDescription(foreground))
	if not signalFocused:
		add("  NOTE: Signal is not the focused application, so the sections below")
		add("  describe whatever is focused instead. For the clearest picture, run")
		add("  this report with a Signal conversation open and focused.")

	add("")
	add("-- how NVDA sees Signal --")
	focus = _safe(api.getFocusObject, default=None)
	add("  focus object: %s" % _objDescription(focus))
	add("  markers readable from focus upwards:")
	lines.extend(_markerProbe(focus, markers))

	add("")
	add("-- message list --")
	mlist = _safe(plugin.messageListForDiagnostics, default=None)
	if mlist is None or isinstance(mlist, str):
		add("  NOT FOUND (%s)" % (mlist if isinstance(mlist, str) else "no match"))
	else:
		add(
			"  found: %s, children=%s"
			% (_objDescription(mlist), _safe(lambda: mlist.childCount))
		)
	add("  acquisition path last used: %s" % (plugin.listPath or "<none>"))

	add("")
	add("-- verdict --")
	lines.extend(_verdict(plugin))
	return "\n".join(str(line) for line in lines)


def spokenSummary(plugin):
	"""The verdict as one speakable sentence."""
	return " ".join(line.strip() for line in _verdict(plugin))


def _verdict(plugin):
	"""Name the broken link in the chain, in plain language."""
	out = []
	if not plugin.hookInstalled:
		out.append("  The live-region callback could not be hooked, so the add-on can")
		out.append("  never announce anything.  See the error above.")
		return out
	if _safe(lambda: config.conf[CONFIG_SECTION]["enabled"], default=False) is not True:
		out.append("  The add-on is switched off in its own settings.")
		return out
	if _safe(plugin.hookStillOurs, default=None) is False:
		out.append("  Something else has replaced the live-region callback since the")
		out.append("  add-on installed it -- another add-on that patches the same NVDA")
		out.append("  callback, or a plugin reload.  Restarting NVDA restores it.")
		return out
	if plugin.helperEvents == 0 and plugin.objectEvents == 0:
		out.append("  The hook is installed but has never been called while Signal was")
		out.append("  in the foreground.  Either Signal has not been used since NVDA")
		out.append("  started, or NVDA is not seeing Signal's live regions at all.")
		out.append("  Check that 'recognised as Signal' above is True, and that NVDA's")
		out.append("  Advanced setting 'Use UIA with Microsoft Edge and other Chromium")
		out.append("  based browsers' is not set to 'Yes'.")
		return out
	if plugin.helperEvents == 0 and plugin.objectEvents > 0:
		out.append("  Live regions are arriving through the UI Automation path rather")
		out.append("  than IAccessible2.  Set NVDA's Advanced setting 'Use UIA with")
		out.append("  Microsoft Edge and other Chromium based browsers' to 'Only when")
		out.append("  necessary', then restart Signal.")
		return out
	if plugin.announceCount == 0:
		out.append("  Live regions are arriving, but Signal's markers could not be read,")
		out.append("  so no announcement could be built.  Look at the marker probe above:")
		out.append("  if ia2class is empty everywhere, NVDA is not using IAccessible2 for")
		out.append("  Signal; if it is populated but never contains")
		out.append("  'module-timeline__messages', this Signal release renamed its CSS")
		out.append("  classes and the add-on needs updating.")
		return out
	out.append("  Events arrive and announcements have been made: the add-on is working.")
	return out
