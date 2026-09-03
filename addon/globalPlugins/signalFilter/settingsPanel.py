# Signal Filter -- settings panel.
# Copyright (C) 2026 Tobias
# This file is covered by the GNU General Public License (version 2).
# See the file COPYING.txt for more details.

import config
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel

CONFIG_SECTION = "signalFilter"


class SignalFilterSettingsPanel(SettingsPanel):
	# Translators: title of the Signal Filter category in NVDA's Settings dialog.
	title = _("Signal Filter")

	def makeSettings(self, settingsSizer):
		conf = config.conf[CONFIG_SECTION]
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		# Translators: a checkbox in Signal Filter settings.
		self._enabled = sHelper.addItem(wx.CheckBox(self, label=_("&Enable Signal Filter")))
		self._enabled.SetValue(conf["enabled"])

		# Translators: a checkbox in Signal Filter settings.
		self._announceReceived = sHelper.addItem(
			wx.CheckBox(self, label=_("Announce incoming &messages"))
		)
		self._announceReceived.SetValue(conf["announceReceived"])

		# Translators: a checkbox in Signal Filter settings.
		self._announceSent = sHelper.addItem(
			wx.CheckBox(self, label=_('Announce "Message &sent." when you send'))
		)
		self._announceSent.SetValue(conf["announceSent"])

		# Translators: a checkbox in Signal Filter settings.
		self._announceTyping = sHelper.addItem(
			wx.CheckBox(self, label=_("Announce when the other person is &typing"))
		)
		self._announceTyping.SetValue(conf["announceTyping"])

		# Translators: a checkbox in Signal Filter settings.
		self._announceTypingStopped = sHelper.addItem(
			wx.CheckBox(self, label=_("Also announce when they &stop typing"))
		)
		self._announceTypingStopped.SetValue(conf["announceTypingStopped"])

		# Translators: a checkbox in Signal Filter settings.
		self._debug = sHelper.addItem(
			wx.CheckBox(self, label=_("Log &debug information to the NVDA log"))
		)
		self._debug.SetValue(conf["debug"])

		# Translators: a button in Signal Filter settings.
		self._diagnostics = sHelper.addItem(
			wx.Button(self, label=_("&Report why Signal Filter is silent"))
		)
		self._diagnostics.Bind(wx.EVT_BUTTON, self._onDiagnostics)

	def _onDiagnostics(self, evt):
		from . import getActivePlugin

		plugin = getActivePlugin()
		if plugin is None:
			return
		plugin.reportDiagnostics()

	def onSave(self):
		conf = config.conf[CONFIG_SECTION]
		conf["enabled"] = self._enabled.GetValue()
		conf["announceReceived"] = self._announceReceived.GetValue()
		conf["announceSent"] = self._announceSent.GetValue()
		conf["announceTyping"] = self._announceTyping.GetValue()
		conf["announceTypingStopped"] = self._announceTypingStopped.GetValue()
		conf["debug"] = self._debug.GetValue()
