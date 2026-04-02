import { useEffect, useCallback } from "react";
import { Sidebar } from "./components/layout/Sidebar";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import ToastContainer from "./components/shared/ToastContainer";
import { ProjectView } from "./views/ProjectView";
import { DeviceView } from "./views/DeviceView";
import { LogView } from "./views/LogView";
import { UIBuilderView } from "./views/UIBuilderView";
import { MacroView } from "./views/MacroView";
import { ScriptView } from "./views/ScriptView";
import { VariablesView } from "./views/VariablesView";
import { ISCView } from "./views/ISCView";
import { CloudSettingsView } from "./views/CloudSettingsView";
import { AIChatView } from "./views/AIChatView";
import { DashboardView } from "./views/DashboardView";
import { PluginsView } from "./views/PluginsView";
import { PluginExtensionView } from "./views/PluginExtensionView";
import { UpdatesView } from "./views/UpdatesView";
import { useProjectStore } from "./store/projectStore";
import { useNavigationStore } from "./store/navigationStore";
import { useWebSocket } from "./hooks/useWebSocket";
import { showInfo } from "./store/toastStore";

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
      case "updates":
        return <UpdatesView />;
      default:
        if (activeView.startsWith("plugin-view:")) {
          return <PluginExtensionView viewKey={activeView.slice("plugin-view:".length)} />;
        }
        return null;
    }
  };

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <Sidebar activeView={activeView} onViewChange={handleViewChange} />
      <main style={{ flex: 1, overflow: "hidden" }}>
        <ErrorBoundary>{renderView()}</ErrorBoundary>
      </main>
      <ToastContainer />
    </div>
  );
}

export default App;
