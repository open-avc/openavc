import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronRight, Pencil, RefreshCw } from "lucide-react";
import * as api from "../../api/restClient";
import { ApiError, parseApiError } from "../../api/errors";
import { useConnectionStore } from "../../store/connectionStore";
import { CHILD_RESERVED_PROPS } from "../../api/types";
import { childPresence, childStateFor, countNotOk } from "./childPresence";
import type {
  ChildEntitiesListResponse,
  ChildEntityEntry,
  ChildEntityTypeSchema,
} from "../../api/types";

const ROW_HEIGHT = 36;
// Initial estimate for an expanded row before it's measured: the collapsed
// header plus one line per control in the child's schema (the real height is
// then measured via virtualizer.measureElement, so this only needs to be close
// enough to keep the scrollbar from jumping).
const EXPANDED_ROW_HEIGHT = 22;
const EXPANDED_PADDING = 18;
const LIST_HEIGHT = 480;

/**
 * Child Entities panel. Only renders when the device's driver declares
 * `child_entity_types`. One tab per declared type; each tab is a virtualized
 * row list keyed on padded local_id with the type's `summary_fields` as
 * columns. Inline label edits PATCH /api/devices/{id}/children/{type}/{id}.
 *
 * Cell values prefer liveState (so WS deltas update the UI instantly) but
 * fall back to the initial fetch's `state` snapshot. The set of registered
 * children is refreshed on tab change, refresh-button click, and after a
 * driver-side `refresh_children` call. Inter-fetch live state mutations
 * are picked up reactively via the connection store subscription.
 */
