/**
 * A monitored reading's limits form, drawn in a full-width row under the
 * reading's own row: the form is wider than any cell of a readings table. The
 * device page's Live State table and its child readings draw it the same way,
 * each with its own padding, beside the compact Monitor toggle in the row.
 */
import type { MonitorConfig } from "../../api/types";
import { MonitorLimitsPanel, type DeclaredReading } from "./MonitorControl";

export function MonitorLimitsRow({
  monitor,
  declared,
  monitors,
  onChange,
  padding,
}: {
  monitor: MonitorConfig;
  declared: DeclaredReading | undefined;
  monitors: MonitorConfig[];
  onChange: (next: MonitorConfig[], description: string) => void;
  padding: string;
}) {
  return (
    <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
      <td colSpan={3} style={{ padding }}>
        <MonitorLimitsPanel
          monitor={monitor}
          declared={declared}
          monitors={monitors}
          onChange={onChange}
        />
      </td>
    </tr>
  );
}
