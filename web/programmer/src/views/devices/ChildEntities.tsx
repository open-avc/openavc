import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronRight, Pencil, RefreshCw } from "lucide-react";
import * as api from "../../api/restClient";
import { useConnectionStore } from "../../store/connectionStore";
import type {
  ChildEntitiesListResponse,
  ChildEntityEntry,
  ChildEntityTypeSchema,
} from "../../api/types";

const ROW_HEIGHT = 36;
const EXPANDED_EXTRA = 220;
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
}: {
  deviceId: string;
  /** Controlled filter term, owned by the parent device page so one box
      filters both child rows and the Live State list. */
  search: string;
}) {
  const [data, setData] = useState<ChildEntitiesListResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeType, setActiveType] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

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
    } catch (err) {
      setLoadError(String(err));
    } finally {
      setLoading(false);
    }
  }, [deviceId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleDriverRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await api.refreshChildEntities(deviceId);
      // After the driver reconciles its child set, re-fetch so removed
      // children disappear from the list.
      await reload();
    } catch (err) {
      setRefreshError(String(err));
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

      {refreshError && (
        <div style={{ ...errorStyle, marginBottom: "var(--space-sm)" }}>
          {refreshError.includes("501")
            ? "This driver doesn't support re-discovering its children from the device."
            : refreshError.includes("503")
            ? "Device is not connected — cannot refresh."
            : `Refresh failed: ${refreshError}`}
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
          />
        )
      )}
    </Section>
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
      name: string;
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
            name: formatStateValue(state.name),
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
      {shown.map(({ type, typeLabel, entry, name, rows }) => (
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
              {entry.label || name || "(no label)"}
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
          Showing first {SHOWN} of {results.length} matches — refine the filter to narrow.
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
}: {
  deviceId: string;
  childType: string;
  schema: ChildEntityTypeSchema;
  entries: ChildEntityEntry[];
}) {
  const liveState = useConnectionStore((s) => s.liveState);
  // local_id is a number for numbered children, a string for name-keyed
  // (dynamic / string-id) children — so all row-keyed UI state is widened.
  const [expanded, setExpanded] = useState<Set<number | string>>(new Set());
  const [editing, setEditing] = useState<{ id: number | string; value: string } | null>(null);
  const [savingId, setSavingId] = useState<number | string | null>(null);
  const [labelOverrides, setLabelOverrides] = useState<Record<string, string>>({});

  // Reset row-level UI state when the active tab changes.
  useEffect(() => {
    setExpanded(new Set());
    setEditing(null);
    setSavingId(null);
    setLabelOverrides({});
  }, [childType]);

  const summaryFields = useMemo(
    () => schema.summary_fields ?? Object.keys(schema.state_variables).slice(0, 3),
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

  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => {
      const entry = entries[index];
      return entry && expanded.has(entry.local_id)
        ? ROW_HEIGHT + EXPANDED_EXTRA
        : ROW_HEIGHT;
    },
    overscan: 6,
    // Key on padded id so virtualization survives list changes.
    getItemKey: (index) => entries[index]?.local_id_padded ?? index,
  });

  // Re-measure when expanded set changes so row heights reflow.
  useEffect(() => {
    virtualizer.measure();
  }, [expanded, virtualizer]);

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
        No {schema.label_plural?.toLowerCase() ?? schema.label?.toLowerCase() ?? "children"}{" "}
        registered yet. Connect the device or click <em>Refresh from Device</em>{" "}
        to populate the list.
      </div>
    );
  }

  const items = virtualizer.getVirtualItems();

  return (
    <>
      {/* Column header. Sticky so it stays visible while the row body
          scrolls inside the virtualizer below. */}
      <div style={headerRowStyle}>
        <div style={{ ...headerCellStyle, width: 32 }}></div>
        <div style={{ ...headerCellStyle, width: 64 }}>ID</div>
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
          height: LIST_HEIGHT,
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
            const entry = entries[virtualItem.index];
            if (!entry) return null;
            const isExpanded = expanded.has(entry.local_id);
            const isEditing = editing?.id === entry.local_id;
            const isSaving = savingId === entry.local_id;
            const liveS = liveStateForChild(entry);
            const displayLabel = labelOverrides[entry.local_id] ?? entry.label;

            return (
              <div
                key={virtualItem.key}
                data-testid={`child-row-${entry.local_id_padded}`}
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
                  <div
                    style={{
                      width: 64,
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--font-size-sm)",
                      color: "var(--text-secondary)",
                    }}
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
                        title="Click to edit"
                        style={{
                          width: "100%",
                          textAlign: "left",
                          padding: "2px 6px",
                          background: "transparent",
                          border: "none",
                          cursor: "pointer",
                          fontSize: "var(--font-size-sm)",
                          color: displayLabel
                            ? "var(--text-primary)"
                            : "var(--text-muted)",
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
