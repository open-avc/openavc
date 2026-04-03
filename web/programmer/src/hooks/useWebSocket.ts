import { useEffect } from "react";
import * as ws from "../api/wsClient";
import { useConnectionStore } from "../store/connectionStore";
import { useLogStore } from "../store/logStore";
import type { LogEntry } from "../store/logStore";
import { useProjectStore } from "../store/projectStore";
import { useDiscoveryStore } from "../store/discoveryStore";
import { usePluginStore } from "../store/pluginStore";
import { showSuccess, showInfo } from "../store/toastStore";
import * as api from "../api/restClient";

export function useWebSocket() {
  const setConnected = useConnectionStore((s) => s.setConnected);
  const applyStateUpdate = useConnectionStore((s) => s.applyStateUpdate);
  const setFullState = useConnectionStore((s) => s.setFullState);

  useEffect(() => {
    // Helper: sync state + subscribe logs (used on initial connect AND reconnects)
    const syncOnConnect = () => {
      setConnected(true);
      // The server sends state.snapshot on WS connect, so we rely on
      // that instead of a separate REST fetch. This eliminates the race
      // where state changes between REST response and WS subscription.
      // Re-subscribe to log stream
      ws.send({ type: "log.subscribe" });
      useLogStore.getState().setLogSubscribed(true);
      // Always load server state first on reconnect to avoid overwriting
      // external changes with stale local data
      const store = useProjectStore.getState();
      const wasDirty = store.dirty;
      store.load().then(() => {
        if (wasDirty) {
          showSuccess("Project reloaded from server — local changes may need to be re-applied");
        }
      }).catch(console.error);
    };

    // Connect WebSocket — server will send state.snapshot + ui.definition on connect
    ws.connect();

    // Subscribe to connect/disconnect lifecycle events
    const unsubConnect = ws.onConnect(() => {
      syncOnConnect();
    });

    const unsubDisconnect = ws.onDisconnect(() => {
      setConnected(false);
      useLogStore.getState().setLogSubscribed(false);
    });

    // Debounce project reloads to avoid rapid-fire refetches
    let reloadTimer: ReturnType<typeof setTimeout> | null = null;
    const debouncedProjectReload = () => {
      if (reloadTimer) clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => {
        useProjectStore.getState().load();
      }, 300);
    };

    const unsub = ws.onMessage((msg) => {
      // Full state snapshot (sent by server on WS connect)
      if (msg.type === "state.snapshot" && msg.state) {
        setFullState(msg.state as Record<string, unknown>);
      }

      // Incremental state updates
      if (msg.type === "state.update" && msg.changes) {
        applyStateUpdate(msg.changes as Record<string, unknown>);
      }

      // Project was modified (by AI, fleet push, or other source) — refetch
      if (msg.type === "project.reloaded") {
        const store = useProjectStore.getState();
        if (store.dirty) {
          // Warn user that an external change was received but we have local edits
          showInfo("Project modified externally — your unsaved changes may conflict");
        } else {
          // Silently reload — no toast needed whether this is our own echo or
          // another client's change, the data refreshes automatically
          debouncedProjectReload();
        }
      }

      // Log streaming
      if (msg.type === "log.entry") {
        useLogStore.getState().addLogEntry({
          timestamp: msg.timestamp as number,
          level: msg.level as string,
          source: msg.source as string,
          category: msg.category as string,
          message: msg.message as string,
        });
      }

      if (msg.type === "log.history" && Array.isArray(msg.entries)) {
        useLogStore.getState().addLogBatch(msg.entries as LogEntry[]);
      }

      // Macro progress events
      if (msg.type === "macro.started") {
        useLogStore.getState().setMacroProgress({
          macroId: msg.macro_id as string,
          totalSteps: msg.total_steps as number,
          stepIndex: null,
          status: "running",
        });
      }

      if (msg.type === "macro.progress") {
        useLogStore.getState().setMacroProgress({
          macroId: msg.macro_id as string,
          stepIndex: msg.step_index as number,
          totalSteps: msg.total_steps as number,
          status: "running",
        });
      }

      if (msg.type === "macro.completed") {
        const completedId = msg.macro_id as string;
        useLogStore.getState().setMacroProgress({
          macroId: completedId,
          status: "completed",
        });
        // Auto-reset after a brief moment, but only if still showing this macro
        setTimeout(() => {
          if (useLogStore.getState().macroProgress.macroId === completedId) {
            useLogStore.getState().resetMacroProgress();
          }
        }, 2000);
      }

      if (msg.type === "macro.error") {
        const errorId = msg.macro_id as string;
        useLogStore.getState().setMacroProgress({
          macroId: errorId,
          status: "error",
        });
        setTimeout(() => {
          if (useLogStore.getState().macroProgress.macroId === errorId) {
            useLogStore.getState().resetMacroProgress();
          }
        }, 3000);
      }

      // Discovery events
      if (msg.type === "discovery_update" && msg.device) {
        useDiscoveryStore.getState().upsertDevice(msg.device as api.DiscoveredDevice);
        if (typeof msg.progress === "number") {
          useDiscoveryStore.getState().setPhase(
            (msg.phase as string) ?? "",
            msg.progress as number,
            "",
          );
        }
      }

      if (msg.type === "discovery_phase") {
        useDiscoveryStore.getState().setPhase(
          (msg.phase as string) ?? "",
          (msg.progress as number) ?? 0,
          (msg.message as string) ?? "",
        );
      }

      if (msg.type === "discovery_complete") {
        useDiscoveryStore.getState().setStatus("complete");
      }

      // Plugin events — refresh plugin list on status changes
      if (
        msg.type === "plugin.started" ||
        msg.type === "plugin.stopped" ||
        msg.type === "plugin.error" ||
        msg.type === "plugin.missing"
      ) {
        usePluginStore.getState().load();
      }
    });

    return () => {
      if (reloadTimer) clearTimeout(reloadTimer);
      unsub();
      unsubConnect();
      unsubDisconnect();
      ws.send({ type: "log.unsubscribe" });
      ws.disconnect();
      setConnected(false);
      useLogStore.getState().setLogSubscribed(false);
    };
  }, [setConnected, applyStateUpdate, setFullState]);
}
