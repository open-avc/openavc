// An update another screen asks the Updates page for: the assistant, on a
// system behind the release it needs, offers that release as one button. The
// button opens the Updates page with the release named, and the page runs its
// own check and its own Install, so the progress dialog and its completion are
// the ones every update gets.

import { useEffect, useRef, useState } from "react";
import * as api from "../api/restClient";
import type { UpdateCheckResult, UpdateStatus } from "../api/restClient";
import { parseApiError } from "../api/errors";
import { compareVersions } from "../api/versionRule";
import { useNavigationStore } from "../store/navigationStore";
import { showError } from "../store/toastStore";

/** The navigation focus type that names the release asked for. */
const INSTALL_REQUEST = "install";

/** Open the Updates page and ask it to install `release`. */
export function requestInstall(release: string): void {
  useNavigationStore.getState().navigateTo("updates", { type: INSTALL_REQUEST, id: release });
}

/** What the page does for a request for `needed`, after its check: install
 *  the release an Install installs when that is `needed` or newer, on a system
 *  that updates itself with no update under way; say the release was
 *  `not_found` when such a system's check found nothing that new; else `show`
 *  the page as the check left it (how to update this deployment, a failed
 *  check, the firmware's guidance, the update already under way). */
export function requestedInstall(
  needed: string,
  check: UpdateCheckResult,
  status: UpdateStatus,
): { install: string } | "not_found" | "show" {
  if (check.error) return "show";
  if (status.update_owner === "firmware" || !status.can_self_update) return "show";
  if (status.update_status !== "idle" && status.update_status !== "error") return "show";
  const target = status.install_version ?? "";
  const meets = compareVersions(target, needed);
  return meets === 0 || meets === 1 ? { install: target } : "not_found";
}

/** What the page lends a request: its Checking state, what it shows of a
 *  check, and its Install. */
export interface RequestedInstallPage {
  setChecking: (checking: boolean) => void;
  onChecked: (check: UpdateCheckResult, status: UpdateStatus) => void;
  install: (version: string) => void;
}

/** Carry out a request another screen made when it opened this page: check
 *  for updates, then do what `requestedInstall` says. Read once, at mount; a
 *  later visit is an ordinary one. */
export function useRequestedInstall(page: RequestedInstallPage): void {
  const [needed] = useState(() => {
    const focus = useNavigationStore.getState().consumeFocus();
    return focus?.type === INSTALL_REQUEST ? focus.id : "";
  });
  // The page's handlers as they are when the check answers, not at mount.
  const handlers = useRef(page);
  useEffect(() => {
    handlers.current = page;
  });

  useEffect(() => {
    if (!needed) return;
    let left = false;
    void carryOut(needed, () => (left ? null : handlers.current));
    return () => {
      left = true;
    };
  }, [needed]);
}

/** Check for updates, then do what `requestedInstall` says, through the
 *  page's handlers; `page()` is null once the page is left. */
async function carryOut(needed: string, page: () => RequestedInstallPage | null): Promise<void> {
  page()?.setChecking(true);
  try {
    const check = await api.checkForUpdates();
    const status = await api.getUpdateStatus();
    const handlers = page();
    if (!handlers) return;
    handlers.onChecked(check, status);
    if (check.error) {
      showError("Could not check for updates: " + check.error);
      return;
    }
    const next = requestedInstall(needed, check, status);
    if (next === "not_found") {
      showError("This system's update check did not find v" + needed.replace(/^v/i, "")
        + ". Check for Updates again later.");
    } else if (next !== "show") {
      handlers.install(next.install);
    }
  } catch (e) {
    if (page()) showError("Update check failed: " + parseApiError(e));
  } finally {
    page()?.setChecking(false);
  }
}
