import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, Info, RefreshCw } from "lucide-react";
import type { ProjectConfig, UIElement } from "../../api/types";
import {
  getMatrixProposals,
  type MatrixProposal,
  type ProposedDestination,
} from "../../api/matrixProposals";
import { Modal } from "../shared/Modal";
import { InlineError } from "../shared/InlineError";

/**
 * Set a matrix up from the device, instead of from the manual.
 *
 * Every field a matrix needs is already declared: which child type holds the
 * destinations, which property on each holds the routed source, which command
 * routes and which parameter is which end of it. Typing that again by hand is
 * how an 8x8 shipped as a dead 4x4 -- four hand-written glob patterns, two
 * counts and two label arrays, none of them checked against anything.
 *
 * Two rules from the plan shape this dialog and are worth knowing before
 * changing it:
 *
 * **D3 -- it proposes, the author disposes.** Nothing here is applied on its
 * own. The server's guess is right for most of the corpus and wrong for some of
 * it (an Atlona DSP's destinations are children called `input`), so every row
 * arrives with a tick, a name you can change and a place in the order, and the
 * proposal's own warnings sit above them.
 *
 * **D5 -- it writes the list out.** What lands in the project is the explicit
 * sources/destinations lists, not a live link to the device. A row appearing by
 * itself on a commissioned panel is worse than a stale one, so re-reading is a
 * button somebody presses: Re-read device keeps every tick, rename and reorder
 * whose value still exists, and marks anything the device has gained as New,
 * unticked, for the author to accept.
 */

interface Row {
  /** The opaque value the device reports and accepts. Never edited here: it is
   *  the device's word, and editing it is what the raw fields are for. */
  value: string | number;
  label: string;
  label_key?: string;
  route_key?: string;
  audio_route_key?: string;
  lock_key?: string;
  /** A source's second value, where the device REPORTS in different words from
   *  the ones its route command accepts. Read off the driver, never typed. */
  report_value?: string | number;
  route?: Record<string, unknown>[];
  include: boolean;
  /** The device has this and the project did not, at the last re-read. */
  isNew: boolean;
  /** The device lists this port and is not reaching it right now. Live: it is
   *  never written into the project. */
  offline?: boolean;
  /** The caption shown here is the DEVICE's name for this port, not one the
   *  author typed. Such a label is not written out -- the entry keeps its
   *  `label_key` and the panel reads the live name, so a rename in the rack
   *  still reaches the panel. Typing here clears this and the name is stored. */
  labelFromDevice: boolean;
}

type Axis = "sources" | "destinations";

interface MatrixSetupDialogProps {
  element: UIElement;
  project: ProjectConfig;
  onApply: (patch: Partial<UIElement>) => void;
  onClose: () => void;
}

/** The entries an axis of the current config already holds, written out. */
function existingRows(config: unknown, axis: Axis): ProposedDestination[] {
  const spec = (config as Record<string, unknown> | undefined)?.[axis];
  if (!Array.isArray(spec)) return [];
  return spec
    .map((entry) =>
      typeof entry === "object" && entry !== null
        ? (entry as ProposedDestination)
        : { value: entry as string | number, label: String(entry) },
    )
    .filter((entry) => entry.value !== undefined);
}

/**
 * The device's list and the project's, as one list of rows.
 *
 * The project wins on everything it already says -- its order, its names, the
 * fact that it chose these ports and not the others. The device contributes
 * rows the project has never seen, at the end, unticked and marked New, because
 * this is a Refresh and a row nobody asked for must not appear on a panel that
 * is already commissioned.
 *
 * First-time setup has no project list, so every device row is ticked and the
 * order is the device's.
 */
