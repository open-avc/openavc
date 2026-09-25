import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Download, Loader2, Trash2 } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import { useAuditStore } from "../../../../store/auditStore";
import { fileSize, parseCommunities, pauseNotice } from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import {
  buttonStyle,
  headingStyle,
  hintStyle,
  inputStyle,
  labelStyle,
  panelStyle,
  spinStyle,
} from "../auditStyles";

/** Step 1: which device, and what is already using it. */
export function TargetStep() {
  const preset = useAuditStore((s) => s.presetAddress);
  const presetDevice = useAuditStore((s) => s.presetDevice);
  const [address, setAddress] = useState(preset);
  const [extended, setExtended] = useState(false);
  const [communities, setCommunities] = useState("");
  const [showOptions, setShowOptions] = useState(false);
  const [conflicts, setConflicts] = useState<audit.AuditConflicts | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // A different address means a different set of devices to pause.
  useEffect(() => {
    setConflicts(null);
  }, [address]);

  // From a device page, say up front which project devices the audit pauses.
  const fromDevice = presetDevice && address.trim() === preset ? presetDevice : "";
  useEffect(() => {
    if (!fromDevice || !preset) return;
    let alive = true;
    audit
      .getAuditConflicts(preset)
      .then((found) => {
        if (alive && found.devices.length > 0) setConflicts(found);
      })
      .catch(() => {
        /* the check runs again on Continue */
      });
    return () => {
      alive = false;
    };
  }, [fromDevice, preset]);

  const start = useCallback(
    async (pause: string[]) => {
      const { session } = await audit.startAudit({
        address: address.trim(),
        pause,
        extended,
        snmp_communities: parseCommunities(communities),
        from_device: fromDevice || null,
      });
      const store = useAuditStore.getState();
      store.setSession(session);
      const { session: running } = await audit.startNetworkCheck(session.session_id);
      store.setSession(running);
      store.setStep("network");
    },
    [address, extended, communities, fromDevice],
  );

  const onContinue = useCallback(async () => {
    setError("");
    setBusy(true);
    try {
      if (conflicts && conflicts.devices.length > 0) {
        await start(conflicts.devices.map((d) => d.device_id));
        return;
      }
      const found = await audit.getAuditConflicts(address.trim());
      if (!found.resolved) {
        setError(
          `OpenAVC could not find ${address.trim()}. Check the spelling, or enter the ` +
            "device's IP address.",
        );
        return;
      }
      if (found.devices.length > 0) {
        setConflicts(found);
        return;
      }
      await start([]);
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy(false);
    }
  }, [address, conflicts, start]);

  const needsPause = !!conflicts && conflicts.devices.length > 0;
  const ready = address.trim().length > 0 && !busy;

  return (
    <div style={{ maxWidth: 640 }}>
      <h2 style={headingStyle}>Which device?</h2>
      <label htmlFor="audit-address" style={labelStyle}>
        IP address or host name
      </label>
      <input
        id="audit-address"
        value={address}
        onChange={(e) => setAddress(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && ready) void onContinue();
        }}
        placeholder="192.168.1.50"
        autoComplete="off"
        spellCheck={false}
        style={inputStyle}
      />

      <ul
        style={{
          margin: "var(--space-md) 0",
          paddingLeft: "var(--space-lg)",
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          lineHeight: 1.6,
        }}
      >
        <li>
          Close any other software connected to this device, such as the manufacturer's control
          app. Some devices answer only one connection at a time.
        </li>
        <li>
          Run this on a computer on the same network as the device, so OpenAVC can see its MAC
          address and its network announcements.
        </li>
      </ul>

      <button
        type="button"
        onClick={() => setShowOptions(!showOptions)}
        aria-expanded={showOptions}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: "var(--text-secondary)",
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-sm)",
        }}
      >
        {showOptions ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Options
      </button>
      {showOptions && (
        <div style={{ ...panelStyle, marginTop: "var(--space-sm)" }}>
          <fieldset style={{ border: "none", margin: 0, padding: 0 }}>
            <legend style={labelStyle}>Check</legend>
            <label style={{ display: "flex", gap: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
              <input
                type="radio"
                name="audit-depth"
                checked={!extended}
                onChange={() => setExtended(false)}
              />
              <span>
                Standard <span style={hintStyle}>(about a minute)</span>
              </span>
            </label>
            <label
              style={{
                display: "flex",
                gap: "var(--space-sm)",
                fontSize: "var(--font-size-sm)",
                marginTop: "var(--space-xs)",
              }}
            >
              <input
                type="radio"
                name="audit-depth"
                checked={extended}
                onChange={() => setExtended(true)}
              />
              <span>
                Extended{" "}
                <span style={hintStyle}>
                  (also checks every port from 1 to 1024 and reads every SNMP value; slower)
                </span>
              </span>
            </label>
          </fieldset>
          <label htmlFor="audit-snmp" style={{ ...labelStyle, marginTop: "var(--space-md)" }}>
            SNMP read community
          </label>
          <input
            id="audit-snmp"
            value={communities}
            onChange={(e) => setCommunities(e.target.value)}
            placeholder="public"
            autoComplete="new-password"
            spellCheck={false}
            style={inputStyle}
          />
          <div style={hintStyle}>
            "public" is always tried. Add the device's own read community if it has one. It is
            not written into the report.
          </div>
        </div>
      )}

      {needsPause && conflicts && (
        <div
          role="status"
          style={{
            ...panelStyle,
            marginTop: "var(--space-lg)",
            background: "var(--color-warning-bg)",
            borderColor: "var(--color-warning)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          {pauseNotice(conflicts.devices)}
        </div>
      )}

      {error && (
        <div style={{ marginTop: "var(--space-md)" }}>
          <ErrorLine text={error} />
        </div>
      )}

      <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: "var(--space-sm)" }}>
        <button
          type="button"
          onClick={() => void onContinue()}
          disabled={!ready}
          style={buttonStyle("primary", !ready)}
        >
          {busy && <Loader2 size={14} style={spinStyle} />}
          {needsPause ? "Pause and continue" : "Start the network check"}
        </button>
      </div>

      <RecentReports />
    </div>
  );
}

function RecentReports() {
  const [reports, setReports] = useState<audit.AuditReportFile[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    audit
      .listAuditReports()
      .then((r) => setReports(r.reports))
      .catch(() => setReports([]));
  }, []);

  useEffect(load, [load]);

  if (reports.length === 0) return null;
  return (
    <div style={{ marginTop: "var(--space-xl)" }}>
      <h3 style={{ ...headingStyle, fontSize: "var(--font-size-sm)" }}>Recent reports</h3>
      {error && <ErrorLine text={error} />}
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {reports.map((r) => (
          <li
            key={r.name}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-sm)",
              padding: "var(--space-xs) 0",
              borderBottom: "1px solid var(--border-color)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {r.name}
            </span>
            <span style={{ color: "var(--text-secondary)", flexShrink: 0 }}>
              {new Date(r.modified * 1000).toLocaleString()} · {fileSize(r.size)}
            </span>
            <button
              type="button"
              aria-label={`Download ${r.name}`}
              title="Download"
              onClick={() =>
                audit.downloadSavedAuditReport(r.name).catch((e) => setError(parseApiError(e)))
              }
              style={{ ...buttonStyle("muted"), padding: "var(--space-xs) var(--space-sm)" }}
            >
              <Download size={14} />
            </button>
            <button
              type="button"
              aria-label={`Delete ${r.name}`}
              title="Delete"
              onClick={() =>
                audit
                  .deleteAuditReport(r.name)
                  .then(load)
                  .catch((e) => setError(parseApiError(e)))
              }
              style={{ ...buttonStyle("muted"), padding: "var(--space-xs) var(--space-sm)" }}
            >
              <Trash2 size={14} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
