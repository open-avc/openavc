/**
 * A device page's Live State table: one row per key the page lists
 * (`liveStateRows.ts` decides which, and how each value reads), each with the
 * Monitor toggle and its limits in a full-width row underneath, one row's
 * limits open at a time.
 *
 * A value the device is no longer reporting is marked, not removed
 * (`staleReading.tsx`): only the driver's own declared readings, since
 * `connected`, `enabled`, the fault pair and the platform's other keys are
 * statements about the device that are true right now.
 */
import { Fragment, useMemo, useState } from "react";
import type { MonitorConfig } from "../../api/types";
import { CopyButton } from "../../components/shared/CopyButton";
import { MonitorCell, MonitorControl } from "../../components/shared/MonitorControl";
import { MonitorLimitsRow } from "../../components/shared/MonitorLimitsRow";
import type { MonitorEdit } from "../../components/shared/useMonitorEdits";
import { parseStateOptionList } from "../../components/shared/paramOptions";
import { sectionStyle, sectionTitleStyle, surfaceStyle } from "./deviceSectionStyles";
import { optionFeedKeys, type LiveStateRow, type StateVariables } from "./liveStateRows";
import { DEVICE_STALE_TITLE, LastHeard, staleValueStyle } from "./staleReading";

export function LiveStateSection({
  deviceId,
  rows,
  childKeyCount,
  searchTerm,
  connected,
  stateVariables,
  commands,
  driverInfo,
  monitors,
  onMonitorsChange,
}: {
  deviceId: string;
  rows: LiveStateRow[];
  /** The child keys left to the Child Entities panel above. */
  childKeyCount: number;
  /** The filter box's words, as typed. */
  searchTerm: string;
  connected: boolean;
  stateVariables: StateVariables;
  commands: Record<string, unknown> | undefined;
  driverInfo: Record<string, unknown> | undefined;
  monitors: MonitorConfig[];
  onMonitorsChange: MonitorEdit;
}) {
  // Which row has its Monitor limits open. One at a time: the panel is drawn
  // in a full-width row of its own, and a table with six of them open stops
  // being a table.
  const [openMonitor, setOpenMonitor] = useState<string | null>(null);
  const feeds = useMemo(() => optionFeedKeys(commands, driverInfo), [commands, driverInfo]);

  return (
    <div style={sectionStyle}>
      <h3 style={sectionTitleStyle}>Live State</h3>
      {childKeyCount > 0 && <ChildKeysNote count={childKeyCount} />}
      <div style={{ ...surfaceStyle, overflow: "hidden" }}>
        {rows.length === 0 ? (
          <div
            style={{
              padding: "var(--space-lg)",
              color: "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {searchTerm.trim() ? `No state matches "${searchTerm}"` : "No state values yet"}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <tbody>
              {rows.map((row) => {
                const fullKey = `device.${deviceId}.${row.key}`;
                return (
                  <StateRow
                    key={row.key}
                    row={row}
                    fullKey={fullKey}
                    stale={!connected && row.shown !== "" && row.key in stateVariables}
                    isFeed={feeds.has(row.key)}
                    declared={stateVariables[row.key]}
                    monitors={monitors}
                    onMonitorsChange={onMonitorsChange}
                    open={openMonitor === fullKey}
                    onOpenChange={(next) => setOpenMonitor(next ? fullKey : null)}
                  />
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function ChildKeysNote({ count }: { count: number }) {
  return (
    <div
      style={{
        fontSize: 11,
        color: "var(--text-muted)",
        marginTop: "-4px",
        marginBottom: "var(--space-sm)",
      }}
    >
      {count.toLocaleString()} child-entity state{" "}
      {count === 1 ? "key is" : "keys are"} shown in the
      Child Entities tab above.
    </div>
  );
}

function StateRow({
  row,
  fullKey,
  stale,
  isFeed,
  declared,
  monitors,
  onMonitorsChange,
  open,
  onOpenChange,
}: {
  row: LiveStateRow;
  fullKey: string;
  /** The last value heard from a device that is not answering. */
  stale: boolean;
  isFeed: boolean;
  declared: StateVariables[string] | undefined;
  monitors: MonitorConfig[];
  onMonitorsChange: MonitorEdit;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const monitor = monitors.find((m) => m.key === fullKey);
  return (
    <Fragment>
      <tr
        data-testid={`live-state-${row.key}`}
        style={{ borderBottom: monitor && open ? undefined : "1px solid var(--border-color)" }}
      >
        <td
          style={{
            padding: "var(--space-sm) var(--space-md)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            width: "40%",
          }}
        >
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            {row.key}
            <CopyButton value={fullKey} size={11} title="Copy full state key" />
          </span>
        </td>
        <td
          title={stale ? DEVICE_STALE_TITLE : row.full}
          style={{
            padding: "var(--space-sm) var(--space-md)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            ...(stale ? staleValueStyle : null),
          }}
        >
          {isFeed ? <OptionFeedValue raw={row.shown} /> : row.shown}
          {stale && <LastHeard />}
        </td>
        {/* Most of what a room's health is lives here (lamp hours, fault
            flags, input signal presence, a DSP's temperature), so a reading
            is tagged for monitoring where it is read. */}
        <MonitorCell padding="var(--space-xs) var(--space-md)">
          <MonitorControl
            compact
            toggleOnly
            stateKey={fullKey}
            declared={declared}
            monitors={monitors}
            liveValue={row.value}
            onChange={onMonitorsChange}
            open={open}
            onOpenChange={onOpenChange}
          />
        </MonitorCell>
      </tr>
      {monitor && open && (
        <MonitorLimitsRow
          monitor={monitor}
          declared={declared}
          monitors={monitors}
          onChange={onMonitorsChange}
          padding="0 var(--space-md) var(--space-md)"
        />
      )}
    </Fragment>
  );
}

/** A picker's option list, read by its labels; the raw value when it does
 *  not parse (empty, not yet published, malformed). */
function OptionFeedValue({ raw }: { raw: string }) {
  const options = parseStateOptionList(raw);
  if (options.length === 0) return <>{raw}</>;
  const labels = options.map((o) => o.label);
  const MAX_SHOWN = 8;
  const shown = labels.slice(0, MAX_SHOWN).join(", ");
  const more = labels.length - MAX_SHOWN;
  return (
    <span title={`${labels.length} options: ${labels.join(", ")}`}>
      <span style={{ color: "var(--text-muted)" }}>
        {labels.length} option{labels.length === 1 ? "" : "s"}:{" "}
      </span>
      {shown}
      {more > 0 && (
        <span style={{ color: "var(--text-muted)" }}> (+{more} more)</span>
      )}
    </span>
  );
}