function mergeRows(
  fromDevice: ProposedDestination[],
  fromProject: ProposedDestination[],
  axis: Axis,
): Row[] {
  // The caption the PANEL will draw for a row nobody has named -- same words as
  // panel.js and the resolver, so the list here says what the wall will say
  // rather than the port's raw id (a MAC address, on an AVoIP endpoint).
  const caption = (i: number) =>
    `${axis === "sources" ? "In" : "Out"} ${i + 1}`;
  const key = (v: string | number) => String(v);
  const deviceByValue = new Map(fromDevice.map((entry) => [key(entry.value), entry]));
  const firstTime = fromProject.length === 0;

  const rows: Row[] = fromProject.map((entry, index) => {
    const device = deviceByValue.get(key(entry.value));
    deviceByValue.delete(key(entry.value));
    const label = entry.label || device?.label || caption(index);
    return {
      ...entry,
      // A key the device now declares fills one the project never had; a key
      // the project set is the author's and is left alone.
      label_key: entry.label_key ?? device?.label_key,
      route_key: entry.route_key ?? device?.route_key,
      report_value: entry.report_value ?? device?.report_value,
      offline: device?.offline,
      label,
      // A stored label that is exactly what the device currently calls the port
      // is a copy, not a decision -- every matrix set up before this picker
      // stopped writing them looks like that. Treating it as authored would
      // freeze the rack's own name onto the panel for good.
      labelFromDevice: !entry.label || entry.label === device?.label,
      include: true,
      isNew: false,
    };
  });

  for (const [index, entry] of fromDevice.entries()) {
    if (!deviceByValue.has(key(entry.value))) continue;
    rows.push({
      ...entry,
      label: entry.label || caption(index),
      include: firstTime,
      isNew: !firstTime,
      labelFromDevice: true,
    });
  }
  return rows;
}

