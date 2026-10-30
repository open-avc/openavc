import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { SingleClientInfo, SingleClientMode } from "../store/api";
import { setSingleClient } from "../store/api";

/** What each choice does to the connections, said under the choice. */
const MODE_HELP: Record<SingleClientMode, string> = {
  newest:
    "The connection already open gets no more answers and is not closed. " +
    "Its controller hears nothing until it reconnects.",
  oldest: "The new connection stays open and gets no answers, not even a greeting.",
  reject: "The new connection is closed as soon as it opens.",
};

/** One device's connections: how many are open, and what it does when
 *  another one arrives. */
export function ConnectionsPanel({
  deviceId,
  singleClient,
}: {
  deviceId: string;
  singleClient: SingleClientInfo;
}) {
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const send = (mode: SingleClientMode | null) => {
    setProblem(null);
    setSingleClient(deviceId, mode).catch((e: Error) => setProblem(e.message));
  };

  const parts = [`${singleClient.open} open`];
  if (singleClient.mode) parts.unshift("one at a time");
  if (singleClient.ignored) parts.push(`${singleClient.ignored} not answered`);
  if (singleClient.refused) parts.push(`${singleClient.refused} closed`);

  return (
    <div className="errors-panel connections-panel">
      <div
        className="label"
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Connections ({parts.join(", ")})
      </div>
      {open && (
        <div className="capacity-body">
          <label className="capacity-field">
            <span className="capacity-field-label">When another arrives</span>
            <select
              value={singleClient.mode ?? ""}
              onChange={(e) => send((e.target.value || null) as SingleClientMode | null)}
            >
              <option value="">Answer it too</option>
              <option value="newest">Answer only the new one</option>
              <option value="oldest">Ignore the new one</option>
              <option value="reject">Close the new one</option>
            </select>
          </label>
          {singleClient.mode && <div className="capacity-help">{MODE_HELP[singleClient.mode]}</div>}
          {singleClient.source !== "simulator" && (
            <div className="capacity-help">
              {singleClient.source === "driver" ? "As the driver declares." : "Changed here."}
              {singleClient.source === "edited" && singleClient.declared && (
                <>
                  {" "}
                  <button
                    type="button"
                    className="capacity-link"
                    onClick={() => send(singleClient.declared)}
                  >
                    Use the driver's setting
                  </button>
                </>
              )}
            </div>
          )}
          {problem && <div className="capacity-problem">{problem}</div>}
        </div>
      )}
    </div>
  );
}
