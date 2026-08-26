/**
 * A dropdown over a state-published option list, for a plugin panel-element
 * config field (`options_source`).
 *
 * A plain <select> is all this was, and all it still is when the list is a
 * plain list. What it adds is the case where the list has something to say:
 * rows grouped under the device they came from, a row marked because that
 * device is unreachable, and a row with no stream at all saying what is
 * missing and offering the setting that would fix it.
 *
 * That last one is the point. A source one setting away from working used to
 * publish nothing, so the picker was empty and the only way to find out why
 * was to know already. The list can now carry those rows; see OptionRow.
 *
 * Nothing here knows what a video stream is. The publisher says what a row is
 * called, which device it belongs to, why it cannot be used, and which config
 * field would change that. This renders it.
 */
import { useMemo } from "react";
import type { ReactNode } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { parseStateOptionRows, type OptionRow } from "./paramOptions";
import { byGroup, groupHeadingStyle, OptionRowCard, statusWord } from "./optionRowUi";

/** A pickable row's text. Offline is worth saying in the option itself: the
 *  list is closed most of the time, and a camera that is switched off is still
 *  the right camera to build a page against. */
function optionText(row: OptionRow): string {
  return row.status ? `${row.label} (${statusWord(row.status).toLowerCase()})` : row.label;
}

export function OptionSourcePicker({
  value,
  staticOptions,
  optionsSource,
  onChange,
  emptyHint,
}: {
  value: string;
  staticOptions?: string[];
  optionsSource?: string;
  onChange: (v: string) => void;
  /** Shown when the publisher offered nothing at all. */
  emptyHint?: ReactNode;
}) {
  const rawStateValue = useConnectionStore((s) =>
    optionsSource ? s.liveState[optionsSource] : undefined,
  );

  const rows: OptionRow[] = useMemo(() => {
    if (staticOptions && staticOptions.length > 0) {
      return staticOptions.map((o) => ({ value: o, label: o }));
    }
    return optionsSource ? parseStateOptionRows(rawStateValue) : [];
  }, [staticOptions, optionsSource, rawStateValue]);

  const pickable = rows.filter((r) => r.value !== undefined);
  const explained = rows.filter((r) => r.value === undefined);
  const grouped = byGroup(pickable);
  const showGroups = grouped.some(([g]) => g !== "");

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ width: "100%" }}>
        {/* Placeholder shown whenever nothing is selected yet. This is
            required, not cosmetic: a controlled <select value=""> with no
            option whose value is "" visually falls back to displaying the
            first option while the committed value stays "" — so picking that
            lone option fires no change event and never persists. A disabled
            empty option makes "nothing selected" a distinct state, so choosing
            a real entry is a genuine change that calls onChange. */}
        {value === "" && (
          <option value="" disabled>
            {pickable.length === 0
              ? optionsSource
                ? "(nothing to choose yet)"
                : "(no options)"
              : "Select a stream…"}
          </option>
        )}
        {/* Keep an unknown current value visible (plugin not started, or the
            entry was renamed) rather than silently switching to another. */}
        {value !== "" && !pickable.some((o) => o.value === value) && (
          <option value={value}>{value}</option>
        )}
        {showGroups
          ? grouped.map(([group, groupRows]) =>
              group === "" ? (
                groupRows.map((o) => (
                  <option key={o.value} value={o.value}>
                    {optionText(o)}
                  </option>
                ))
              ) : (
                <optgroup key={group} label={group}>
                  {groupRows.map((o) => (
                    <option key={o.value} value={o.value}>
                      {optionText(o)}
                    </option>
                  ))}
                </optgroup>
              ),
            )
          : pickable.map((o) => (
              <option key={o.value} value={o.value}>
                {optionText(o)}
              </option>
            ))}
      </select>

      {rows.length === 0 && emptyHint && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>{emptyHint}</div>
      )}

      {explained.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {byGroup(explained).map(([group, groupRows]) => (
            <div key={group || "_"} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {group && <div style={groupHeadingStyle}>{group}</div>}
              {groupRows.map((row) => (
                <OptionRowCard key={row.id ?? row.label} row={row} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