function move(rows: Row[], index: number, delta: number): Row[] {
  const target = index + delta;
  if (target < 0 || target >= rows.length) return rows;
  const next = [...rows];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** One row, back in the shape the project stores. Empty fields are dropped so
 *  a written entry says only what it means. */
function toEntry(row: Row, axis: Axis): Record<string, unknown> {
  const entry: Record<string, unknown> = { value: row.value };
  // Only a name somebody typed. A caption copied out of the device goes
  // unwritten so the entry's `label_key` stays the one thing naming this port:
  // rename the endpoint in the rack and every panel follows, which is the whole
  // reason the key is here. Write the copy instead and the panel is stuck with
  // whatever the port was called on the afternoon the matrix was set up.
  if (row.label && !(row.labelFromDevice && row.label_key)) entry.label = row.label;
  if (row.label_key) entry.label_key = row.label_key;
  if (axis === "sources" && row.report_value !== undefined) {
    entry.report_value = row.report_value;
  }
  if (axis === "destinations") {
    if (row.route_key) entry.route_key = row.route_key;
    if (row.audio_route_key) entry.audio_route_key = row.audio_route_key;
    if (row.route) entry.route = row.route;
  }
  return entry;
}

/** The variable backing one destination's lock, named after the element and the
 *  port it belongs to. Generated rather than typed: the author has already said
 *  which destinations there are, and a lock key per row is bookkeeping. */
function lockKeyFor(elementId: string, value: string | number): string {
  const slug = String(value).replace(/[^A-Za-z0-9_]+/g, "_");
  return `var.${elementId}_lock_${slug}`;
}

export function MatrixSetupDialog({
  element,
  project,
  onApply,
  onClose,
}: MatrixSetupDialogProps) {
  const routingDevices = project.devices;
  const [deviceId, setDeviceId] = useState(routingDevices[0]?.id ?? "");
  const [proposals, setProposals] = useState<MatrixProposal[] | null>(null);
  const [live, setLive] = useState(true);
  const [chosenId, setChosenId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sources, setSources] = useState<Row[]>([]);
  const [destinations, setDestinations] = useState<Row[]>([]);
  const [setRoute, setSetRoute] = useState(true);
  // On by default where the device offers it. A destination showing one source
  // with another source's sound is the failure this prevents, and nobody can see
  // it from the panel; breakaway is the case somebody asks for deliberately.
  const [followAudio, setFollowAudio] = useState(true);
  // Off by default, like the lock itself. It costs a column and a variable per
  // destination, and most matrices do not want one.
  const [setLocks, setSetLocks] = useState(false);

  const chosen = useMemo(
    () => proposals?.find((p) => p.id === chosenId) ?? null,
    [proposals, chosenId],
  );

  // The plane carrying this one's audio, where the device routes it separately.
  // Named by the server (matrix_inference._pair_audio_planes) rather than sniffed
  // for here: which plane is which is a driver question and the plane words live
  // beside the rest of the guess.
  const audioPlane = useMemo(
    () =>
      (chosen?.audio_plane_id
        ? proposals?.find((p) => p.id === chosen.audio_plane_id)
        : null) ?? null,
    [proposals, chosen],
  );

  const seed = useCallback(
    (proposal: MatrixProposal | null) => {
      if (!proposal) {
        setSources([]);
        setDestinations([]);
        return;
      }
      setSources(
        mergeRows(
          proposal.sources, existingRows(element.matrix_config, "sources"), "sources",
        ),
      );
      setDestinations(
        mergeRows(
          proposal.destinations,
          existingRows(element.matrix_config, "destinations"),
          "destinations",
        ),
      );
    },
    [element.matrix_config],
  );

  const load = useCallback(async () => {
    if (!deviceId) return;
    setLoading(true);
    setError(null);
    try {
      const reply = await getMatrixProposals(deviceId);
      setProposals(reply.proposals);
      setLive(reply.live);
      // Keep the plane the author was already looking at across a re-read;
      // otherwise take the most confident one, which the server sorted first.
      const keep = reply.proposals.find((p) => p.id === chosenId);
      const next = keep ?? reply.proposals[0] ?? null;
      setChosenId(next?.id ?? "");
      seed(next);
    } catch (e) {
      setProposals(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [deviceId, chosenId, seed]);

  // Only on a device change: `load` closes over chosenId so it changes on every
  // pick, and depending on it here would re-read the device on each one.
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId]);

  const pickProposal = (id: string) => {
    setChosenId(id);
    seed(proposals?.find((p) => p.id === id) ?? null);
  };

  const apply = () => {
    const keptSources = sources.filter((r) => r.include);
    const keptDestinations = destinations.filter((r) => r.include);
    const withAudio = !!audioPlane && followAudio;
    // Each destination's audio key comes from the audio plane's own entry for the
    // same port, so a matrix built over a subset gets only the rows it kept.
    const audioKeys = new Map(
      (audioPlane?.destinations ?? []).map((d) => [String(d.value), d.route_key]),
    );
    const patch: Partial<UIElement> = {
      matrix_config: {
        ...(element.matrix_config as Record<string, unknown> | undefined),
        sources: keptSources.map((r) => toEntry(r, "sources")),
        destinations: keptDestinations.map((r) => {
          const audioKey = withAudio ? audioKeys.get(String(r.value)) : undefined;
          return {
            ...toEntry(r, "destinations"),
            ...(audioKey ? { audio_route_key: audioKey } : {}),
            ...(setLocks
              ? { lock_key: r.lock_key ?? lockKeyFor(element.id, r.value) }
              : {}),
          };
        }),
        ...(setLocks ? { show_lock: true } : {}),
        ...(withAudio ? { audio_follow_video: true } : {}),
      },
    };
    if (setRoute && chosen?.route) {
      const bindings = (element.bindings || {}) as Record<string, unknown>;
      const doMap = (bindings.do || {}) as Record<string, unknown>;
      patch.bindings = {
        ...bindings,
        do: {
          ...doMap,
          route: chosen.route,
          // The other half of audio-follow, and the half nobody could fill in
          // from here before: the panel sends this second action on every route
          // (panel.js sendRoute), and the Builder's only answer used to be a
          // warning telling the author to hand-author the action list this
          // picker was already holding.
          ...(withAudio && audioPlane?.route ? { audio_route: audioPlane.route } : {}),
        },
      } as UIElement["bindings"];
    }
    onApply(patch);
    onClose();
  };

  const ticked = (rows: Row[]) => rows.filter((r) => r.include).length;

  return (
    <Modal
      onClose={onClose}
      label="Set up matrix from a device"
      // Apply is pinned rather than scrolled to. The lists are as long as the
      // device has ports, so on a laptop window a scrolling footer puts the
      // button that finishes the job below the fold with nothing to say so.
      panelStyle={{
        padding: "var(--space-xl)", width: "min(880px, 94vw)", maxHeight: "88vh",
        display: "flex", flexDirection: "column",
      }}
    >
      <h3 style={{ marginBottom: 4, fontSize: "var(--font-size-lg)" }}>
        Set up from a device
      </h3>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: "var(--space-lg)" }}>
        Reads what the driver declares and fills in the sources, the destinations and
        each one&rsquo;s route key. Nothing is applied until you press Apply.
      </div>

      <div style={{ flex: "1 1 auto", minHeight: 0, overflowY: "auto" }}>
      <div style={{ display: "flex", gap: "var(--space-md)", alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ flex: "1 1 220px" }}>
          <div style={fieldLabelStyle}>Device</div>
          <select
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            style={{ width: "100%" }}
          >
            {routingDevices.length === 0 && <option value="">No devices in this project</option>}
            {routingDevices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} — {d.driver}
              </option>
            ))}
          </select>
        </label>
        {proposals !== null && proposals.length > 1 && (
          <label style={{ flex: "2 1 300px" }}>
            <div style={fieldLabelStyle}>What to route</div>
            <select
              value={chosenId}
              onChange={(e) => pickProposal(e.target.value)}
              style={{ width: "100%" }}
            >
              {proposals.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                  {p.confidence === "high" ? "" : ` (${p.confidence} confidence)`}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          type="button"
          onClick={() => void load()}
          disabled={!deviceId || loading}
          style={secondaryBtnStyle}
          title="Read this device again and pick up any ports it has gained"
        >
          <RefreshCw size={12} /> Re-read device
        </button>
      </div>

      {error && <InlineError message={`Could not read this device: ${error}`} />}

      {!loading && proposals !== null && proposals.length === 0 && (
        <div style={{ ...noteStyle, marginTop: "var(--space-md)" }}>
          <Info size={13} style={iconStyle} />
          <span>
            This driver does not declare anything that routes. Set the matrix up by
            hand in the fields below, or pick a different device.
          </span>
        </div>
      )}

      {chosen && (
        <>
          <div style={{ ...noteStyle, marginTop: "var(--space-md)" }}>
            <Info size={13} style={iconStyle} />
            <span>{chosen.why}</span>
          </div>
          {!live && chosen.warnings.length === 0 && (
            <div style={warnStyle}>
              <AlertTriangle size={13} style={iconStyle} />
              <span>
                This device has not reported its ports, so these come from what its
                driver declares. Connect it and press Re-read device to see what is
                really there.
              </span>
            </div>
          )}
          {chosen.warnings.map((w) => (
            <div key={w} style={warnStyle}>
              <AlertTriangle size={13} style={iconStyle} />
              <span>{w}</span>
            </div>
          ))}

          <div style={{ display: "flex", gap: "var(--space-lg)", marginTop: "var(--space-lg)", flexWrap: "wrap" }}>
            <RowEditor
              title="Sources"
              subtitle={`${ticked(sources)} of ${sources.length} included`}
              axis="sources"
              rows={sources}
              onChange={setSources}
            />
            <RowEditor
              title="Destinations"
              subtitle={`${ticked(destinations)} of ${destinations.length} included`}
              axis="destinations"
              rows={destinations}
              onChange={setDestinations}
            />
          </div>

          {chosen.route && (
            <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: "var(--space-md)", fontSize: 11 }}>
              <input
                type="checkbox"
                checked={setRoute}
                onChange={(e) => setSetRoute(e.target.checked)}
              />
              Also set this matrix&rsquo;s route action to{" "}
              <strong>{chosen.command_label || chosen.command}</strong>
              {" "}(replaces whatever is on it now)
            </label>
          )}

          {audioPlane && (
            <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: "var(--space-sm)", fontSize: 11 }}>
              <input
                type="checkbox"
                checked={followAudio}
                onChange={(e) => setFollowAudio(e.target.checked)}
              />
              Move the audio with it &mdash; this device switches audio separately, with{" "}
              <strong>{audioPlane.command_label || audioPlane.command}</strong>
            </label>
          )}

          <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: "var(--space-sm)", fontSize: 11 }}>
            <input
              type="checkbox"
              checked={setLocks}
              onChange={(e) => setSetLocks(e.target.checked)}
            />
            Give each destination a lock button, backed by its own variable
          </label>
        </>
      )}

      </div>

      <div style={{
        display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)",
        marginTop: "var(--space-lg)", flexShrink: 0,
      }}>
        <button type="button" onClick={onClose} style={secondaryBtnStyle}>
          Cancel
        </button>
        <button
          type="button"
          onClick={apply}
          disabled={!chosen || (ticked(sources) === 0 && ticked(destinations) === 0)}
          style={primaryBtnStyle}
        >
          Apply
        </button>
      </div>
    </Modal>
  );
}

