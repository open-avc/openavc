import { useEffect, useCallback, lazy, Suspense } from "react";
import { Sidebar } from "./components/layout/Sidebar";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import ToastContainer from "./components/shared/ToastContainer";
import { DashboardView } from "./views/DashboardView";
import { useProjectStore } from "./store/projectStore";
import { useNavigationStore } from "./store/navigationStore";
import { useWebSocket } from "./hooks/useWebSocket";
import { showInfo } from "./store/toastStore";

// Lazy-load views that aren't shown on initial page load
const ProjectView = lazy(() => import("./views/ProjectView").then((m) => ({ default: m.ProjectView })));
const DeviceView = lazy(() => import("./views/DeviceView").then((m) => ({ default: m.DeviceView })));
const LogView = lazy(() => import("./views/LogView").then((m) => ({ default: m.LogView })));
const UIBuilderView = lazy(() => import("./views/UIBuilderView").then((m) => ({ default: m.UIBuilderView })));
const MacroView = lazy(() => import("./views/MacroView").then((m) => ({ default: m.MacroView })));
const ScriptView = lazy(() => import("./views/ScriptView").then((m) => ({ default: m.ScriptView })));
const VariablesView = lazy(() => import("./views/VariablesView").then((m) => ({ default: m.VariablesView })));
const ISCView = lazy(() => import("./views/ISCView").then((m) => ({ default: m.ISCView })));
const CloudSettingsView = lazy(() => import("./views/CloudSettingsView").then((m) => ({ default: m.CloudSettingsView })));
const AIChatView = lazy(() => import("./views/AIChatView").then((m) => ({ default: m.AIChatView })));
const PluginsView = lazy(() => import("./views/PluginsView").then((m) => ({ default: m.PluginsView })));
const PluginExtensionView = lazy(() => import("./views/PluginExtensionView").then((m) => ({ default: m.PluginExtensionView })));
const UpdatesView = lazy(() => import("./views/UpdatesView").then((m) => ({ default: m.UpdatesView })));
const SystemSettingsView = lazy(() => import("./views/SystemSettingsView").then((m) => ({ default: m.SystemSettingsView })));

function App() {
  const activeView = useNavigationStore((s) => s.activeView);
  const navigateTo = useNavigationStore((s) => s.navigateTo);
  const loadProject = useProjectStore((s) => s.load);

  // Connect WebSocket and load project on mount
  useWebSocket();
  useEffect(() => {
    loadProject();
  }, [loadProject]);

  // Warn before closing tab with unsaved changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (useProjectStore.getState().dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  // Global undo/redo keyboard shortcuts (skip when in UI Builder, which has its own)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      if (e.key !== "z" && e.key !== "Z") return;
      // Don't intercept in UI Builder
      if (useNavigationStore.getState().activeView === "ui-builder") return;
      // Don't intercept in text inputs
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      e.preventDefault();
      const store = useProjectStore.getState();
      if (e.shiftKey) {
        store.redo();
      } else {
        store.undo();
      }
      // Show toast with description
      const desc = useProjectStore.getState().lastUndoDescription;
      if (desc) showInfo(desc);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleViewChange = useCallback(
    (view: typeof activeView) => navigateTo(view),
    [navigateTo],
  );

  const renderView = () => {
    switch (activeView) {
      case "dashboard":
        return <DashboardView />;
      case "project":
        return <ProjectView />;
      case "devices":
        return <DeviceView />;
      case "variables":
        return <VariablesView />;
      case "log":
        return <LogView />;
      case "ui-builder":
        return <UIBuilderView />;
      case "macros":
        return <MacroView />;
      case "scripts":
        return <ScriptView />;
      case "plugins":
        return <PluginsView />;
      case "isc":
        return <ISCView />;
      case "ai":
        return <AIChatView />;
      case "cloud":
        return <CloudSettingsView />;
      case "settings":
        return <SystemSettingsView />;
      case "updates":
        return <UpdatesView />;
      default:
        if (activeView.startsWith("plugin-view:")) {
          return <PluginExtensionView viewKey={activeView.slice("plugin-view:".length)} />;
        }
        return null;
    }
  };

  const conflictDetected = useProjectStore((s) => s.conflictDetected);
  const forceReload = useProjectStore((s) => s.forceReload);
  const dismissConflict = useProjectStore((s) => s.dismissConflict);

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <Sidebar activeView={activeView} onViewChange={handleViewChange} />
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {/* Conflict banner (14.4) */}
        {conflictDetected && (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "var(--space-sm) var(--space-md)",
            background: "rgba(244,67,54,0.12)", borderBottom: "1px solid rgba(244,67,54,0.3)",
            fontSize: 13, color: "#ef4444", flexShrink: 0,
          }}>
            <span>
              <strong>Conflict:</strong> The project was modified by another session. Your changes could not be saved.
            </span>
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <button
                onClick={dismissConflict}
                style={{ padding: "2px 10px", borderRadius: 4, border: "1px solid rgba(244,67,54,0.3)", background: "transparent", color: "#ef4444", fontSize: 12, cursor: "pointer" }}
              >
                Dismiss
              </button>
              <button
                onClick={forceReload}
                style={{ padding: "2px 10px", borderRadius: 4, border: "none", background: "#ef4444", color: "#fff", fontSize: 12, cursor: "pointer" }}
              >
                Reload Project
              </button>
            </div>
          </div>
        )}
        <main style={{ flex: 1, overflow: "hidden" }}>
          <ErrorBoundary>
            <Suspense fallback={null}>{renderView()}</Suspense>
          </ErrorBoundary>
        </main>
      </div>
      <ToastContainer />
    </div>
  );
}

export default App;
