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

/** What the page installs for a request for `needed`, after its check: the
 *  release an Install installs when that is `needed` or newer, on a system
 *  that updates itself with no update under way; else null, and the page is
 *  left showing what the check found (how to update this deployment, a
 *  failed check, the firmware's guidance). */
export function requestedInstallTarget(
  needed: string,
  check: UpdateCheckResult,
  status: UpdateStatus,
): string | null {
  if (check.error) return null;
  if (status.update_owner === "firmware" || !status.can_self_update) return null;
  if (status.update_status !== "idle" && status.update_status !== "error") return null;
  const target = status.install_version ?? "";
  const meets = compareVersions(target, needed);
  return meets === 0 || meets === 1 ? target : null;
}

/** Carry out a request another screen made when it opened this page: check
 *  for updates (`onChecked` gets what the page shows), then `install` the
 *  release `requestedInstallTarget` names, if any. Read once, at mount; a
 *  later visit is an ordinary one. */
export function useRequestedInstall(
  onChecked: (check: UpdateCheckResult, status: UpdateStatus) => void,
  install: (version: string) => void,
): void {
  const [needed] = useState(() => {
    const focus = useNavigationStore.getState().consumeFocus();
    return focus?.type === INSTALL_REQUEST ? focus.id : "";
  });
  // The page's handlers as they are when the check answers, not at mount.
  const handlers = useRef({ onChecked, install });
  useEffect(() => {
    handlers.current = { onChecked, install };
  });

  useEffect(() => {
    if (!needed) return;
    let left = false;
    (async () => {
      try {
        const check = await api.checkForUpdates();
        const status = await api.getUpdateStatus();
        if (left) return;
        handlers.current.onChecked(check, status);
        if (check.error) {
          showError("Could not check for updates: " + check.error);
          return;
        }
        const target = requestedInstallTarget(needed, check, status);
        if (target) handlers.current.install(target);
      } catch (e) {
        if (!left) showError("Update check failed: " + parseApiError(e));
      }
    })();
    return () => {
      left = true;
    };
  }, [needed]);
}
