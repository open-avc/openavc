import { beforeEach, describe, expect, it } from "vitest";
import type { AuditListen, AuditSessionState } from "../api/auditClient";
import { useAuditStore } from "./auditStore";

function listen(status: AuditListen["status"]): AuditListen {
  return { status } as AuditListen;
}

function session(seq: number, status: AuditListen["status"]): AuditSessionState {
  return {
    session_id: "abc123",
    status: "active",
    target: { address: "10.0.0.50", ip: "10.0.0.50" },
    options: { extended: false, snmp_communities: 0 },
    started_at: 1,
    ended_at: null,
    steps: ["target", "listen"],
    paused: [],
    report_name: null,
    tester: {},
    check: null,
    runs: [
      {
        index: 0,
        choice: {} as never,
        started_at: 2,
        finished_at: null,
        active: true,
        connection: null,
        listen: listen(status),
      },
    ],
    seq,
  };
}

const shown = () => useAuditStore.getState().session?.runs?.[0].listen?.status;

describe("a reply that arrives after a newer message", () => {
  beforeEach(() => {
    useAuditStore.getState().closeWizard();
    useAuditStore.getState().setSession(session(2, "connecting"));
  });

  it("does not put the older state back", () => {
    // Connect answers with the state as it was (seq 3), but the driver
    // connected at once and the server said so first (seq 4).
    useAuditStore.getState().applyMessage({
      type: "audit.listen", session_id: "abc123", seq: 4, run: 0, listen: listen("listening"),
    });
    useAuditStore.getState().setSession(session(3, "connecting"));
    expect(shown()).toBe("listening");
  });

  it("applies a reply as new as what is shown", () => {
    useAuditStore.getState().applyMessage({
      type: "audit.state", session_id: "abc123", seq: 3, state: session(2, "connecting"),
    });
    useAuditStore.getState().setSession(session(3, "listening"));
    expect(shown()).toBe("listening");
  });

  it("counts traffic and timeline messages too", () => {
    useAuditStore.getState().applyMessage({
      type: "audit.timeline", session_id: "abc123", seq: 5,
      entry: { t: 1, kind: "listen.connected", text: "" },
    });
    useAuditStore.getState().setSession(session(4, "not_connected"));
    expect(shown()).toBe("connecting");
  });

  it("takes the state sent on subscribing by the number inside it", () => {
    useAuditStore.getState().applyMessage({
      type: "audit.state", session_id: "abc123", state: session(6, "listening"),
    });
    expect(useAuditStore.getState().seq).toBe(6);
  });

  it("starts counting again for another audit", () => {
    useAuditStore.getState().applyMessage({
      type: "audit.listen", session_id: "abc123", seq: 9, run: 0, listen: listen("listening"),
    });
    useAuditStore.getState().setSession({ ...session(1, "connecting"), session_id: "def456" });
    expect(useAuditStore.getState().session?.session_id).toBe("def456");
    expect(useAuditStore.getState().seq).toBe(1);
  });
});
