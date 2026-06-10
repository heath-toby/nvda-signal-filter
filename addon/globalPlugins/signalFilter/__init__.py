# Signal Filter -- globalPlugin.
# Copyright (C) 2026 Tobias
# This file is covered by the GNU General Public License (version 2).
# See the file COPYING.txt for more details.

"""Clean, Orca-style announcements for Signal Desktop under NVDA.

This is the Windows counterpart of the Orca Signal Filter add-on.  It reproduces
that add-on's lovely, clean announcements -- "Message sent.", "Alex: <text>" /
"Message received: <text>", "Alex is typing." -- by reading Signal's real
rendered DOM markers (the same ones Orca uses, which exist identically in the
Windows Electron build):

    module-timeline__messages          -> the message list
      module-message ... --incoming|--outgoing       -> direction
      id="message-accessibility-contents:<uuid>"      -> per-message id (dedup key)
        module-message__text            -> clean body text (no timestamp)
        module-message__author           -> sender name in group chats

How it is driven (and why it is not slow)
-----------------------------------------
For web content NVDA announces ARIA live regions via its in-process helper, which
calls ``NVDAHelper.nvdaControllerInternal_reportLiveRegion(text, politeness)``.
We overwrite that callback's DLL function pointer (exactly as NVDA installs it).
While Signal is the foreground app our callback:

  * returns 0 for everything, so NVDA never speaks Signal's noisy live regions
    itself (no spam, no double-speak, regardless of the "report dynamic content
    changes" setting);
  * cheaply ignores obvious noise (timestamps / playback timers) by text, on the
    spot;
  * for anything that might be a real change, queues a tiny, bounded read of just
    the newest message(s) onto NVDA's MAIN thread, which produces the clean
    announcement.

The expensive accessibility work (reading the DOM) therefore happens on the main
thread (where COM access is safe), only for non-noise events, bounded to the tail
of the message list, and using cached objects -- so there is none of the
cross-process-COM lag that an earlier scan-everything design caused.  For every
other application the original handler is called unchanged.
"""

import re
import time
from collections import OrderedDict
from ctypes import WINFUNCTYPE, c_long, c_wchar_p

import api
import config
import controlTypes
import core
import globalPluginHandler
import NVDAHelper
import queueHandler
import ui
from gui.settingsDialogs import NVDASettingsDialog
from logHandler import log

from .settingsPanel import SignalFilterSettingsPanel

CONFIG_SECTION = "signalFilter"

CONFIG_SPEC = {
	"enabled": "boolean(default=True)",
	"announceSent": "boolean(default=True)",
	"announceReceived": "boolean(default=True)",
	"announceTyping": "boolean(default=True)",
	"announceTypingStopped": "boolean(default=True)",
	"debug": "boolean(default=False)",
}

# Fallback defaults if the spec somehow isn't registered when read.
_CONFIG_DEFAULTS = {
	"enabled": True,
	"announceSent": True,
	"announceReceived": True,
	"announceTyping": True,
	"announceTypingStopped": True,
	"debug": False,
}

# --- NVDA live-region callback we hook ------------------------------------
_FUNC_POINTER_NAME = "_nvdaControllerInternal_reportLiveRegion"

# --- Signal DOM markers (verified against the installed Electron build) ----
CLS_MESSAGE_LIST = "module-timeline__messages"
CLS_MSG_TEXT = "module-message__text"
CLS_AUTHOR = "module-message__author"
CLS_TYPING = "module-typing-animation"
CLS_TYPING_BUBBLE = "module-message--typing-bubble"
CLS_MSG_GROUP = "module-message--group"  # on the typing bubble only in group chats
CLS_TYPING_AVATAR = "module-message__typing-avatar"  # one per typist (group), name = contact
CLS_TYPING_AVATAR_SPACER = "module-message__typing-avatar-spacer"  # contains the above as a substring!
CLS_TYPING_AVATAR_CONTAINER = "module-message__author-avatar-container--typing"
CLS_TYPING_AVATAR_OVERFLOW = "--overflow-count"  # the "+N more typists" avatar
CLS_HEADER_TITLE = "module-ConversationHeader__header__info__title"
CLS_HEADER_BTN = "module-ConversationHeader__header--clickable"
CLS_TIMELINE_REGION = "ConversationView__timeline"
CLS_TIMELINE = "module-timeline"
ID_MSG_PREFIX = "message-accessibility-contents:"
ID_DESC_PREFIX = "message-accessibility-description:"
CONV_ID_PREFIX = "conversation-"

