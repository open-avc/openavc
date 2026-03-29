/**
 * Navigation store — centralized view routing with optional focus targets.
 *
 * Allows any component to trigger cross-view navigation:
 *   navigateTo("macros", { type: "macro", id: "system_on" })
 *   navigateTo("scripts", { type: "script", id: "status_updater", detail: "line:12" })
 *   navigateTo("ui-builder", { type: "element", id: "btn_power", detail: "page:main_panel" })
 */
import { create } from "zustand";
import type { ViewId } from "../components/layout/Sidebar";

export interface FocusTarget {
  type: string; // "macro", "script", "element"
  id: string; // item ID within the view
  detail?: string; // extra context: "line:12", "page:main_panel", "trigger:t1"
}

interface NavigationState {
  activeView: ViewId;
  pendingFocus: FocusTarget | null;

  /** Switch to a view, optionally setting a focus target for the destination. */
  navigateTo: (view: ViewId, focus?: FocusTarget) => void;

  /** Called by destination view to claim and clear the pending focus (one-shot). */
  consumeFocus: () => FocusTarget | null;
}

// Read initial view from URL hash (e.g. #devices → "devices")
function viewFromHash(): ViewId {
  let hash = window.location.hash.slice(1); // remove #
  if (!hash) return "dashboard";
  // Discovery and Drivers are now sub-tabs within Devices view
  if (hash === "discovery" || hash === "drivers") hash = "devices";
  // plugin views use "plugin-view:..." prefix
  if (hash.startsWith("plugin-view:")) return hash as ViewId;
  // Validate against known views
  const known: ViewId[] = [
    "dashboard", "project", "devices", "variables",
    "ui-builder", "macros", "scripts", "plugins",
    "isc", "ai", "cloud", "log",
  ];
  return known.includes(hash as ViewId) ? (hash as ViewId) : "dashboard";
}

export const useNavigationStore = create<NavigationState>((set, get) => ({
  activeView: viewFromHash(),
  pendingFocus: null,

  navigateTo: (view, focus) => {
    set({ activeView: view, pendingFocus: focus ?? null });
    // Sync hash without triggering hashchange handler
    const newHash = view === "dashboard" ? "" : view;
    if (window.location.hash.slice(1) !== newHash) {
      window.history.pushState(null, "", newHash ? `#${newHash}` : window.location.pathname);
    }
  },

  consumeFocus: () => {
    const focus = get().pendingFocus;
    if (focus) set({ pendingFocus: null });
    return focus;
  },
}));

// Listen for browser back/forward (popstate covers both hash changes and history nav)
window.addEventListener("popstate", () => {
  const view = viewFromHash();
  const current = useNavigationStore.getState().activeView;
  if (view !== current) {
    useNavigationStore.setState({ activeView: view, pendingFocus: null });
  }
});