export function ChildEntities({
  deviceId,
  search,
  connected,
  childKeyCount,
  config,
  driverInfo,
}: {
  deviceId: string;
  /** Controlled filter term, owned by the parent device page so one box
      filters both child rows and the Live State list. */
  search: string;
  /** Live `device.<id>.connected`. Decides whether "connect the device" is
      advice or an insult. */
  connected: boolean;
  /** How many child-entity state keys this device has in live state, counted
      by the page above (the same number its Live State panel reports as
      hidden). It changes exactly when children register or deregister, which
      is the event this list has no other way to hear about. */
  childKeyCount: number;
  /** This device's saved config. A roster built from a config field is fixed
      by setting that field, so the panel has to know whether it is set. */
  config: Record<string, unknown> | undefined;
  /** The driver's DRIVER_INFO, for `config_schema` labels: the field has to be
      named the way the settings form spells it. */
  driverInfo: Record<string, unknown> | undefined;
}) {
  const [data, setData] = useState<ChildEntitiesListResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeType, setActiveType] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<unknown>(null);
  const [refreshOutcome, setRefreshOutcome] = useState<string | null>(null);
  const liveState = useConnectionStore((s) => s.liveState);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.listChildEntities(deviceId);
      setData(resp);
      setLoadError(null);
      // Pick first type on initial load, keep current selection if still valid.
      setActiveType((current) => {
        const types = Object.keys(resp.child_entity_types);
        if (current && types.includes(current)) return current;
        return types[0] ?? null;
      });
      return resp;
    } catch (err) {
      setLoadError(String(err));
      return null;
    } finally {
      setLoading(false);
    }
  }, [deviceId]);

  // On mount, and again whenever the roster moves underneath this panel --
  // which nothing used to say. A device whose child count comes from its
  // settings registers every child the moment those settings are saved, and
  // this list went on showing "Outputs 0 / Inputs 0" until somebody pressed
  // Refresh from Device, while the Live State panel on the same screen said 56
  // child keys were being shown up here.
  //
  // Two signals, because a roster can change without any child key moving.
  // `connected` covers the driver itself coming and going: the types this
  // panel is built from come from a LIVE driver, so a device that was
  // disabled while this page was open answered with no types at all, and the
  // whole section stayed gone after it was switched back on.
  const settledKeyCount = useSettled(childKeyCount, 400);
  useEffect(() => {
    void reload();
  }, [reload, settledKeyCount, connected]);

  // The refresh line is about the refresh that was just pressed, so it goes
  // away on its own rather than sitting over a list that has since changed --
  // "the device reported no children" above four outputs is the same kind of
  // contradiction this panel was fixed for.
  useEffect(() => {
    if (refreshOutcome == null) return;
    const timer = setTimeout(() => setRefreshOutcome(null), 6000);
    return () => clearTimeout(timer);
  }, [refreshOutcome]);

  const handleDriverRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshError(null);
    setRefreshOutcome(null);
    try {
      await api.refreshChildEntities(deviceId);
      // After the driver reconciles its child set, re-fetch so removed
      // children disappear from the list.
      const resp = await reload();
      // Say what happened. A refresh that succeeds and finds nothing looked
      // exactly like a button that does not work, so it was pressed again.
      setRefreshOutcome(resp ? refreshSummary(resp) : null);
    } catch (err) {
      setRefreshError(err);
    } finally {
      setRefreshing(false);
    }
  }, [deviceId, reload]);

  if (loadError) {
    return (
      <Section title="Child Entities">
        <div style={errorStyle}>Failed to load child entities: {loadError}</div>
      </Section>
    );
  }

  if (!data) {
    return loading ? (
      <Section title="Child Entities">
        <div style={mutedStyle}>Loading...</div>
      </Section>
    ) : null;
  }

  const types = Object.keys(data.child_entity_types);
  if (types.length === 0) return null; // Driver doesn't declare any.

  // Per-type "not answering" counts, for the tab badges. Read live so the
  // number moves with the WS delta rather than the last fetch.
  const downByType: Record<string, number> = {};
  for (const t of types) {
    downByType[t] = countNotOk(
      (data.children[t] ?? []).map((e) =>
        childStateFor(liveState, deviceId, t, e),
      ),
    );
  }

  const term = search.trim().toLowerCase();
  const schema = activeType ? data.child_entity_types[activeType] : null;
  const entries = activeType ? data.children[activeType] ?? [] : [];

  return (
    <Section title="Child Entities">
      {/* Type tabs — hidden while a search is active, since the search spans
          every type instead of the selected tab. */}
      {!term && (
      <div
        style={{
          display: "flex",
          gap: "var(--space-xs)",
          marginBottom: "var(--space-md)",
          flexWrap: "wrap",
        }}
        role="tablist"
        aria-label="Child entity types"
      >
        {types.map((t) => {
          const tSchema = data.child_entity_types[t];
          const count = data.children[t]?.length ?? 0;
          const label = tSchema.label_plural || tSchema.label || t;
          const isActive = t === activeType;
          return (
            <button
              key={t}
              onClick={() => setActiveType(t)}
              role="tab"
              aria-selected={isActive}
              data-testid={`child-type-tab-${t}`}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: isActive ? "var(--accent-bg)" : "var(--bg-hover)",
                color: isActive ? "var(--text-on-accent)" : "var(--text-secondary)",
                fontSize: "var(--font-size-sm)",
                fontWeight: isActive ? 600 : 400,
                border: "none",
                cursor: "pointer",
              }}
            >
              {label}
              <span style={{ marginLeft: "var(--space-xs)", opacity: 0.7 }}>
                {count}
              </span>
              {/* The health, not just the size. A bare total is what let two
                  wedged decoders sit unnoticed behind the word "8". */}
              {downByType[t] > 0 && (
                <span
                  data-testid={`child-type-down-${t}`}
                  style={{
                    marginLeft: "var(--space-xs)",
                    color: isActive ? "var(--text-on-accent)" : "var(--color-error)",
                    fontWeight: 600,
                  }}
                >
                  · {downByType[t]} down
                </span>
              )}
            </button>
          );
        })}
      </div>
      )}

      {/* Refresh (the filter box lives at the top of the device page and is
          passed in as `search`, so one box filters children + Live State). */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          marginBottom: "var(--space-sm)",
        }}
      >
        <button
          onClick={handleDriverRefresh}
          disabled={refreshing}
          title="Ask the driver to re-discover children from the device"
          data-testid="child-driver-refresh"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
            opacity: refreshing ? 0.6 : 1,
            border: "none",
            cursor: refreshing ? "wait" : "pointer",
          }}
        >
          <RefreshCw size={14} /> {refreshing ? "Refreshing..." : "Refresh from Device"}
        </button>
      </div>

      {refreshError != null && (
        <div style={{ ...errorStyle, marginBottom: "var(--space-sm)" }}>
          {refreshError instanceof ApiError && refreshError.status === 501
            ? "This driver doesn't support re-discovering its children from the device."
            : refreshError instanceof ApiError && refreshError.status === 503
            ? "Device is not connected. Cannot refresh."
            : `Refresh failed: ${parseApiError(refreshError)}`}
        </div>
      )}

      {refreshError == null && refreshOutcome && (
        <div
          style={{ ...mutedStyle, marginBottom: "var(--space-sm)" }}
          data-testid="child-refresh-outcome"
        >
          {refreshOutcome}
        </div>
      )}

      {term ? (
        <ChildSearchResults data={data} term={term} deviceId={deviceId} />
      ) : (
        schema && (
          <ChildEntityList
            deviceId={deviceId}
            childType={activeType!}
            schema={schema}
            entries={entries}
            emptyMessage={emptyRosterMessage(schema, connected, config, driverInfo)}
          />
        )
      )}
    </Section>
  );
}


