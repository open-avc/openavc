import type {
  DriverDefinition,
  DriverRoutingDef,
  DriverRoutingPlane,
} from "../../api/types";

interface RoutingEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};
const helpStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
  marginTop: "var(--space-xs)",
};
const boxStyle: React.CSSProperties = {
  display: "grid",
  gap: "var(--space-md)",
  padding: "var(--space-md)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-surface)",
};

/** Parameter names of one declared command, for the end-pickers. */
function paramNames(draft: DriverDefinition, command?: string): string[] {
  if (!command) return [];
  const entry = (draft.commands ?? {})[command] as
    | { params?: Record<string, unknown> }
    | undefined;
  return Object.keys(entry?.params ?? {});
}

/** State variable names of a child type, or of the device when none is set. */
function propertyNames(draft: DriverDefinition, childType?: string): string[] {
  if (!childType) return Object.keys(draft.state_variables ?? {});
  const schema = (draft.child_entity_types ?? {})[childType] as
    | { state_variables?: Record<string, unknown> }
    | undefined;
  return Object.keys(schema?.state_variables ?? {});
}

/**
 * Edits the `routing` block — where this driver's routing lives, so a Matrix
 * control can be set up from the device instead of typed in from the manual.
 *
 * Everything here is a pick from what the driver already declares: a child
 * type, a property on it, a command, that command's parameters. Typing any of
 * them by hand is how a name goes stale without anything noticing, and the
 * failure is silent — a Matrix whose crosspoints never light.
 *
 * Leaving it off is a normal answer. Without it the platform reads the same
 * driver and guesses, which is right for an ordinary frame; this is for the
 * cases a guess cannot reach — a routing command needing a fixed extra
 * parameter, a device that routes itself and has no destination child, or a
 * property that merely reads like routing and is not one.
 */
