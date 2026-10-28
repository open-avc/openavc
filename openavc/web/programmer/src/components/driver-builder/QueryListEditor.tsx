import { Filter, Plus, Trash2 } from "lucide-react";
import type { DriverDefinition, DriverWhenState } from "../../api/types";
import {
  buildQueryEntry,
  carryQueryExtras,
  firstGateEntry,
  gateFieldNames,
  isEachChild,
  listReadOptions,
  namesRoutedChild,
  queryPageSize,
  queryQueryFor,
  queryWhen,
  queryWhenState,
  querySend,
  stateGateVariables,
  withPageSize,
  withWhenState,
  type QueryEntry,
} from "./queryEntryHelpers";
import { WhenStateEditor } from "./WhenStateEditor";

interface QueryListEditorProps {
  draft: DriverDefinition;
  queries: QueryEntry[];
  onChange: (next: QueryEntry[]) => void;
  /** The child type `{child_id}` names in a query sent once: a response
   *  rule's then_query, on a rule that writes children of one type. */
  routedChildType?: string;
  addLabel: string;
  /** "button" for a list of its own (Poll Queries), "link" for a list
   *  inside another editor (a response rule). */
  addStyle?: "button" | "link";
}

/**
 * The rows of a query list: `polling.queries` and a response rule's
 * `then_query`. Each row is a send string, sent once or once per child, with
 * its config gate, the state variable its reply reports, a page size for a
 * per-child list read, and a device-state condition.
 */
