# Signal Filter -- Signal app module.
# Copyright (C) 2026 Tobias
# This file is covered by the GNU General Public License (version 2).
# See the file COPYING.txt for more details.

"""A second way in, for setups where the in-process helper callback never fires.

Normally the global plugin does all the work: NVDA reads Signal (an Electron app)
over IAccessible2, and its injected in-process helper reports live regions by
calling ``nvdaControllerInternal_reportLiveRegion``, which the plugin intercepts.
That path never produces a ``liveRegionChange`` event, so this module usually
sees nothing at all.

It matters when NVDA reads Signal through UI Automation instead -- for instance
when "Use UIA with Microsoft Edge and other Chromium based browsers" is set to
"Yes".  UIA live regions arrive as ``liveRegionChange`` events and never reach
the helper callback, so without this module the add-on is completely silent on
such a machine while looking perfectly healthy.
"""

import appModuleHandler
from logHandler import log


class AppModule(appModuleHandler.AppModule):
	def event_liveRegionChange(self, obj, nextHandler):
		try:
			from globalPlugins.signalFilter import getActivePlugin

			plugin = getActivePlugin()
			if plugin is not None and plugin.handleObjectLiveRegion(obj):
				# We own this announcement; do not let NVDA also read the region.
				return
		except Exception:
			log.error("Signal Filter: error handling a live region event", exc_info=True)
		nextHandler()
