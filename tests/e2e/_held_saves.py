"""The tab's project saves, held in the browser until the test lets them go.

A save the IDE sends (PUT /api/project) waits here, unanswered, so the tab
sits in "saving" for as long as the test needs: another session can save
first, or the tab can be asked to do something while its edit is on its way.
The order is the test's to set rather than the save delay's. Requests made
through `page.request` are not the page's and are never held.
"""

from __future__ import annotations

from playwright.sync_api import Page, Route

_PROJECT = "**/api/project"


class HeldSaves:
    def __init__(self, page: Page) -> None:
        self._page = page
        self.routes: list[Route] = []
        self._holding = True
        page.route(_PROJECT, self._hold)

    def _hold(self, route: Route) -> None:
        if self._holding and route.request.method == "PUT":
            self.routes.append(route)
        else:
            route.continue_()

    def wait_for(self, count: int = 1, timeout_ms: int = 10_000) -> None:
        """Until the tab has sent `count` saves."""
        waited = 0
        while len(self.routes) < count:
            assert waited < timeout_ms, f"the tab sent {len(self.routes)} saves, not {count}"
            self._page.wait_for_timeout(100)
            waited += 100

    def release(self) -> None:
        """Send on every save held, and hold no more."""
        self._holding = False
        for route in self.routes:
            route.continue_()