export function RoutingEditor({ draft, onUpdate }: RoutingEditorProps) {
  const routing = draft.routing;
  const enabled = !!routing;
  const planes: DriverRoutingPlane[] = routing?.planes ?? [];

  const childTypes = Object.keys(draft.child_entity_types ?? {});
  const commandNames = Object.keys(draft.commands ?? {});

  const setEnabled = (next: boolean) => {
    onUpdate(
      next
        ? { routing: { planes: [{ route_property: "" }] } }
        : { routing: undefined },
    );
  };

  const update = (partial: Partial<DriverRoutingDef>) => {
    onUpdate({ routing: { ...(routing as DriverRoutingDef), ...partial } });
  };

  const updatePlane = (index: number, partial: Partial<DriverRoutingPlane>) => {
    update({
      planes: planes.map((p, i) => (i === index ? { ...p, ...partial } : p)),
    });
  };

  const movePlane = (index: number, by: number) => {
    const to = index + by;
    if (to < 0 || to >= planes.length) return;
    const next = [...planes];
    [next[index], next[to]] = [next[to], next[index]];
    update({ planes: next });
  };

  // A plane inherits the block's defaults, so an inherited value has to be
  // visible where it is overridden — otherwise a blank box reads as "nothing"
  // when it means "whatever the block said".
  const inherited = (key: keyof DriverRoutingDef & keyof DriverRoutingPlane) =>
    (routing?.[key] as string | undefined) ?? "";

  const effective = (plane: DriverRoutingPlane, key: "destination_child_type" | "source_child_type" | "command") =>
    plane[key] ?? (routing?.[key] as string | undefined) ?? "";

  /** Fixed extras, edited as name/value rows over the command's own params. */
  const setFixed = (
    index: number,
    entries: [string, unknown][],
  ) => {
    updatePlane(index, {
      params: entries.length ? Object.fromEntries(entries) : undefined,
    });
  };

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginTop: 0,
          marginBottom: "var(--space-md)",
        }}
      >
        Says where this driver's routing lives, so somebody building a panel
        can set a Matrix up from the device rather than typing its ports in by
        hand. Optional: without it the platform reads the same driver and works
        it out, which is right for an ordinary switcher. Declare it when that
        would get it wrong — a routing command that needs a fixed extra
        parameter (a signal or stream selector), a device that routes itself
        and has no output ports to list, or a property that reads like routing
        and is not one. Declaring it replaces the guess: these become the
        routing planes offered, in this order.
      </p>

      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          fontSize: "var(--font-size-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Declare where routing lives
      </label>

      {enabled && (
        <div style={boxStyle}>
          <div>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                marginBottom: "var(--space-sm)",
              }}
            >
              Shared by every plane
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr",
                gap: "var(--space-md)",
              }}
            >
              <div>
                <label style={labelStyle}>Destinations are</label>
                <select
                  value={inherited("destination_child_type")}
                  onChange={(e) =>
                    update({
                      destination_child_type: e.target.value || undefined,
                    })
                  }
                  style={{ width: "100%" }}
                >
                  <option value="">This device itself</option>
                  {childTypes.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <div style={helpStyle}>
                  The ports routed TO, in your own words — outputs, decoders,
                  zones, or the input channels of a mixer. Leave it as this
                  device when the device shows one thing at a time and has no
                  output ports of its own.
                </div>
              </div>
              <div>
                <label style={labelStyle}>Sources are</label>
                <select
                  value={inherited("source_child_type")}
                  onChange={(e) =>
                    update({ source_child_type: e.target.value || undefined })
                  }
                  style={{ width: "100%" }}
                >
                  <option value="">From the routing command</option>
                  {childTypes.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <div style={helpStyle}>
                  The ports routed FROM. Leave it on the routing command when
                  the sources are its own accepted values rather than a list of
                  ports.
                </div>
              </div>
              <div>
                <label style={labelStyle}>Routing command</label>
                <select
                  value={inherited("command")}
                  onChange={(e) =>
                    update({ command: e.target.value || undefined })
                  }
                  style={{ width: "100%" }}
                >
                  <option value="">None — each plane names its own</option>
                  {commandNames.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <div style={helpStyle}>
                  The command that performs a route, when every plane uses the
                  same one.
                </div>
              </div>
            </div>
          </div>

          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: "var(--space-sm)",
              }}
            >
              <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>
                Routing planes
              </div>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  update({ planes: [...planes, { route_property: "" }] })
                }
              >
                + Add plane
              </button>
            </div>
            <div style={helpStyle}>
              One per independently routable thing. Usually one. A decoder that
              routes video, audio, IR, RS-232, USB and CEC separately declares
              six, because each is its own Matrix watching its own property.
            </div>

            {planes.map((plane, index) => {
              const planeCommand = effective(plane, "command");
              const planeChild = effective(plane, "destination_child_type");
              const params = paramNames(draft, planeCommand);
              const properties = propertyNames(draft, planeChild || undefined);
              const fixed = Object.entries(plane.params ?? {});
              const ends = [
                plane.destination_param ?? inherited("destination_param"),
                plane.source_param ?? inherited("source_param"),
              ];
              return (
                <div
                  key={index}
                  style={{
                    ...boxStyle,
                    marginTop: "var(--space-md)",
                    background: "var(--bg-base)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                    }}
                  >
                    <div
                      style={{
                        fontSize: "var(--font-size-sm)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      Plane {index + 1}
                    </div>
                    <div style={{ display: "flex", gap: "var(--space-xs)" }}>
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={index === 0}
                        onClick={() => movePlane(index, -1)}
                        title="Move up"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={index === planes.length - 1}
                        onClick={() => movePlane(index, 1)}
                        title="Move down"
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        onClick={() =>
                          update({
                            planes: planes.filter((_, i) => i !== index),
                          })
                        }
                      >
                        Remove
                      </button>
                    </div>
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr",
                      gap: "var(--space-md)",
                    }}
                  >
                    <div>
                      <label style={labelStyle}>Name</label>
                      <input
                        value={plane.label ?? ""}
                        onChange={(e) =>
                          updatePlane(index, {
                            label: e.target.value || undefined,
                          })
                        }
                        placeholder="e.g. Video"
                        style={{ width: "100%" }}
                      />
                      <div style={helpStyle}>
                        What to call this plane where somebody picks between
                        them. Defaults to the property's own label.
                      </div>
                    </div>
                    <div>
                      <label style={labelStyle}>
                        Reports what is routed here
                      </label>
                      <select
                        value={plane.route_property ?? ""}
                        onChange={(e) =>
                          updatePlane(index, {
                            route_property: e.target.value,
                          })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">Select a property…</option>
                        {properties.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                      <div style={helpStyle}>
                        The property a crosspoint lights from. Required —
                        without it a Matrix can switch and can never show what
                        is on.
                      </div>
                    </div>
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr 1fr",
                      gap: "var(--space-md)",
                    }}
                  >
                    <div>
                      <label style={labelStyle}>Destinations (override)</label>
                      <select
                        value={plane.destination_child_type ?? ""}
                        onChange={(e) =>
                          updatePlane(index, {
                            destination_child_type:
                              e.target.value || undefined,
                          })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">
                          Shared ({inherited("destination_child_type") ||
                            "this device itself"})
                        </option>
                        {childTypes.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label style={labelStyle}>Sources (override)</label>
                      <select
                        value={plane.source_child_type ?? ""}
                        onChange={(e) =>
                          updatePlane(index, {
                            source_child_type: e.target.value || undefined,
                          })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">
                          Shared ({inherited("source_child_type") ||
                            "from the routing command"})
                        </option>
                        {childTypes.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label style={labelStyle}>Command (override)</label>
                      <select
                        value={plane.command ?? ""}
                        onChange={(e) =>
                          updatePlane(index, {
                            command: e.target.value || undefined,
                          })
                        }
                        style={{ width: "100%" }}
                      >
                        <option value="">
                          Shared ({inherited("command") || "none"})
                        </option>
                        {commandNames.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {planeCommand && (
                    <>
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "1fr 1fr",
                          gap: "var(--space-md)",
                        }}
                      >
                        <div>
                          <label style={labelStyle}>
                            Which parameter takes the destination
                          </label>
                          <select
                            value={plane.destination_param ?? ""}
                            onChange={(e) =>
                              updatePlane(index, {
                                destination_param:
                                  e.target.value || undefined,
                              })
                            }
                            style={{ width: "100%" }}
                          >
                            <option value="">
                              {inherited("destination_param") ||
                                "Work it out from the command"}
                            </option>
                            {params.map((p) => (
                              <option key={p} value={p}>
                                {p}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <label style={labelStyle}>
                            Which parameter takes the source
                          </label>
                          <select
                            value={plane.source_param ?? ""}
                            onChange={(e) =>
                              updatePlane(index, {
                                source_param: e.target.value || undefined,
                              })
                            }
                            style={{ width: "100%" }}
                          >
                            <option value="">
                              {inherited("source_param") ||
                                "Work it out from the command"}
                            </option>
                            {params.map((p) => (
                              <option key={p} value={p}>
                                {p}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>

                      <div>
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            marginBottom: "var(--space-xs)",
                          }}
                        >
                          <label style={{ ...labelStyle, marginBottom: 0 }}>
                            Always send with this plane
                          </label>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => setFixed(index, [...fixed, ["", ""]])}
                          >
                            + Add value
                          </button>
                        </div>
                        <div style={helpStyle}>
                          Fixed values sent on every route on this plane, on
                          top of the source and destination — a decoder whose
                          one command carries <code>signal: VIDEO</code>. This
                          is what a plane's name alone cannot supply, and a
                          required parameter left out is a command the device
                          refuses.
                        </div>
                        {fixed.map(([name, value], row) => (
                          <div
                            key={row}
                            style={{
                              display: "grid",
                              gridTemplateColumns: "1fr 1fr auto",
                              gap: "var(--space-sm)",
                              marginTop: "var(--space-sm)",
                            }}
                          >
                            <select
                              value={name}
                              onChange={(e) =>
                                setFixed(
                                  index,
                                  fixed.map((entry, i) =>
                                    i === row
                                      ? [e.target.value, entry[1]]
                                      : entry,
                                  ),
                                )
                              }
                            >
                              <option value="">Select a parameter…</option>
                              {params
                                .filter((p) => !ends.includes(p))
                                .map((p) => (
                                  <option key={p} value={p}>
                                    {p}
                                  </option>
                                ))}
                            </select>
                            <input
                              value={String(value ?? "")}
                              onChange={(e) =>
                                setFixed(
                                  index,
                                  fixed.map((entry, i) =>
                                    i === row
                                      ? [entry[0], e.target.value]
                                      : entry,
                                  ),
                                )
                              }
                              placeholder="e.g. VIDEO"
                              style={{ fontFamily: "var(--font-mono)" }}
                            />
                            <button
                              type="button"
                              className="btn btn-sm btn-danger"
                              onClick={() =>
                                setFixed(
                                  index,
                                  fixed.filter((_, i) => i !== row),
                                )
                              }
                            >
                              Remove
                            </button>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
