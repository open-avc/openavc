/**
 * Device audit store: whether the wizard is open, which step it shows, and
 * the running session as the server last described it.
 *
 * The session is replaced wholesale on every `audit.state` and patched per
 * activity on `audit.progress` (see applyAuditMessage), so a component that
 * selects `session` re-renders only when it changed. Select primitives or the
 * stored objects themselves; never build an object inside a selector.
 */
import { create } from "zustand";
import type { AuditSessionState, AuditTimelineEntry, AuditTrafficEntry } from "../api/auditClient";
import {
  appendTraffic,
  applyAuditMessage,
  isStaleReply,
  messageSeq,
  type AuditStep,
} from "../views/devices/audit/auditHelpers";

interface AuditStoreState {
  open: boolean;
  /** The address an entry point filled in (a Discovery result, say). */
  presetAddress: string;
  /** The project device whose page opened the wizard ("" otherwise). */
  presetDevice: string;
  step: AuditStep;
  session: AuditSessionState | null;
  timeline: AuditTimelineEntry[];
  /** The driver's traffic as it arrives (the newest few hundred entries). */
  traffic: AuditTrafficEntry[];
  /** The newest message number applied for this session: a reply older than
   *  it is not applied (see isStaleReply). */
  seq: number;

  openWizard: (opts?: { address?: string; deviceId?: string }) => void;
  closeWizard: () => void;
  setStep: (step: AuditStep) => void;
  setSession: (session: AuditSessionState | null) => void;
  applyMessage: (msg: Record<string, unknown>) => void;
}

export const useAuditStore = create<AuditStoreState>((set) => ({
  open: false,
  presetAddress: "",
  presetDevice: "",
  step: "target",
  session: null,
  timeline: [],
  traffic: [],
  seq: 0,

  openWizard: (opts) =>
    set({
      open: true,
      presetAddress: opts?.address ?? "",
      presetDevice: opts?.deviceId ?? "",
      step: "target",
      timeline: [],
      traffic: [],
      seq: 0,
    }),
  closeWizard: () =>
    set({ open: false, session: null, timeline: [], traffic: [], step: "target", seq: 0 }),
  setStep: (step) => set({ step }),
  setSession: (session) =>
    set((state) => {
      if (isStaleReply(state.session, state.seq, session)) return state;
      const same = !!session && session.session_id === state.session?.session_id;
      return { session, seq: Math.max(same ? state.seq : 0, session?.seq ?? 0) };
    }),
  applyMessage: (msg) =>
    set((state) => {
      const mine = !!state.session && msg.session_id === state.session.session_id;
      const seq = mine ? Math.max(state.seq, messageSeq(msg) ?? 0) : state.seq;
      if (msg.type === "audit.traffic") {
        const traffic = appendTraffic(state.traffic, msg, state.session?.session_id ?? null);
        if (traffic === state.traffic) return seq === state.seq ? state : { seq };
        return { traffic, seq };
      }
      const next = applyAuditMessage(state.session, state.timeline, msg);
      if (next.session === state.session && next.timeline === state.timeline) {
        return seq === state.seq ? state : { seq };
      }
      return { session: next.session, timeline: next.timeline, seq };
    }),
}));
