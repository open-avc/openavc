import { useEffect } from "react";
import * as ws from "../api/wsClient";
import { useConnectionStore } from "../store/connectionStore";
import { useLogStore } from "../store/logStore";
import type { LogEntry, StepPathSegment } from "../store/logStore";
import { useProjectStore } from "../store/projectStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { useDiscoveryStore } from "../store/discoveryStore";
import { usePluginStore } from "../store/pluginStore";
import { invalidatePluginMacroActions } from "../components/macros/pluginMacroActions";
import { showSuccess, showInfo, showError } from "../store/toastStore";
import * as api from "../api/restClient";

// How long the "just fired" highlight stays lit on a trigger card after a
// trigger.fired message.
const TRIGGER_FIRED_FLASH_MS = 1500;

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
      // Load plugins + their UI extensions on every (re)connect. plugin.* events
      // only fire on changes, not on a fresh connect, so without this the UI
      // Builder's plugin-element config form has no schema after a reload and
      // falls back to the raw-JSON editor.
      usePluginStore.getState().load();
      // Always load server state first on reconnect to avoid overwriting
      // external changes with stale local data
      const store = useProjectStore.getState();
      const wasDirty = store.dirty;
      store.load().then(() => {
        // Project replaced from server — UI Builder undo snapshots reference
        // stale pages/settings/master_elements that no longer match.
        useUIBuilderStore.getState().clearUndoHistory();
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
        useProjectStore.getState().load().then(() => {
          useUIBuilderStore.getState().clearUndoHistory();
        });
      }, 300);
    };

    // Plugin events arrive in bursts during engine reload (2N events for N
    // plugins); debounce so we issue at most one listPlugins+extensions fetch
    // per burst.
    let pluginRefreshTimer: ReturnType<typeof setTimeout> | null = null;
    const debouncedPluginRefresh = () => {
      if (pluginRefreshTimer) clearTimeout(pluginRefreshTimer);
      pluginRefreshTimer = setTimeout(() => {
        usePluginStore.getState().load();
      }, 300);
    };

    // Track transient timers so an unmount / StrictMode double-mount / WS-auth
    // bounce can't orphan them. An orphaned timer mutates global store state
    // after this hook is gone — worst case a stale macro-reset or trigger-clear
    // timer firing after re-login and stomping fresh state.
    const macroResetTimers = new Set<ReturnType<typeof setTimeout>>();
    // trigger.pending auto-clear timers, keyed by trigger_id: the server
    // re-emits trigger.pending for the same id on every debounce reset, so we
    // cancel the prior timer before arming a new one (otherwise an old timer
    // clears a freshly re-armed pending entry mid-wait).
    const triggerClearTimers = new Map<string, ReturnType<typeof setTimeout>>();
    const cancelTriggerClear = (triggerId: string) => {
      const existing = triggerClearTimers.get(triggerId);
      if (existing) {
        clearTimeout(existing);
        triggerClearTimers.delete(triggerId);
      }
    };
    // trigger.fired "just fired" highlight auto-clear timers, keyed by trigger_id.
    // A rapid re-fire re-arms the timer so the flash restarts cleanly.
    const firedClearTimers = new Map<string, ReturnType<typeof setTimeout>>();

    const unsub = ws.onMessage((msg) => {
      // Full state snapshot (sent by server on WS connect)
      if (msg.type === "state.snapshot" && msg.state) {
        setFullState(msg.state as Record<string, unknown>);
      }

      // Incremental state updates
      if (msg.type === "state.update" && msg.changes) {
        applyStateUpdate(msg.changes as Record<string, unknown>);
      }

      // Explicit key removals (sent when state.delete() is called server-side)
      if (msg.type === "state.delete" && Array.isArray(msg.keys)) {
        useConnectionStore.getState().applyStateDelete(msg.keys as string[]);
      }

      // Project was modified (by AI, fleet push, or other source) — refetch
      if (msg.type === "project.reloaded") {
        const store = useProjectStore.getState();
        // If we're mid-save, this is our own save echoing back via the
        // server's reload broadcast.  Ignore it — the PUT response will
        // update our revision, and real conflicts are caught by the 409.
        if (store.saving) return;
        const serverRevision = (msg as any).revision;
        if (store.dirty) {
          if (serverRevision != null && store.revision != null && serverRevision !== store.revision) {
            showInfo("Project modified by another session — save may trigger a conflict. Consider reloading.");
          } else {
            showInfo("Project modified externally — your unsaved changes may conflict");
          }
        } else {
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
          device: (msg.device as string) ?? "",
        });
      }

      if (msg.type === "log.history" && Array.isArray(msg.entries)) {
        const entries = msg.entries as Array<Omit<LogEntry, "id" | "device"> & { device?: string }>;
        useLogStore.getState().addLogBatch(
          entries.map((e) => ({ ...e, device: e.device ?? "" })),
        );
      }

      // Command / state ack — show toast on failure
      if (msg.type === "command.ack" && msg.success === false) {
        showError(msg.error as string || "Command failed");
      }

      if (msg.type === "state.set.ack" && msg.success === false) {
        showError(msg.error as string || "Failed to set state");
      }

      // Macro progress events
      if (msg.type === "macro.started") {
        useLogStore.getState().startMacroRun(msg.macro_id as string);
        useLogStore.getState().setMacroProgress({
          totalSteps: msg.total_steps as number,
        });
      }

      if (msg.type === "macro.progress") {
        const store = useLogStore.getState();
        // Regular step progress
        if (msg.status === "running") {
          const stepIndex = msg.step_index as number;
          const totalSteps = msg.total_steps as number;
          // The server sends the step's explicit tree location as step_path
          // (e.g. [2, "then", 0]) — consume it verbatim rather than inferring
          // branch depth from total_steps deltas (which is ambiguous when a
          // branch's length equals its parent's). Fall back to the flat index.
          const stepPath = Array.isArray(msg.step_path)
            ? (msg.step_path as StepPathSegment[])
            : [stepIndex];

          store.setMacroProgress({
            macroId: msg.macro_id as string,
            stepIndex,
            totalSteps,
            status: "running",
            activeStepPath: stepPath,
          });
        }
        // Conditional evaluation result
        if (msg.status === "evaluated" && msg.action === "conditional") {
          store.addConditionalResult({
            stepIndex: store.macroProgress.stepIndex ?? -1,
            conditionResult: msg.condition_result as boolean,
            branch: msg.branch as "then" | "else",
            conditionKey: msg.condition_key as string,
            conditionOperator: msg.condition_operator as string,
            actualValue: msg.actual_value,
          });
        }
        // Group command per-device results
        if (msg.status === "group_complete" && msg.action === "group.command") {
          store.addGroupResult({
            stepIndex: store.macroProgress.stepIndex ?? -1,
            group: msg.group as string,
            command: msg.command as string,
            deviceResults: msg.device_results as any[],
          });
        }
      }

      // Macro step error (emitted even when stop_on_error is false)
      if (msg.type === "macro.step_error") {
        useLogStore.getState().addStepError({
          stepIndex: msg.step_index as number,
          action: msg.action as string,
          device: (msg.device as string) ?? "",
          group: (msg.group as string) ?? "",
          command: (msg.command as string) ?? "",
          error: msg.error as string,
          description: (msg.description as string) ?? "",
        });
      }

      if (msg.type === "macro.completed") {
        const completedId = msg.macro_id as string;
        useLogStore.getState().finishMacroRun("completed");
        useLogStore.getState().setMacroProgress({
          macroId: completedId,
          status: "completed",
        });
        // Auto-reset after a brief moment, but only if still showing this macro
        const resetTimer = setTimeout(() => {
          macroResetTimers.delete(resetTimer);
          if (useLogStore.getState().macroProgress.macroId === completedId) {
            useLogStore.getState().resetMacroProgress();
          }
        }, 2000);
        macroResetTimers.add(resetTimer);
      }

      if (msg.type === "macro.error") {
        const errorId = msg.macro_id as string;
        useLogStore.getState().finishMacroRun("error", msg.error as string);
        useLogStore.getState().setMacroProgress({
          macroId: errorId,
          status: "error",
        });
        const resetTimer = setTimeout(() => {
          macroResetTimers.delete(resetTimer);
          if (useLogStore.getState().macroProgress.macroId === errorId) {
            useLogStore.getState().resetMacroProgress();
          }
        }, 3000);
        macroResetTimers.add(resetTimer);
      }

      // Trigger pending / queued events
      if (msg.type === "trigger.pending") {
        const triggerId = msg.trigger_id as string;
        const armedAt = Date.now();
        useLogStore.getState().setTriggerPending(triggerId, {
          reason: msg.reason as "debounce" | "delay",
          waitSeconds: msg.wait_seconds as number,
          timestamp: armedAt,
        });
        // Cancel any prior auto-clear for this trigger before arming a new one,
        // so a debounce re-arm can't have its old timer clear the fresh entry.
        cancelTriggerClear(triggerId);
        const clearMs = ((msg.wait_seconds as number) ?? 5) * 1000 + 500;
        const clearTimer = setTimeout(() => {
          triggerClearTimers.delete(triggerId);
          // Only clear if the pending entry is still the one this timer armed
          // (a newer pending/queued would carry a different timestamp).
          if (useLogStore.getState().triggerPending[triggerId]?.timestamp === armedAt) {
            useLogStore.getState().setTriggerPending(triggerId, null);
          }
        }, clearMs);
        triggerClearTimers.set(triggerId, clearTimer);
      }

      if (msg.type === "trigger.queued") {
        const triggerId = msg.trigger_id as string;
        // A queued state supersedes any pending countdown — drop its clear timer.
        cancelTriggerClear(triggerId);
        useLogStore.getState().setTriggerPending(triggerId, {
          reason: "queued",
          queuePosition: msg.queue_position as number,
          timestamp: Date.now(),
        });
      }

      // Clear trigger pending when it fires, and flash the "just fired" highlight.
      if (msg.type === "trigger.fired") {
        const triggerId = msg.trigger_id as string;
        cancelTriggerClear(triggerId);
        useLogStore.getState().setTriggerPending(triggerId, null);
        useLogStore.getState().setTriggerFired(triggerId, true);
        const priorFlash = firedClearTimers.get(triggerId);
        if (priorFlash) clearTimeout(priorFlash);
        const flashTimer = setTimeout(() => {
          firedClearTimers.delete(triggerId);
          useLogStore.getState().setTriggerFired(triggerId, false);
        }, TRIGGER_FIRED_FLASH_MS);
        firedClearTimers.set(triggerId, flashTimer);
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
        if (Array.isArray(msg.warnings)) {
          useDiscoveryStore.getState().setWarnings(msg.warnings as string[]);
        }
      }

      if (msg.type === "discovery_complete") {
        useDiscoveryStore.getState().setStatus("complete");
        if (Array.isArray(msg.warnings)) {
          useDiscoveryStore.getState().setWarnings(msg.warnings as string[]);
        }
      }

      // Plugin events — refresh plugin list AND macro builder's plugin
      // actions cache so newly-enabled plugins show up in Add Step without
      // a page reload.
      if (
        msg.type === "plugin.started" ||
        msg.type === "plugin.stopped" ||
        msg.type === "plugin.error" ||
        msg.type === "plugin.missing"
      ) {
        debouncedPluginRefresh();
        invalidatePluginMacroActions().catch(console.error);
      }
    });

    return () => {
      if (reloadTimer) clearTimeout(reloadTimer);
      if (pluginRefreshTimer) clearTimeout(pluginRefreshTimer);
      macroResetTimers.forEach(clearTimeout);
      macroResetTimers.clear();
      triggerClearTimers.forEach(clearTimeout);
      triggerClearTimers.clear();
      firedClearTimers.forEach(clearTimeout);
      firedClearTimers.clear();
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