# Live-region text that is pure noise -- a relative/clock timestamp or a voice
# note's playback time -- so we needn't even look at the DOM for it.
_NOISE_RE = re.compile(
	r"^("
	r"now|yesterday"
	r"|\d+\s*[smhdwy]"
	r"|\d{1,2}:\d{2}(:\d{2})?(\s*[ap]\.?m\.?)?(\s*/\s*\d{1,2}:\d{2}(:\d{2})?)?"
	r")$",
	re.IGNORECASE,
)
# Signal's typing-indicator live-region text -- the standalone accessible name of
# the typing bubble, e.g. "Typing animation for this chat" / "Typing indicator
# for this chat".  Matched ANCHORED AT THE START (and only on short text), so a
# long live-region blob that merely CONTAINS the phrase (e.g. a whole-timeline
# announcement that includes the bubble's title) is not mistaken for typing.
_TYPING_TEXT_RE = re.compile(r"typing\s+(indicator|animation)\b", re.IGNORECASE)
_TYPING_TEXT_MAXLEN = 64

_ISOLATES = dict.fromkeys(map(ord, "⁦⁧⁨⁩‪‫‬‭‮‎‏"), None)

SCAN_TAIL = 4
ANNOUNCE_MAX = 4  # more new than this at once => backlog/history, stay silent
SEEN_CAP = 1000
PRIME_GRACE_SECONDS = 1.5
# The expensive whole-document list search is a last resort; never run it more
# often than this.  With no conversation open there is no list to find, and an
# unthrottled search on every chat-list update freezes NVDA.
LIST_DOC_SEARCH_RETRY = 6.0
LIST_DOC_SEARCH_MAXNODES = 4000
TYPING_POLL_MS = 1200  # how often to check whether the typing bubble is still up
TYPING_MESSAGE_WINDOW = 2.0  # don't say "stopped typing" within this long of a message


# ---------------------------------------------------------------------------
# Accessibility-tree helpers (run on the main thread)
# ---------------------------------------------------------------------------

def _ia2(obj):
	try:
		return obj.IA2Attributes or {}
	except Exception:
		return {}


def _cls(obj):
	return _ia2(obj).get("class", "")


def _id(obj):
	return _ia2(obj).get("id", "")


def _strip(s):
	if not s:
		return ""
	return s.translate(_ISOLATES).strip()


def _role(obj):
	try:
		return obj.role
	except Exception:
		return None


def _name(obj):
	try:
		return obj.name or ""
	except Exception:
		return ""


def _childCount(obj):
	try:
		return obj.childCount or 0
	except Exception:
		return 0


def _getChild(obj, index):
	try:
		return obj.getChild(index)
	except Exception:
		return None


def _findDescendant(root, pred, maxDepth=10, maxNodes=300):
	if root is None:
		return None
	stack = [(root, 0)]
	budget = maxNodes
	while stack and budget > 0:
		node, depth = stack.pop()
		budget -= 1
		try:
			if pred(node):
				return node
		except Exception:
			pass
		if depth >= maxDepth:
			continue
		cnt = _childCount(node)
		for i in range(cnt - 1, -1, -1):
			ch = _getChild(node, i)
			if ch is not None:
				stack.append((ch, depth + 1))
	return None


def _gatherText(node, maxDepth=10):
	if node is None:
		return ""
	parts = []
	stack = [(node, 0)]
	while stack:
		n, depth = stack.pop()
		cnt = _childCount(n)
		if cnt == 0:
			try:
				nm = n.name
			except Exception:
				nm = None
			if nm:
				parts.append(nm)
		elif depth < maxDepth:
			for i in range(cnt - 1, -1, -1):
				ch = _getChild(n, i)
				if ch is not None:
					stack.append((ch, depth + 1))
	return _strip(" ".join(parts))


def _ancestorMatching(obj, pred, maxUp=20):
	cur = obj
	for _ in range(maxUp):
		if cur is None:
			return None
		try:
			if pred(cur):
				return cur
		except Exception:
			pass
		try:
			cur = cur.parent
		except Exception:
			return None
	return None


