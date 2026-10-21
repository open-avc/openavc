import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { CapacityInfo, CapacitySettings, OnOverflow } from "../store/api";
import { setCapacity } from "../store/api";

const SOURCE_TEXT: Record<CapacityInfo["source"], string> = {
  driver: "Numbers from the driver.",
  simulator: "The simulator's own numbers, not this device's.",
  edited: "Changed here.",
};

/** One device's capacity limit: on or off, its numbers, and the bytes lost. */
export function CapacityPanel({ deviceId, capacity }: { deviceId: string; capacity: CapacityInfo }) {
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const send = (changes: Partial<CapacitySettings> & { enabled?: boolean }) => {
    setProblem(null);
    setCapacity(deviceId, changes).catch((e: Error) => setProblem(e.message));
  };

  const status = capacity.enabled
    ? `on, ${capacity.bytes_lost} ${capacity.bytes_lost === 1 ? "byte" : "bytes"} lost`
    : "off";
  const silenceText =
    capacity.kind === "datagram"
      ? "Stop answering until Capacity is switched off"
      : "Stop answering until it reconnects";

  return (
    <div className="errors-panel capacity-panel">
      <div
        className="label"
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Capacity ({status})
      </div>
      {open && (
        <div className="capacity-body">
          <label className={`error-toggle ${capacity.enabled ? "active" : ""}`}>
            <input
              type="checkbox"
              checked={capacity.enabled}
              onChange={(e) => send({ enabled: e.target.checked })}
            />
            <span>Limit what this device can take in</span>
          </label>
          <div className="capacity-help">
            Bytes that arrive while the receive buffer is full are lost.
          </div>
          <NumberField
            label="Receive buffer"
            unit="bytes"
            value={capacity.rx_buffer}
            min={1}
            step={1}
            onCommit={(v) => send({ rx_buffer: v })}
          />
          <NumberField
            label="Time per message"
            unit="ms"
            value={capacity.process_ms}
            min={0}
            step={1}
            onCommit={(v) => send({ process_ms: v })}
          />
          <label className="capacity-field">
            <span className="capacity-field-label">When full</span>
            <select
              value={capacity.on_overflow}
              onChange={(e) => send({ on_overflow: e.target.value as OnOverflow })}
            >
              <option value="discard">Discard what does not fit</option>
              <option value="silence">{silenceText}</option>
            </select>
          </label>
          <div className="capacity-help">
            {SOURCE_TEXT[capacity.source]}
            {capacity.source === "edited" && capacity.declared && (
              <>
                {" "}
                <button
                  type="button"
                  className="capacity-link"
                  onClick={() => send({ ...capacity.declared! })}
                >
                  Use the driver's numbers
                </button>
              </>
            )}
          </div>
          {problem && <div className="capacity-problem">{problem}</div>}
        </div>
      )}
    </div>
  );
}

/** A number that is sent when the field loses focus or Enter is pressed, not
 *  on every keystroke. */
function NumberField({
  label,
  unit,
  value,
  min,
  step,
  onCommit,
}: {
  label: string;
  unit: string;
  value: number;
  min: number;
  step: number;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);

  const commit = () => {
    const parsed = Number(draft);
    if (draft.trim() === "" || !Number.isFinite(parsed)) {
      setDraft(String(value));
      return;
    }
    if (parsed !== value) onCommit(parsed);
  };

  return (
    <label className="capacity-field">
      <span className="capacity-field-label">{label}</span>
      <input
        type="number"
        value={draft}
        min={min}
        step={step}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
      <span className="capacity-unit">{unit}</span>
    </label>
  );
}