export function QueryListEditor({
  draft,
  queries,
  onChange,
  routedChildType,
  addLabel,
  addStyle = "button",
}: QueryListEditorProps) {
  const childTypeNames = Object.keys(draft.child_entity_types ?? {});
  const gateFields = gateFieldNames(draft);
  const stateVarNames = Object.keys(draft.state_variables ?? {});
  const listReads = listReadOptions(draft);
  const gateVariables = stateGateVariables(draft);

  const childVars = (type: string) =>
    Object.keys(draft.child_entity_types?.[type]?.state_variables ?? {});

  const setQueryGate = (index: number, gate: DriverWhenState | undefined) => {
    const next = [...queries];
    next[index] = withWhenState(queries[index], gate);
    onChange(next);
  };

  const addQuery = () => onChange([...queries, ""]);

  const removeQuery = (index: number) =>
    onChange(queries.filter((_: unknown, i: number) => i !== index));

  const updateQuery = (index: number, value: QueryEntry) => {
    const next = [...queries];
    // An edit of the send, scope, gate or pairing keeps the entry's other
    // keys (a page size) where the new shape can hold them.
    next[index] = carryQueryExtras(queries[index], value);
    onChange(next);
  };

  return (
    <>
      {queries.map((query: QueryEntry, i: number) => {
        const eachChild = isEachChild(query);
        const send = querySend(query);
        const when = queryWhen(query);
        const queryFor = queryQueryFor(query);
        const stateGate = queryWhenState(query);
        const routed =
          !eachChild && !!routedChildType && namesRoutedChild(send);
        return (
          <div key={i} style={{ marginBottom: "var(--space-xs)" }}>
          <div
            style={{
              display: "flex",
              gap: "var(--space-sm)",
              alignItems: "center",
            }}
          >
            {childTypeNames.length > 0 && (
              <select
                value={eachChild ? query.each_child : ""}
                onChange={(e) =>
                  // Changing the scope invalidates a declared state pairing
                  // (device-level vs child-level variables) — drop it.
                  updateQuery(
                    i,
                    buildQueryEntry(send, e.target.value, when, undefined, ""),
                  )
                }
                title="Send once, or once per registered child of a type"
                style={{ width: 130, fontSize: "var(--font-size-sm)" }}
              >
                <option value="">Once</option>
                {childTypeNames.map((t) => (
                  <option key={t} value={t}>
                    Per {draft.child_entity_types?.[t]?.label || t}
                  </option>
                ))}
              </select>
            )}
            <input
              value={send}
              onChange={(e) =>
                updateQuery(
                  i,
                  buildQueryEntry(
                    e.target.value,
                    eachChild ? query.each_child : "",
                    when,
                    undefined,
                    queryFor,
                  ),
                )
              }
              placeholder={
                eachChild || routedChildType
                  ? "e.g., ?VOUT{child_id}\\r"
                  : draft.transport === "osc"
                    ? "e.g., /xremote or get_status"
                    : "e.g., %1POWR ?\\r"
              }
              style={{
                flex: 1,
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
              }}
            />
            {gateFields.length > 0 && (
              <select
                value={when}
                onChange={(e) =>
                  updateQuery(
                    i,
                    buildQueryEntry(
                      send,
                      eachChild ? query.each_child : "",
                      e.target.value,
                      undefined,
                      queryFor,
                    ),
                  )
                }
                title="Only run this query while a config field is on, for example poll meters behind an 'Enable Meters' checkbox"
                style={{ width: 150, fontSize: "var(--font-size-sm)" }}
              >
                <option value="">Always</option>
                {gateFields.map((f) => (
                  <option key={f} value={f}>
                    Only if {f}
                  </option>
                ))}
              </select>
            )}
            {draft.transport !== "osc" && (() => {
              // Device-level variables on a plain query; the child type's
              // own variables on a per-child query (each child answers
              // from its own state), and on a query whose {child_id} names
              // the child a response rule writes.
              // A query sent once may also be a list read that a
              // child_list rule spreads across every child.
              const reportOptions = eachChild
                ? childVars(query.each_child)
                : routed
                  ? childVars(routedChildType!)
                  : [...stateVarNames, ...listReads];
              if (reportOptions.length === 0) return null;
              return (
                <select
                  value={queryFor}
                  onChange={(e) =>
                    updateQuery(
                      i,
                      buildQueryEntry(
                        send,
                        eachChild ? query.each_child : "",
                        when,
                        undefined,
                        e.target.value,
                      ),
                    )
                  }
                  title="Which state variable the device's reply reports, which lets the simulator answer this query without guessing from command names"
                  style={{ width: 150, fontSize: "var(--font-size-sm)" }}
                >
                  <option value="">Reports (auto)</option>
                  {reportOptions.map((v) => (
                    <option key={v} value={v}>
                      Reports {v}
                    </option>
                  ))}
                </select>
              );
            })()}
            {eachChild && (
              <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              Children per query
              <input
                type="number"
                min={2}
                value={queryPageSize(query) ?? ""}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  const next = [...queries];
                  next[i] = withPageSize(query, Number.isNaN(n) ? undefined : n);
                  onChange(next);
                }}
                placeholder="1 each"
                aria-label="Children per query"
                title="Children one reply covers. Set it for a list read that answers a page of children starting at the one asked for: the query then goes out once per page. Leave blank to send it once per child."
                style={{ width: 70, fontSize: "var(--font-size-sm)" }}
              />
              </label>
            )}
            {gateVariables.length > 0 && (
              <button
                onClick={() =>
                  setQueryGate(
                    i,
                    stateGate
                      ? undefined
                      : Object.fromEntries([firstGateEntry(gateVariables)!]),
                  )
                }
                aria-label={stateGate ? "Remove the state condition" : "Send only while the device reports a state"}
                title={stateGate ? "Remove the state condition" : "Send only while the device reports a state"}
                style={{
                  padding: "2px",
                  color: stateGate ? "var(--accent)" : "var(--text-muted)",
                }}
              >
                <Filter size={14} />
              </button>
            )}
            <button
              onClick={() => removeQuery(i)}
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <Trash2 size={14} />
            </button>
          </div>
          {stateGate && (
            <div style={{ marginLeft: childTypeNames.length > 0 ? 138 : 0 }}>
              <WhenStateEditor
                draft={draft}
                gate={stateGate}
                onChange={(gate) => setQueryGate(i, gate)}
                lead="Send only while"
              />
            </div>
          )}
          </div>
        );
      })}

      {addStyle === "link" ? (
        <button
          onClick={addQuery}
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--accent)",
            padding: "var(--space-xs) 0",
          }}
        >
          + {addLabel}
        </button>
      ) : (
        <button
          onClick={addQuery}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
            marginTop: "var(--space-sm)",
          }}
        >
          <Plus size={14} /> {addLabel}
        </button>
      )}
    </>
  );
}