def _isTimelineRegion(o):
	toks = _cls(o).split()
	return bool(toks) and (CLS_TIMELINE_REGION in toks or toks[0] == CLS_TIMELINE)


def _isMessageList(o):
	"""The real message list: class module-timeline__messages AND role list.
	The role check is essential -- an outer landmark/container shares the same
	class prefix, and a top-down search would otherwise grab that wrong element
	(whose children are not messages), producing silence."""
	return CLS_MESSAGE_LIST in _cls(o) and _role(o) == controlTypes.Role.LIST


def _typingNode(o):
	c = _cls(o)
	return CLS_TYPING in c or CLS_TYPING_BUBBLE in c


def _namedDescendants(root, maxDepth=6, maxNodes=80, limit=6):
	"""Collect the accessible names of the first few NAMED nodes under root, in
	document order, without descending into a named node (its children usually
	just echo its label).  Bounded -- used only when typing starts.  This reads
	whatever node actually carries the name, which matters because Chromium
	prunes unlabeled plain divs from the tree, so the labelled node may sit at
	an unpredictable depth."""
	out = []
	stack = [(root, 0)]
	budget = maxNodes
	while stack and budget > 0 and len(out) < limit:
		o, d = stack.pop()
		budget -= 1
		nm = _strip(_name(o))
		if nm:
			out.append(nm)
			continue
		if d < maxDepth:
			cnt = _childCount(o)
			for i in range(min(cnt, 8) - 1, -1, -1):
				ch = _getChild(o, i)
				if ch is not None:
					stack.append((ch, d + 1))
	return out


def _convContainer(node):
	return _ancestorMatching(node, lambda o: _id(o).startswith(CONV_ID_PREFIX), maxUp=25)


def _messageContainer(child):
	return _findDescendant(
		child, lambda o: _id(o).startswith(ID_MSG_PREFIX), maxDepth=8, maxNodes=150
	)


def _fallbackText(container, uuid):
	wrapper = _ancestorMatching(container, lambda o: "module-message__wrapper" in _cls(o), maxUp=6)
	root = wrapper or container
	desc = _findDescendant(
		root, lambda o: _id(o) == ID_DESC_PREFIX + uuid[len(ID_MSG_PREFIX):], maxDepth=10, maxNodes=300
	)
	if desc is not None:
		txt = _gatherText(desc)
		if txt:
			return txt
	if wrapper is not None:
		try:
			nm = _strip(wrapper.name)
			return re.sub(r"\s*(?:Now|\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)\s*$", "", nm).strip()
		except Exception:
			pass
	return ""


def _messageInfo(container):
	uuid = _id(container)
	if not uuid.startswith(ID_MSG_PREFIX):
		return None
	outgoing = "--outgoing" in _cls(container)

	textNode = _findDescendant(container, lambda o: CLS_MSG_TEXT in _cls(o), maxDepth=10, maxNodes=300)
	text = _gatherText(textNode) if textNode is not None else ""
	if not text:
		text = _fallbackText(container, uuid)

	author = None
	if not outgoing:
		anode = _findDescendant(container, lambda o: CLS_AUTHOR in _cls(o), maxDepth=10, maxNodes=300)
		if anode is not None:
			try:
				author = _strip(anode.name) or _gatherText(anode) or None
			except Exception:
				author = _gatherText(anode) or None

	return {"uuid": uuid, "outgoing": outgoing, "author": author, "text": text}


def _getContact(convContainer):
	if convContainer is None:
		return None
	node = _findDescendant(convContainer, lambda o: CLS_HEADER_BTN in _cls(o), maxDepth=12, maxNodes=1200)
	if node is not None:
		try:
			raw = _strip(node.name)
		except Exception:
			raw = ""
		raw = re.split(r"\s+(?:This (?:person|group|is)\b|Verified\b|·)", raw)[0].strip()
		if raw:
			return raw
	node = _findDescendant(convContainer, lambda o: CLS_HEADER_TITLE in _cls(o), maxDepth=12, maxNodes=1200)
	if node is not None:
		try:
			name = _strip(node.name) or _gatherText(node)
		except Exception:
			name = _gatherText(node)
		if name:
			return name
	return None