/** A value that only changes once it has stopped changing.
 *
 * Children register in bursts — 56 state keys arrive over a handful of
 * updates — and re-fetching the list on each burst would fetch it four times
 * to land on the same answer.
 */
function useSettled<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    if (value === settled) return;
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, settled, delayMs]);
  return settled;
}


/** What a refresh actually did, counted off the list it produced.
 *
 * Read from the reloaded list rather than from the endpoint's `result`,
 * because drivers return their own shapes there and this sentence must not be
 * able to disagree with the tabs right below it.
 */
function refreshSummary(data: ChildEntitiesListResponse): string {
  const counted = Object.entries(data.child_entity_types)
    .map(([type, schema]) => {
      const n = data.children[type]?.length ?? 0;
      const noun = n === 1
        ? (schema.label ?? type)
        : (schema.label_plural ?? schema.label ?? type);
      return `${n} ${noun.toLowerCase()}`;
    });
  if (counted.length === 0) return "Refreshed. This driver has no child entities.";
  const nothing = Object.values(data.children).every((rows) => rows.length === 0);
  return nothing
    ? "Refreshed. The device reported no children."
    : `Refreshed: ${counted.join(", ")}.`;
}


/** Why this type has no children yet, and the one thing that would change it.
 *
 * The sentence this replaced told the user to connect a device that was
 * already connected, green dot and all. A declarative driver that covers a
 * family of frames sizes its roster from a field on the device
 * (`instances.count_from`), and until that field is filled in there are no
 * children — no cable, no reconnect and no amount of pressing Refresh from
 * Device will produce one.
 */
function emptyRosterMessage(
  schema: ChildEntityTypeSchema,
  connected: boolean,
  config: Record<string, unknown> | undefined,
  driverInfo: Record<string, unknown> | undefined,
): ReactNode {
  const noun = schema.label_plural?.toLowerCase()
    ?? schema.label?.toLowerCase()
    ?? "children";
  const instances = schema.instances ?? {};
  const field = instances.count_from ?? instances.ids_from ?? "";
  const value = field ? config?.[field] : undefined;
  const unset = value === undefined || value === null || value === "" || value === 0;

  // `count_from_state` means the hardware settles the count once it answers,
  // so the config field is only a fallback and the device is what to ask.
  if (field && unset && !instances.count_from_state) {
    const schemaField = (
      (driverInfo?.config_schema ?? {}) as Record<string, { label?: string }>
    )[field];
    return (
      <>
        No {noun} yet. This driver builds them from{" "}
        <strong>{schemaField?.label || field}</strong> in this device&rsquo;s
        settings. Set it and save.
      </>
    );
  }
  if (!connected) {
    return (
      <>
        No {noun} registered yet. Connect the device or click{" "}
        <em>Refresh from Device</em> to populate the list.
      </>
    );
  }
  return (
    <>
      No {noun} registered yet. The device has not reported any. Click{" "}
      <em>Refresh from Device</em> to ask it again.
    </>
  );
}


