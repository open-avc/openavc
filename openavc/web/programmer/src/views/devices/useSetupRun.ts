import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../api/setupClient";
import { ApiError, parseApiError } from "../../api/errors";
import { getConnectionId, onDisconnect, onMessage } from "../../api/wsClient";
import { syncAfterSetupAction } from "../../store/projectStore";

interface Attempt extends api.PreparedSetup {
  params: Record<string, unknown>;
  connection: string | null;
  acknowledged: number;
}
const rank = { prepared: 0, claimed: 1, running: 2, terminal: 3 };
const sameParams = (a: Record<string, unknown>, b: Record<string, unknown>) =>
  JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());

/** One in-memory attempt. Recovery never prepares another physical operation. */
export function useSetupRun(deviceId: string, actionId: string, retained?: api.RetainedSetup) {
  const transfer = useRef(retained);
  const attempt = useRef<Attempt | null>(null);
  const active = useRef(true);
  const starting = useRef(false);
  const latest = useRef<api.SetupStatus | null>(null);
  const synced = useRef(false);
  const deniedResult = useRef(false);
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<api.SetupStatus | null>(null);
  const [steps, setSteps] = useState<NonNullable<api.SetupStatus["progress"]>[]>([]);
  const [secret, setSecret] = useState<api.SetupSecret | null>(null);
  const [error, setError] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [secretBusy, setSecretBusy] = useState(false);
  const valid = useCallback((owner: Attempt) => active.current && attempt.current === owner, []);

  const accept = useCallback((value: api.SetupStatus, owner: Attempt) => {
    if (!valid(owner) || deniedResult.current || value.run_id !== owner.run_id) return;
    const old = latest.current;
    // A delayed start/status response cannot undo an already observed terminal
    // result or a newer progress/credential generation.
    if (old && rank[value.status] < rank[old.status]) return;
    let next = value;
    if (old?.progress && (!value.progress || value.progress.sequence < old.progress.sequence)) {
      next = { ...next, progress: old.progress };
    }
    if (old && (value.secret.generation < old.secret.generation ||
        (value.secret.generation === old.secret.generation && value.secret.state === "available" &&
         ["reported_saved", "acknowledged", "unavailable"].includes(old.secret.state)))) {
      next = { ...next, secret: old.secret };
    }
    if (next.secret.generation > 0 && next.secret.generation <= owner.acknowledged) {
      next = { ...next, secret: { ...next.secret, state: "acknowledged" } };
    }
    if (next.progress && (!old?.progress || next.progress.sequence > old.progress.sequence)) {
      const progress = next.progress;
      setSteps((rows) => [...rows.slice(-99), progress]);
    }
    latest.current = next;
    setStatus(next);
    setUnavailable(false);
    setError("");
    setSecret((held) => held && next.secret.state === "available" &&
      held.generation === next.secret.generation ? held : null);
    if (next.status === "terminal") {
      owner.params = {};
      if (!synced.current) {
        synced.current = true;
        // The failed/unverified path can have saved settings too. This owner
        // preserves the project store's existing dirty-edit merge behavior.
        void syncAfterSetupAction(deviceId);
      }
    }
  }, [deviceId, valid]);

  const failed = useCallback((e: unknown, owner: Attempt) => {
    if (!valid(owner)) return;
    setSecret(null);
    const denied = e instanceof ApiError && [401, 403, 410].includes(e.status);
    if (denied) {
      deniedResult.current = true;
      setUnavailable(true);
    }
    setError(denied
      ? "This setup result is unavailable. Check the device before starting another attempt."
      : `${parseApiError(e)} Check this attempt's status before doing anything else.`);
  }, [valid]);

  const refresh = useCallback(async () => {
    const owner = attempt.current;
    if (!owner) return;
    try { accept(await api.setupStatus(owner.capability), owner); }
    catch (e) { failed(e, owner); }
  }, [accept, failed]);

  const secretFailed = useCallback(async (e: unknown, owner: Attempt) => {
    if (!valid(owner)) return;
    setSecret(null);
    // A credential generation can disappear while the reservation remains
    // usable (saving, acknowledgement or replacement). Reconcile this same
    // capability before treating a refused credential read as a lost run.
    if (e instanceof ApiError && e.status === 410) await refresh();
    else failed(e, owner);
  }, [failed, refresh, valid]);

  const subscribe = useCallback(async (owner: Attempt, connection: string) => {
    if (!valid(owner)) return;
    owner.connection = connection;
    try {
      const snapshot = await api.subscribeSetup(owner.capability, connection);
      if (valid(owner) && owner.connection === connection) accept(snapshot, owner);
      // Unmount can occur during subscribe. Retire that late installation too.
      else if (!valid(owner)) void api.unsubscribeSetup(owner.capability, connection).catch(() => {});
    } catch {
      // A lost socket route does not make the run unavailable. HTTP status
      // still reconciles it; connection.ready will install a replacement.
      if (valid(owner)) void refresh();
    }
  }, [accept, refresh, valid]);

  const dispatch = useCallback(async (owner: Attempt) => {
    try { accept(await api.startSetup(deviceId, actionId, owner.capability, owner.params), owner); }
    catch (e) {
      failed(e, owner);
      // The request may already have changed equipment. Read, never prepare.
      if (valid(owner)) {
        await refresh();
        if (latest.current?.status === "prepared" && e instanceof ApiError) failed(e, owner);
      }
    }
  }, [deviceId, actionId, accept, failed, refresh, valid]);

  const start = useCallback(async (params: Record<string, unknown>) => {
    if (starting.current || attempt.current) return;
    starting.current = true; // synchronous double-click guard before any await
    setPending(true);
    setError("");
    try {
      let prepared: api.PreparedSetup;
      const draft = transfer.current;
      if (draft && sameParams(draft.params, params)) prepared = draft;
      else {
        if (draft) {
          await api.discardSetupTransfer(draft.run_id);
          transfer.current = undefined;
          if (!active.current) return;
        }
        prepared = await api.prepareSetup(deviceId, actionId, params);
      }
      if (!active.current) return; // abandoned prepare has no physical effect
      const owner: Attempt = { ...prepared, params, connection: null, acknowledged: 0 };
      attempt.current = owner; // retain the capability BEFORE start is sent
      transfer.current = undefined;
      accept({ ...prepared, progress: null, result: null, secret: { state: "none", generation: 0 } }, owner);
      const connection = getConnectionId();
      if (connection) void subscribe(owner, connection);
      await dispatch(owner);
    } catch (e) {
      if (active.current) setError(parseApiError(e));
    } finally {
      starting.current = false;
      if (active.current) setPending(false);
    }
  }, [deviceId, actionId, accept, subscribe, dispatch]);

  const retryPrepared = useCallback(async () => {
    const owner = attempt.current;
    if (!owner || starting.current || latest.current?.status !== "prepared") return;
    starting.current = true;
    setPending(true);
    try { await dispatch(owner); }
    finally { starting.current = false; if (active.current) setPending(false); }
  }, [dispatch]);

  const reveal = useCallback(async () => {
    const owner = attempt.current;
    if (!owner || secretBusy) return;
    setSecretBusy(true);
    try {
      const value = await api.retrieveSetup(owner.capability);
      if (valid(owner) && !deniedResult.current && latest.current?.secret.state === "available" &&
          latest.current.secret.generation === value.generation && value.generation > owner.acknowledged) {
        setSecret(value);
      }
    } catch (e) { await secretFailed(e, owner); }
    finally { if (active.current) setSecretBusy(false); }
  }, [secretFailed, secretBusy, valid]);

  const acknowledge = useCallback(async () => {
    const owner = attempt.current;
    if (!owner || !secret || secretBusy) return;
    setSecretBusy(true);
    try {
      await api.acknowledgeSetup(owner.capability, secret.generation);
      if (valid(owner)) {
        owner.acknowledged = Math.max(owner.acknowledged, secret.generation);
        setSecret(null);
        if (latest.current) accept(latest.current, owner);
      }
    } catch (e) { await secretFailed(e, owner); }
    finally { if (active.current) setSecretBusy(false); }
  }, [accept, secretFailed, secret, secretBusy, valid]);

  const dispose = useCallback(() => {
    active.current = false;
    const owner = attempt.current;
    attempt.current = null;
    latest.current = null;
    setSecret(null);
    if (owner) {
      owner.params = {};
      if (owner.connection) void api.unsubscribeSetup(owner.capability, owner.connection).catch(() => {});
    }
  }, []);

  const discardRetained = useCallback(async () => {
    const draft = transfer.current;
    if (!draft) return true;
    try {
      await api.discardSetupTransfer(draft.run_id);
      transfer.current = undefined;
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 410) {
        // Explicit close may forget an expired/already-consumed draft. This
        // never permits the edit/start path to prepare another operation.
        transfer.current = undefined;
        return true;
      }
      if (active.current) setError(parseApiError(e));
      return false;
    }
  }, []);

  useEffect(() => {
    active.current = true;
    const unmessage = onMessage((msg) => {
      const owner = attempt.current;
      if (!owner) return;
      if (msg.type === "connection.ready" && typeof msg.connection_id === "string") {
        void subscribe(owner, msg.connection_id);
      } else if (msg.type === "action.progress" && msg.run_id === owner.run_id &&
          msg.device_id === deviceId && msg.action_id === actionId) {
        accept(msg as unknown as api.SetupStatus, owner);
      }
    });
    const undisconnect = onDisconnect(() => {
      setSecret(null);
      if (attempt.current) attempt.current.connection = null;
    });
    return () => { unmessage(); undisconnect(); dispose(); };
  }, [deviceId, actionId, accept, subscribe, dispose]);

  const needsRetention = !!transfer.current || (!!status && (status.status !== "terminal" || status.secret.state === "available"));
  useEffect(() => {
    if (!needsRetention || unavailable) return;
    // Also covers lost final socket frames. Only status is retried, never work.
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    return () => window.clearInterval(timer);
  }, [needsRetention, unavailable, refresh]);
  useEffect(() => {
    if (!needsRetention && !pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [needsRetention, pending]);

  return { status, steps, secret, error, unavailable, pending, secretBusy,
    needsRetention, start, retryPrepared, refresh, reveal, acknowledge, dispose, discardRetained };
}