function RowEditor({
  title,
  subtitle,
  axis,
  rows,
  onChange,
}: {
  title: string;
  subtitle: string;
  axis: Axis;
  rows: Row[];
  onChange: (rows: Row[]) => void;
}) {
  const patch = (index: number, fields: Partial<Row>) =>
    onChange(rows.map((r, i) => (i === index ? { ...r, ...fields } : r)));

  return (
    <div style={{ flex: "1 1 340px", minWidth: 300 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 4 }}>
        <strong style={{ fontSize: 12 }}>{title}</strong>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{subtitle}</span>
      </div>
      <div style={listStyle}>
        {rows.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "6px 8px" }}>
            Nothing to list. Add these by hand after applying.
          </div>
        )}
        {rows.map((row, index) => (
          <div key={String(row.value)} style={rowStyle}>
            <input
              type="checkbox"
              checked={row.include}
              onChange={(e) => patch(index, { include: e.target.checked })}
              title="Include this in the matrix"
            />
            <input
              value={row.label}
              onChange={(e) =>
                patch(index, { label: e.target.value, labelFromDevice: false })
              }
              title={
                row.labelFromDevice && row.label_key
                  ? "What the panel will show for this port, from the device. "
                    + "Type here to name it yourself instead."
                  : undefined
              }
              style={{
                flex: 1,
                minWidth: 0,
                fontSize: 11,
                color:
                  row.labelFromDevice && row.label_key
                    ? "var(--text-secondary)"
                    : undefined,
              }}
            />
            <span style={valueStyle} title={
              axis === "destinations" && row.route_key
                ? `value ${row.value} · reads ${row.route_key}`
                : row.report_value !== undefined
                  ? `sends ${row.value} · the device calls it ${row.report_value}`
                  : `value ${row.value}`
            }>
              {row.report_value !== undefined
                ? `${row.value} → ${row.report_value}`
                : row.value}
            </span>
            {row.isNew && <span style={newBadgeStyle}>new</span>}
            {row.offline && (
              <span
                style={offlineBadgeStyle}
                title="This device lists the port but is not reaching it right now"
              >
                not answering
              </span>
            )}
            <button
              type="button"
              onClick={() => onChange(move(rows, index, -1))}
              disabled={index === 0}
              style={moveBtnStyle}
              title="Move up"
            >
              <ArrowUp size={11} />
            </button>
            <button
              type="button"
              onClick={() => onChange(move(rows, index, 1))}
              disabled={index === rows.length - 1}
              style={moveBtnStyle}
              title="Move down"
            >
              <ArrowDown size={11} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

const listStyle: React.CSSProperties = {
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  maxHeight: 280,
  overflowY: "auto",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 5,
  padding: "3px 6px",
  borderBottom: "1px solid var(--border-color)",
};

const valueStyle: React.CSSProperties = {
  fontSize: 10,
  fontFamily: "var(--font-mono)",
  color: "var(--text-muted)",
  maxWidth: 90,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const offlineBadgeStyle: React.CSSProperties = {
  fontSize: 9,
  padding: "0 4px",
  borderRadius: 3,
  border: "1px solid rgba(255,152,0,0.5)",
  color: "var(--text-secondary)",
  whiteSpace: "nowrap",
};

const newBadgeStyle: React.CSSProperties = {
  fontSize: 9,
  padding: "0 4px",
  borderRadius: 3,
  background: "var(--accent-bg)",
  color: "#fff",
};

const moveBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--text-muted)",
  cursor: "pointer",
  padding: 1,
  lineHeight: 0,
};

const noteStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 6,
  padding: "6px 8px",
  borderRadius: 4,
  background: "rgba(138,180,147,0.08)",
  border: "1px solid rgba(138,180,147,0.15)",
  fontSize: 11,
  color: "var(--text-secondary)",
  lineHeight: 1.4,
};

const warnStyle: React.CSSProperties = {
  ...noteStyle,
  marginTop: 6,
  background: "rgba(255,152,0,0.12)",
  border: "1px solid rgba(255,152,0,0.4)",
  color: "var(--text-primary)",
};

const iconStyle: React.CSSProperties = { flexShrink: 0, marginTop: 1 };

const primaryBtnStyle: React.CSSProperties = {
  padding: "4px 14px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "#fff",
  fontSize: 12,
  border: "none",
  cursor: "pointer",
};

const secondaryBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 5,
  padding: "4px 12px",
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-secondary)",
  fontSize: 12,
  border: "1px solid var(--border-color)",
  cursor: "pointer",
};