/**
 * Global child search. When the device-page filter has a term, this replaces
 * the per-type tabbed browse with a single flat list of every matching child
 * across ALL types — no tab is involved. A child matches by id / padded id /
 * label, by any state-key name, or by any state value, and each result shows
 * the specific state rows that matched so it's clear what was found.
 */
function ChildSearchResults({
  data,
  term,
  deviceId,
}: {
  data: ChildEntitiesListResponse;
  term: string;
  deviceId: string;
}) {
  const liveState = useConnectionStore((s) => s.liveState);

  // Index live state by `${type}/${paddedId}` once per change so per-child
  // lookup is O(1) (a loaded controller has tens of thousands of keys).
  const liveIndex = useMemo(() => {
    const idx = new Map<string, Record<string, unknown>>();
    const root = `device.${deviceId}.`;
    for (const [key, value] of Object.entries(liveState)) {
      if (!key.startsWith(root)) continue;
      const parts = key.slice(root.length).split("."); // type . padded . prop…
      if (parts.length < 3) continue;
      const bucketKey = `${parts[0]}/${parts[1]}`;
      const prop = parts.slice(2).join(".");
      let bucket = idx.get(bucketKey);
      if (!bucket) {
        bucket = {};
        idx.set(bucketKey, bucket);
      }
      bucket[prop] = value;
    }
    return idx;
  }, [liveState, deviceId]);

  const results = useMemo(() => {
    const out: {
      type: string;
      typeLabel: string;
      entry: ChildEntityEntry;
      rows: [string, string][];
    }[] = [];
    for (const type of Object.keys(data.child_entity_types)) {
      const tSchema = data.child_entity_types[type];
      const typeLabel = tSchema.label || tSchema.label_plural || type;
      for (const entry of data.children[type] ?? []) {
        const live = liveIndex.get(`${type}/${entry.local_id_padded}`);
        const state = live ? { ...entry.state, ...live } : entry.state;
        const rows = Object.entries(state)
          .filter(
            ([k, v]) =>
              k.toLowerCase().includes(term) ||
              formatStateValue(v).toLowerCase().includes(term),
          )
          .map(([k, v]) => [k, formatStateValue(v)] as [string, string]);
        const idMatch =
          String(entry.local_id).includes(term) ||
          entry.local_id_padded.toLowerCase().includes(term) ||
          (entry.label ?? "").toLowerCase().includes(term);
        if (rows.length > 0 || idMatch) {
          out.push({
            type,
            typeLabel,
            entry,
            rows,
          });
        }
      }
    }
    return out;
  }, [data, term, liveIndex]);

  if (results.length === 0) {
    return (
      <div style={mutedStyle} data-testid="child-empty-filter">
        No children match "{term}".
      </div>
    );
  }

  const SHOWN = 200;
  const shown = results.slice(0, SHOWN);

  return (
    <div
      data-testid="child-search-results"
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        maxHeight: LIST_HEIGHT,
        overflow: "auto",
      }}
    >
      {shown.map(({ type, typeLabel, entry, rows }) => (
        <div
          key={`${type}/${entry.local_id_padded}`}
          data-testid={`child-row-${entry.local_id_padded}`}
          style={{
            padding: "var(--space-sm) var(--space-md)",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: "var(--space-sm)",
              marginBottom: rows.length ? "var(--space-xs)" : 0,
            }}
          >
            <span
              style={{
                fontSize: 10,
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                color: "var(--text-muted)",
                background: "var(--bg-hover)",
                borderRadius: "var(--border-radius)",
                padding: "1px 6px",
              }}
            >
              {typeLabel}
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
              }}
            >
              {entry.local_id_padded}
            </span>
            <span style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>
              {entry.label || entry.display_name || "(no label)"}
            </span>
          </div>
          {rows.length > 0 && (
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <tbody>
                {rows.map(([k, v]) => (
                  <tr key={k} style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <td
                      style={{
                        padding: "2px 8px",
                        width: "30%",
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {k}
                    </td>
                    <td style={{ padding: "2px 8px", fontFamily: "var(--font-mono)" }}>
                      {v}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
      {results.length > SHOWN && (
        <div style={{ ...mutedStyle, border: "none" }}>
          Showing first {SHOWN} of {results.length} matches. Refine the filter to narrow.
        </div>
      )}
    </div>
  );
}


function ChildEntityList({
  deviceId,
  childType,
  schema,
  entries,
  emptyMessage,
}: {
  deviceId: string;
  childType: string;
  schema: ChildEntityTypeSchema;
  entries: ChildEntityEntry[];
  /** What to say when this type has no children, worked out by the panel
      above from what the driver declares and whether the device is up. */
  emptyMessage: ReactNode;
}) {
  const liveState = useConnectionStore((s) => s.liveState);
  // local_id is a number for numbered children, a string for name-keyed
  // (dynamic / string-id) children — so all row-keyed UI state is widened.
  const [expanded, setExpanded] = useState<Set<number | string>>(new Set());
  const [editing, setEditing] = useState<{ id: number | string; value: string } | null>(null);
  const [savingId, setSavingId] = useState<number | string | null>(null);
  const [labelOverrides, setLabelOverrides] = useState<Record<string, string>>({});
  const [troubleOnly, setTroubleOnly] = useState(false);

  // Reset row-level UI state when the active tab changes.
  useEffect(() => {
    setExpanded(new Set());
    setEditing(null);
    setSavingId(null);
    setLabelOverrides({});
    setTroubleOnly(false);
  }, [childType]);

  // Columns. An explicit `summary_fields` is honoured as authored -- the
  // driver author picked those, and second-guessing them would silently drop
  // a column somebody asked for.
  //
  // The FALLBACK skips the platform's reserved keys, which it did not used to
  // have to: the schema it reads is the EFFECTIVE one, so the reserved keys
  // are in it, and a type declaring nothing of its own used to fall back to
  // `online` + `label`. Both are now drawn by the row itself -- `online` as
  // the presence mark, `label` as its own column -- so picking them here
  // renders the same fact twice and, with the two fault keys added, would
  // have started drawing a mostly-empty `offline_reason` column on any type
  // declaring fewer than three fields of its own. No shipped driver is in
  // that position today; the docs tell dynamic-type authors to leave
  // `state_variables` empty, so the next one written would have been.
  const summaryFields = useMemo(
    () => schema.summary_fields
      ?? Object.keys(schema.state_variables).filter(
        (k) => !CHILD_RESERVED_PROPS.has(k),
      ).slice(0, 3),
    [schema],
  );

  // Index liveState by padded local_id once per liveState change so
  // lookup per child is O(1) instead of O(liveState size). Without this,
  // a 1500-child controller (Chazy max) makes the filter useMemo below
  // do 1500 * ~7500 = 11M key comparisons per keystroke, which is the
  // exact O(N*M) trap the virtualization is meant to dodge.
  const liveStateByPaddedId = useMemo(() => {
    const root = `device.${deviceId}.${childType}.`;
    const idx = new Map<string, Record<string, unknown>>();
    for (const [key, value] of Object.entries(liveState)) {
      if (!key.startsWith(root)) continue;
      const rest = key.slice(root.length);
      const dot = rest.indexOf(".");
      if (dot <= 0) continue;
      const padded = rest.slice(0, dot);
      const prop = rest.slice(dot + 1);
      let bucket = idx.get(padded);
      if (!bucket) {
        bucket = {};
        idx.set(padded, bucket);
      }
      bucket[prop] = value;
    }
    return idx;
  }, [deviceId, childType, liveState]);

  const liveStateForChild = useCallback(
    (entry: ChildEntityEntry): Record<string, unknown> => {
      const live = liveStateByPaddedId.get(entry.local_id_padded);
      return live ? { ...entry.state, ...live } : entry.state;
    },
    [liveStateByPaddedId],
  );

  // Endpoints that are not answering come first, and keep their roster order
  // among themselves. On a frame with 96 outputs the two that are down are
  // otherwise wherever they happen to fall, which is the whole complaint:
  // the answer was on screen and had to be hunted for. A stable partition,
  // not a sort — reordering healthy rows under somebody would be worse than
  // not ordering at all.
  const ordered = useMemo(() => {
    const down: ChildEntityEntry[] = [];
    const up: ChildEntityEntry[] = [];
    for (const e of entries) {
      (childPresence(liveStateForChild(e)).ok ? up : down).push(e);
    }
    return down.length === 0 ? entries : [...down, ...up];
  }, [entries, liveStateForChild]);

  const downCount = useMemo(
    () => countNotOk(entries.map((e) => liveStateForChild(e))),
    [entries, liveStateForChild],
  );

  const visible = useMemo(
    () => (troubleOnly ? ordered.filter((e) => !childPresence(liveStateForChild(e)).ok) : ordered),
    [ordered, troubleOnly, liveStateForChild],
  );

  // How much room the ID column needs. A numbered roster is two or three
  // characters; a device-enumerated one is a MAC address, and 64px drew it on
  // top of the Label column beside it.
  const idWidth = useMemo(() => {
    const longest = entries.reduce((n, e) => Math.max(n, e.local_id_padded.length), 0);
    return Math.min(140, Math.max(64, longest * 8 + 8));
  }, [entries]);

  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => {
      const entry = visible[index];
      if (!entry || !expanded.has(entry.local_id)) return ROW_HEIGHT;
      // A child's expanded panel lists one row per declared control, which for
      // dynamic children (e.g. a Q-SYS mixer) can be dozens — so size the
      // estimate to the control count instead of a flat guess. measureElement
      // corrects it to the real height once rendered.
      const props = entry.schema ?? schema.state_variables;
      const controlRows = props ? Object.keys(props).length : 0;
      return ROW_HEIGHT + EXPANDED_PADDING + controlRows * EXPANDED_ROW_HEIGHT;
    },
    overscan: 6,
    // Key on padded id so virtualization survives list changes.
    getItemKey: (index) => visible[index]?.local_id_padded ?? index,
  });

  // Row heights reflow on expand/collapse automatically: each row is measured
  // via virtualizer.measureElement (below), whose ResizeObserver re-measures
  // the real height when its expanded panel appears or disappears — so no
  // manual virtualizer.measure() call is needed here.

  const toggleExpand = useCallback((localId: number | string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(localId)) next.delete(localId);
      else next.add(localId);
      return next;
    });
  }, []);

  const startEdit = useCallback((entry: ChildEntityEntry) => {
    setEditing({
      id: entry.local_id,
      value: labelOverrides[entry.local_id] ?? entry.label,
    });
  }, [labelOverrides]);

  const saveEdit = useCallback(async () => {
    if (!editing) return;
    const { id, value } = editing;
    setSavingId(id);
    try {
      await api.patchChildEntity(deviceId, childType, id, { label: value });
      setLabelOverrides((prev) => ({ ...prev, [id]: value }));
      setEditing(null);
    } catch (err) {
      console.error("Failed to update child label", err);
    } finally {
      setSavingId(null);
    }
  }, [editing, deviceId, childType]);

  const cancelEdit = useCallback(() => setEditing(null), []);

  if (entries.length === 0) {
    return (
      <div style={mutedStyle} data-testid="child-empty">
        {emptyMessage}
      </div>
    );
  }

  const items = virtualizer.getVirtualItems();

  return (
    <>
      {/* Only offered when there is something to filter TO. A checkbox that
          can only ever produce an empty list is noise on the 95% of devices
          whose ports are all fine. */}
      {downCount > 0 && (
        <label
          data-testid="child-trouble-filter"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            marginBottom: "var(--space-sm)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={troubleOnly}
            onChange={(e) => setTroubleOnly(e.target.checked)}
          />
          Show only the {downCount === 1 ? "one that is" : `${downCount} that are`}{" "}
          not answering
        </label>
      )}

      {/* Column header. Sticky so it stays visible while the row body
          scrolls inside the virtualizer below. */}
      <div style={headerRowStyle}>
        <div style={{ ...headerCellStyle, width: 32 }}></div>
        {/* Aligns with the presence dot on every row below. */}
        <div style={{ ...headerCellStyle, width: 8, flexShrink: 0 }}></div>
        <div style={{ ...headerCellStyle, width: idWidth, flexShrink: 0 }}>ID</div>
        <div style={{ ...headerCellStyle, flex: 1.5 }}>Label</div>
        {summaryFields.map((field) => (
          <div
            key={field}
            style={{ ...headerCellStyle, flex: 1, fontFamily: "var(--font-mono)" }}
          >
            {field}
          </div>
        ))}
        <div style={{ ...headerCellStyle, width: 32 }}></div>
      </div>

      <div
        ref={parentRef}
        data-testid="child-virtual-scroller"
        style={{
          // Grow with the content (the inner div is sized to the virtualizer's
          // total height) but cap at LIST_HEIGHT and scroll past it — so a few
          // children no longer leave a tall empty box, and many get a scrollbar
          // instead of being clipped. Matches the search-results list above.
          maxHeight: LIST_HEIGHT,
          overflow: "auto",
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          border: "1px solid var(--border-color)",
        }}
      >
        <div
          style={{
            height: virtualizer.getTotalSize(),
            position: "relative",
            width: "100%",
          }}
        >
          {items.map((virtualItem) => {
            const entry = visible[virtualItem.index];
            if (!entry) return null;
            const isExpanded = expanded.has(entry.local_id);
            const isEditing = editing?.id === entry.local_id;
            const isSaving = savingId === entry.local_id;
            const liveS = liveStateForChild(entry);
            // What this port is called: the label somebody typed here, else the
            // name the device itself reports — which the server already resolved
            // into `display_name` from the child type's `label_field`. Reading
            // only the project's label made a device-enumerated roster (MXNet
            // endpoints, Q-SYS components) render as seven "(no label)" rows
            // while the device was naming every one of them.
            const authored = labelOverrides[entry.local_id] ?? entry.label;
            const displayLabel = authored || entry.display_name;
            const fromDevice = !authored && !!entry.display_name;

            return (
              <div
                key={virtualItem.key}
                data-testid={`child-row-${entry.local_id_padded}`}
                data-index={virtualItem.index}
                ref={virtualizer.measureElement}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${virtualItem.start}px)`,
                  borderBottom: "1px solid var(--border-color)",
                  background: "var(--bg-surface)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    height: ROW_HEIGHT,
                    padding: "0 var(--space-md)",
                    gap: "var(--space-sm)",
                  }}
                >
                  <button
                    onClick={() => toggleExpand(entry.local_id)}
                    title={isExpanded ? "Collapse" : "Expand"}
                    data-testid={`child-expand-${entry.local_id_padded}`}
                    style={{
                      width: 24,
                      height: 24,
                      padding: 0,
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      color: "var(--text-muted)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                  <PresenceDot state={liveS} />
                  <div
                    style={{
                      width: idWidth,
                      flexShrink: 0,
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--font-size-sm)",
                      color: "var(--text-secondary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={String(entry.local_id_padded)}
                  >
                    {entry.local_id_padded}
                  </div>
                  <div style={{ flex: 1.5, minWidth: 0 }}>
                    {isEditing ? (
                      <input
                        value={editing!.value}
                        onChange={(e) =>
                          setEditing({ id: entry.local_id, value: e.target.value })
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void saveEdit();
                          if (e.key === "Escape") cancelEdit();
                        }}
                        onBlur={() => void saveEdit()}
                        autoFocus
                        placeholder={entry.display_name || undefined}
                        data-testid={`child-label-input-${entry.local_id_padded}`}
                        style={{
                          width: "100%",
                          fontSize: "var(--font-size-sm)",
                          padding: "2px 6px",
                        }}
                      />
                    ) : (
                      <button
                        onClick={() => startEdit(entry)}
                        data-testid={`child-label-${entry.local_id_padded}`}
                        title={
                          fromDevice
                            ? "Name reported by the device. Click to use your own instead."
                            : "Click to edit"
                        }
                        style={{
                          width: "100%",
                          textAlign: "left",
                          padding: "2px 6px",
                          background: "transparent",
                          border: "none",
                          cursor: "pointer",
                          fontSize: "var(--font-size-sm)",
                          color: !displayLabel
                            ? "var(--text-muted)"
                            : fromDevice
                              ? "var(--text-secondary)"
                              : "var(--text-primary)",
                          fontStyle: displayLabel ? undefined : "italic",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {displayLabel || "(no label)"}
                      </button>
                    )}
                  </div>
                  {summaryFields.map((field) => (
                    <div
                      key={field}
                      style={{
                        flex: 1,
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--font-size-sm)",
                        color: "var(--text-primary)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {formatStateValue(liveS[field])}
                    </div>
                  ))}
                  <button
                    onClick={() => startEdit(entry)}
                    disabled={isSaving}
                    title="Edit label"
                    style={{
                      width: 24,
                      height: 24,
                      padding: 0,
                      background: "transparent",
                      border: "none",
                      color: "var(--text-muted)",
                      cursor: isSaving ? "wait" : "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Pencil size={12} />
                  </button>
                </div>

                {isExpanded && (
                  <div
                    style={{
                      padding: "var(--space-sm) var(--space-md)",
                      borderTop: "1px solid var(--border-color)",
                      background: "var(--bg-base)",
                    }}
                  >
                    <table
                      style={{
                        width: "100%",
                        borderCollapse: "collapse",
                        fontSize: "var(--font-size-sm)",
                      }}
                    >
                      <tbody>
                        {/* Dynamic children carry their own discovered control
                            set in entry.schema; static children fall back to
                            the type-level schema (same for every sibling). */}
                        {Object.entries(entry.schema ?? schema.state_variables).map(
                          ([prop, _def]) => (
                            <tr
                              key={prop}
                              style={{
                                borderBottom: "1px solid var(--border-color)",
                              }}
                            >
                              <td
                                style={{
                                  padding: "2px 8px",
                                  width: "30%",
                                  fontFamily: "var(--font-mono)",
                                  color: "var(--text-secondary)",
                                }}
                              >
                                {prop}
                              </td>
                              <td
                                style={{
                                  padding: "2px 8px",
                                  fontFamily: "var(--font-mono)",
                                }}
                              >
                                {formatStateValue(liveS[prop])}
                              </td>
                            </tr>
                          ),
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          marginBottom: "var(--space-md)",
          fontWeight: 600,
        }}
      >
        {title}
      </h3>
      {children}
    </div>
  );
}


/** One glanceable mark per row: filled when the endpoint is in service,
 *  hollow and warned when it is not. The title carries the driver's own
 *  sentence where it wrote one, so hovering a bad row explains it without
 *  expanding anything. */
function PresenceDot({ state }: { state: Record<string, unknown> }) {
  const { ok, reason, detail } = childPresence(state);
  const title = ok
    ? "In service"
    : detail || (reason ? `Not in service (${reason})` : "Not answering");
  return (
    <span
      data-testid="child-presence-dot"
      data-ok={ok ? "true" : "false"}
      data-reason={reason}
      title={title}
      aria-label={title}
      style={{
        width: 8,
        height: 8,
        flexShrink: 0,
        borderRadius: "50%",
        // Hollow, not just recoloured: a ring reads as "not right" even where
        // the colour does not land (a projector-lit room, a colour-blind
        // reader), which is exactly the room this gets read in.
        background: ok ? "var(--color-success)" : "transparent",
        border: ok ? "none" : "2px solid var(--color-error)",
        boxSizing: "border-box",
      }}
    />
  );
}


function formatStateValue(v: unknown): string {
  if (v === true) return "true";
  if (v === false) return "false";
  if (v === null || v === undefined) return "";
  return String(v);
}


const mutedStyle: React.CSSProperties = {
  padding: "var(--space-lg)",
  color: "var(--text-muted)",
  fontSize: "var(--font-size-sm)",
  background: "var(--bg-surface)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
};

const errorStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--color-error-bg)",
  color: "var(--color-error)",
  fontSize: "var(--font-size-sm)",
};

const headerRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "0 var(--space-md)",
  gap: "var(--space-sm)",
  height: 28,
  background: "var(--bg-surface)",
  borderRadius: "var(--border-radius) var(--border-radius) 0 0",
  border: "1px solid var(--border-color)",
  borderBottom: "none",
};

const headerCellStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};