def _isSignalForeground():
	try:
		fg = api.getForegroundObject()
		app = fg.appModule if fg is not None else None
		return app is not None and (app.appName or "").lower() == "signal"
	except Exception:
		return False


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self):
		super().__init__()
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		NVDASettingsDialog.categoryClasses.append(SignalFilterSettingsPanel)

		self._seen = OrderedDict()  # message uuid -> first-seen time (whole session)
		self._convFirstSeen = {}  # conversation id -> first time we scanned it
		self._contactCache = {}  # conversation id -> header contact name
		self._mlist = None
		self._region = None
		self._convContainerObj = None
		self._conv = ""
		self._typingActive = False
		self._typingPollTimer = None
		self._typingMisses = 0
		self._typingSubject = None  # cached spoken subject for the current typing
		self._lastReceivedTime = 0.0
		self._lastDocSearch = 0.0
		self._scanQueued = False

		self._patched = False
		self._original = NVDAHelper.nvdaControllerInternal_reportLiveRegion
		self._hook = WINFUNCTYPE(c_long, c_wchar_p, c_wchar_p)(self._reportLiveRegion)
		try:
			NVDAHelper._setDllFuncPointer(NVDAHelper.localLib.dll, _FUNC_POINTER_NAME, self._hook)
			self._patched = True
			log.info("Signal Filter: live-region reporter hooked")
		except Exception:
			log.error("Signal Filter: could not hook the live-region reporter", exc_info=True)

	def terminate(self, *args, **kwargs):
		if self._patched:
			try:
				NVDAHelper._setDllFuncPointer(NVDAHelper.localLib.dll, _FUNC_POINTER_NAME, self._original)
			except Exception:
				log.error("Signal Filter: could not restore the live-region reporter", exc_info=True)
			self._patched = False
		if self._typingPollTimer is not None:
			try:
				self._typingPollTimer.Stop()
			except Exception:
				pass
			self._typingPollTimer = None
		try:
			NVDASettingsDialog.categoryClasses.remove(SignalFilterSettingsPanel)
		except ValueError:
			pass
		super().terminate(*args, **kwargs)

	# -- the intercepted callback (runs on NVDA's RPC thread) --------------

	def _reportLiveRegion(self, text, politeness):
		"""Cheap and must never raise into the C caller.  Does NO accessibility
		work here -- it only classifies the text and queues main-thread work."""
		try:
			if not self._shouldHandle():
				return int(self._original(text, politeness))
			s = _strip(text)
			if not s:
				return 0
			if _NOISE_RE.match(s):
				return 0
			if len(s) <= _TYPING_TEXT_MAXLEN and _TYPING_TEXT_RE.match(s):
				queueHandler.queueFunction(queueHandler.eventQueue, self._onTyping)
				return 0
			# Something changed that might be a message -> read it on the main
			# thread.  Collapse a burst into a single queued scan.
			if not self._scanQueued:
				self._scanQueued = True
				queueHandler.queueFunction(queueHandler.eventQueue, self._onMessageEvent)
			return 0
		except Exception:
			log.error("Signal Filter: live-region handler error", exc_info=True)
			return -1

	def _shouldHandle(self):
		try:
			if not config.conf[CONFIG_SECTION]["enabled"]:
				return False
		except Exception:
			return False
		return _isSignalForeground()

	# -- locating the message list (cached; main thread) -------------------

	def _messageList(self):
		m = self._mlist
		if m is not None:
			try:
				if _isMessageList(m):
					_ = m.childCount
					return m
			except Exception:
				pass
			self._mlist = self._region = self._convContainerObj = None
			self._conv = ""

		if not _isSignalForeground():
			return None

		def _add(lst, x):
			if x is not None and not any(e is x for e in lst):
				lst.append(x)

		found = None
		convRoots = []  # small: the enclosing conversation container(s)
		docRoots = []  # large: whole-document roots (last resort)
		for getter in (api.getFocusObject, api.getNavigatorObject):
			try:
				o = getter()
			except Exception:
				o = None
			if o is None:
				continue
			# (a) Cheapest: focus is already inside the list -> walk straight up.
			up = _ancestorMatching(o, _isMessageList, maxUp=25)
			if up is not None:
				found = up
				break
			# (b) The enclosing conversation container -- a small, relevant subtree
			# to search down (the list sits near its top, so this is cheap even
			# when focus is in the compose box).
			_add(convRoots, _ancestorMatching(o, lambda x: _id(x).startswith(CONV_ID_PREFIX), maxUp=30))
			# (c) The browse-mode document root, for the rare last-resort search.
			ti = getattr(o, "treeInterceptor", None)
			_add(docRoots, getattr(ti, "rootNVDAObject", None) if ti is not None else None)
		if found is None:
			for cc in convRoots:
				found = _findDescendant(cc, _isMessageList, maxDepth=20, maxNodes=2500)
				if found is not None:
					break
		if found is None and docRoots:
			# Last resort: search the whole document.  This is expensive and, when
			# no conversation is open, fruitless -- so run it at most once every
			# few seconds, or it will freeze NVDA on chat-list churn.
			now = time.time()
			if (now - self._lastDocSearch) >= LIST_DOC_SEARCH_RETRY:
				self._lastDocSearch = now
				for root in docRoots:
					found = _findDescendant(
						root, _isMessageList, maxDepth=30, maxNodes=LIST_DOC_SEARCH_MAXNODES
					)
					if found is not None:
						break
		if found is not None:
			self._mlist = found
			self._region = _ancestorMatching(found, _isTimelineRegion, maxUp=10)
			self._convContainerObj = _convContainer(found)
			self._conv = _id(self._convContainerObj) if self._convContainerObj is not None else ""
		return found

	# -- reading new messages (main thread) --------------------------------

	def _onMessageEvent(self):
		self._scanQueued = False
		if not self._cfg("enabled"):
			return
		mlist = self._messageList()
		if mlist is None:
			return
		try:
			n = _childCount(mlist)
		except Exception:
			self._mlist = None
			return

		conv = self._conv
		now = time.time()
		firstSeen = self._convFirstSeen.get(conv)
		if firstSeen is None:
			firstSeen = self._convFirstSeen[conv] = now
		priming = (now - firstSeen) < PRIME_GRACE_SECONDS

		newMsgs = []
		for i in range(n - 1, max(-1, n - 1 - SCAN_TAIL), -1):
			child = _getChild(mlist, i)
			if child is None:
				continue
			container = _messageContainer(child)
			if container is None:
				continue
			uuid = _id(container)
			if uuid in self._seen:
				break
			info = _messageInfo(container)
			if info:
				newMsgs.append(info)
		newMsgs.reverse()

		if not newMsgs:
			return

		if priming or len(newMsgs) > ANNOUNCE_MAX:
			for m in newMsgs:
				self._remember(m["uuid"])
			self._debug("absorbed %d message(s) silently" % len(newMsgs))
			return

		for m in newMsgs:
			self._remember(m["uuid"])
			if m["outgoing"]:
				if self._cfg("announceSent"):
					self._debug("ANNOUNCE Message sent.")
					# Translators: announced when the user's own message is sent.
					ui.message(_("Message sent."))
				continue
			# Incoming: the sender's typing has just ended by becoming this
			# message, so end any typing state WITHOUT announcing "stopped".
			self._lastReceivedTime = time.time()
			self._endTyping(announce=False)
			if self._cfg("announceReceived"):
				if m["author"]:
					self._debug("ANNOUNCE %r: %r" % (m["author"], m["text"]))
					ui.message("%s: %s" % (m["author"], m["text"]))
				else:
					self._debug("ANNOUNCE received %r" % m["text"])
					# Translators: an incoming message with no separate sender name
					# (1:1 chats); {text} is the message body.
					ui.message(_("Message received: {text}").format(text=m["text"]))

	def _remember(self, uuid):
		self._seen[uuid] = time.time()
		while len(self._seen) > SEEN_CAP:
			self._seen.popitem(last=False)

	# -- typing (main thread) ----------------------------------------------

	def _typingNames(self):
		"""For a group chat, read the names of who is typing from the typing
		bubble's avatars (class module-message__typing-avatar, accessible name =
		the contact).  Returns (names, overflow, isGroup); a 1:1 bubble has no
		avatars and no --group class, so it returns ([], False, False).  Runs once
		when typing starts, not on the poll."""
		mlist = self._mlist or self._messageList()
		if mlist is None:
			return [], False, False

		avatars = []
		containers = []
		isGroup = False

		def gather(root):
			nonlocal isGroup
			stack = [(root, 0)]
			budget = 120
			while stack and budget > 0:
				o, d = stack.pop()
				budget -= 1
				c = _cls(o)
				if CLS_MESSAGE_LIST in c:
					continue  # never crawl the message list
				if CLS_TYPING_BUBBLE in c and CLS_MSG_GROUP in c:
					# Signal puts --group on the typing bubble only in group chats.
					isGroup = True
				if CLS_TYPING_AVATAR_SPACER in c:
					# The spacer's class CONTAINS the avatar class as a substring;
					# skip it before the avatar check or it becomes a phantom avatar.
					continue
				if CLS_TYPING_AVATAR in c:
					avatars.append(o)
					continue
				if CLS_TYPING_AVATAR_CONTAINER in c:
					containers.append(o)
					# fall through: the real avatars are its children
				if d < 8:
					cnt = _childCount(o)
					# Push in reverse so the LIFO pops children in document order
					# (otherwise the spoken names come out reversed).
					for i in range(min(cnt, 8) - 1, -1, -1):
						ch = _getChild(o, i)
						if ch is not None:
							stack.append((ch, d + 1))

		n = _childCount(mlist)
		for i in range(n - 1, max(-1, n - 1 - 3), -1):
			ch = _getChild(mlist, i)
			if ch is not None:
				gather(ch)
		region = self._region
		if region is not None:
			try:
				rn = _childCount(region)
			except Exception:
				rn = 0
			for i in range(rn - 1, max(-1, rn - 1 - 3), -1):
				ch = _getChild(region, i)
				if ch is not None and ch is not mlist:
					gather(ch)

		names = []
		overflow = False
		for av in avatars:
			if CLS_TYPING_AVATAR_OVERFLOW in _cls(av):
				overflow = True
				continue
			nm = _strip(_name(av))
			if not nm:
				# The avatar div itself is usually unlabeled; the name sits on a
				# labelled node (e.g. the clickable avatar) somewhere below it.
				inner = _namedDescendants(av, maxDepth=5, maxNodes=40, limit=1)
				nm = inner[0] if inner else ""
			if nm and nm not in names:
				names.append(nm)

		if not names and containers:
			# Chromium may prune the unlabeled per-typist avatar divs entirely (it
			# does this to Signal's status icon), leaving only the labelled nodes.
			# Read named descendants of the avatar container directly; a bare
			# "+N" name is the overflow indicator, not a contact.
			for cont in containers:
				for nm in _namedDescendants(cont, maxDepth=6, maxNodes=80, limit=6):
					if re.fullmatch(r"\+\s*\d{1,3}", nm):
						overflow = True
						continue
					if nm not in names:
						names.append(nm)

		# Avatars/containers only render in group chats, so they also prove
		# group-ness even if the --group class was missed.
		isGroup = isGroup or bool(avatars) or bool(containers)
		self._debug(
			"typing avatars: %d containers: %d names=%r overflow=%s group=%s"
			% (len(avatars), len(containers), names, overflow, isGroup)
		)
		return names, overflow, isGroup

	def _buildTypingSubject(self, names, overflow):
		"""Build the spoken subject + a plural flag from the typist names."""
		if not names:
			# Translators: spoken when the typist's name is unknown.
			return _("Someone"), False
		if overflow or len(names) > 3:
			# Translators: typing subject when several people type; {first} is a name.
			return _("{first} and others").format(first=names[0]), True
		if len(names) == 1:
			return names[0], False
		if len(names) == 2:
			# Translators: subject naming two people; {first} and {second} are names.
			return _("{first} and {second}").format(first=names[0], second=names[1]), True
		# exactly three
		# Translators: subject naming three people; first/second/third are names.
		return (
			_("{first}, {second} and {third}").format(
				first=names[0], second=names[1], third=names[2]
			),
			True,
		)

	def _onTyping(self):
		if not self._cfg("enabled"):
			return
		announceStart = self._cfg("announceTyping")
		# Latch the typing state even when the start announcement is off, so the
		# independent "stopped typing" option still works on its own.
		if not (announceStart or self._cfg("announceTypingStopped")):
			return
		if not self._typingActive:
			self._typingActive = True
			if self._mlist is None:
				self._messageList()
			# In a group the typing bubble names the typists.  In a 1:1 there are
			# no typist avatars, so use the conversation's contact name.  In a
			# group where the names can't be read, say "Someone" -- announcing the
			# GROUP'S name as if the group itself were typing sounds wrong.
			names, overflow, isGroup = self._typingNames()
			if names:
				subject, plural = self._buildTypingSubject(names, overflow)
			elif isGroup:
				subject, plural = _("Someone"), False
			else:
				subject, plural = (self._contact() or _("Someone")), False
			self._typingSubject = subject
			if announceStart:
				if plural:
					# Translators: announced when several people are typing; {who}
					# is a list of names.
					ui.message(_("{who} are typing.").format(who=subject))
				else:
					# Translators: announced when the other person starts typing;
					# {who} is the contact name.
					ui.message(_("{who} is typing.").format(who=subject))
		# A fresh typing event means they're still at it -- reset the miss count.
		self._typingMisses = 0
		# Removals don't fire live-region events, so poll the DOM for the typing
		# bubble while it is up; we announce "stopped typing" when it disappears.
		self._startTypingPoll()

	def _startTypingPoll(self):
		if self._typingPollTimer is not None:
			try:
				self._typingPollTimer.Stop()
			except Exception:
				pass
		self._typingPollTimer = core.callLater(TYPING_POLL_MS, self._pollTyping)

	def _typingPresent(self, mlist):
		"""Cheap, bounded check for the typing bubble, run repeatedly on the poll.
		The bubble sits among the LAST few children of the message list, so we
		look only there with a small budget -- and we NEVER descend into the
		message list itself (it can hold hundreds of message nodes, and walking it
		every poll is what caused the lag)."""

		def hasTyping(node):
			if CLS_MESSAGE_LIST in _cls(node):
				return False  # don't crawl the whole message list
			return _typingNode(node) or _findDescendant(node, _typingNode, maxDepth=6, maxNodes=80) is not None

		n = _childCount(mlist)
		for i in range(n - 1, max(-1, n - 1 - 4), -1):
			ch = _getChild(mlist, i)
			if ch is not None and hasTyping(ch):
				return True
		region = self._region
		if region is not None:
			try:
				rn = _childCount(region)
			except Exception:
				rn = 0
			for i in range(rn - 1, max(-1, rn - 1 - 4), -1):
				ch = _getChild(region, i)
				if ch is not None and ch is not mlist and hasTyping(ch):
					return True
		return False

	def _pollTyping(self):
		self._typingPollTimer = None
		if not self._typingActive:
			return
		mlist = self._mlist or self._messageList()
		present = bool(mlist is not None and self._typingPresent(mlist))
		if present:
			self._typingMisses = 0
			self._startTypingPoll()
			return
		# Require two consecutive absences before declaring "stopped", so a single
		# transient miss doesn't misfire.
		self._typingMisses += 1
		if self._typingMisses < 2:
			self._startTypingPoll()
			return
		# The indicator is gone.  Suppress "stopped" if it vanished because a
		# message just arrived (typing became a message), or if we couldn't
		# actually verify the timeline.
		recentMessage = (time.time() - self._lastReceivedTime) < TYPING_MESSAGE_WINDOW
		self._endTyping(announce=(mlist is not None and not recentMessage))

	def _endTyping(self, announce):
		wasActive = self._typingActive
		self._typingActive = False
		self._typingMisses = 0
		if self._typingPollTimer is not None:
			try:
				self._typingPollTimer.Stop()
			except Exception:
				pass
			self._typingPollTimer = None
		if announce and wasActive and self._cfg("announceTypingStopped"):
			# Reuse the same subject we announced for "is typing" (names in a
			# group, contact in a 1:1); "stopped typing" reads fine for both.
			who = self._typingSubject or self._contact() or _("Someone")
			self._debug("ANNOUNCE stopped typing")
			# Translators: announced when the other person stops typing without
			# sending a message; {who} is the contact name (or list of names).
			ui.message(_("{who} stopped typing.").format(who=who))
		self._typingSubject = None

	def _contact(self):
		conv = self._conv
		if conv and conv in self._contactCache:
			return self._contactCache[conv]
		name = _getContact(self._convContainerObj)
		if conv and name:
			self._contactCache[conv] = name
		return name

	# -- misc --------------------------------------------------------------

	def _cfg(self, key):
		try:
			return config.conf[CONFIG_SECTION][key]
		except Exception:
			return _CONFIG_DEFAULTS[key]

	def _debug(self, msg):
		try:
			if config.conf[CONFIG_SECTION]["debug"]:
				log.info("Signal Filter: %s" % msg)
		except Exception:
			pass
