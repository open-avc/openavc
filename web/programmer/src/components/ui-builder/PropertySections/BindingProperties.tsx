import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import type { UIElement, ProjectConfig } from "../../../api/types";
import { BINDING_CAPABILITIES, type BindingCapability } from "../uiBuilderHelpers";
import { ButtonBindingEditor, type ButtonBindings } from "../../shared/ButtonBindingEditor";
import { PressBindingEditor } from "../BindingEditor/PressBindingEditor";
import { TextBindingEditor } from "../BindingEditor/TextBindingEditor";
import { FeedbackBindingEditor } from "../BindingEditor/FeedbackBindingEditor";
import { ColorBindingEditor } from "../BindingEditor/ColorBindingEditor";
import { SelectChangeEditor } from "../BindingEditor/SelectChangeEditor";
import { SelectFeedbackEditor } from "../BindingEditor/SelectFeedbackEditor";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { ConditionGroupEditor, type ConditionGroup } from "../../shared/ConditionGroupEditor";
import { useConnectionStore } from "../../../store/connectionStore";

interface BindingPropertiesProps {
  element: UIElement;
  project: ProjectConfig;
  onChange: (patch: Partial<UIElement>) => void;
}

// The UI-event tokens each interaction delivers, surfaced as the "This control"
// group in a command param's "$" picker (the Phase 4 unified resolver). The
// runtime resolves these from the firing event: $value (scaled), $input /
// $output (matrix route), $output / $mute (mute route). Plain press/release/hold
// carry no value, so they offer no token.
const VALUE_TOKEN = [
  { key: "value", label: "value — the value the user just touched (slider position, select choice, etc.)" },
];
const ROUTE_TOKENS = [
  { key: "input", label: "input — the routed input number" },
  { key: "output", label: "output — the routed output number" },
];
const MUTE_TOKENS = [
  { key: "output", label: "output — the muted output number" },
  { key: "mute", label: "mute — true when muting, false when unmuting" },
];
const EVENT_TOKENS_BY_INTERACTION: Record<string, { key: string; label: string }[]> = {
  change: VALUE_TOKEN,
  submit: VALUE_TOKEN,
  select: VALUE_TOKEN,
  route: ROUTE_TOKENS,
  audio_route: ROUTE_TOKENS,
  mute_route: MUTE_TOKENS,
  audio_mute_route: MUTE_TOKENS,
};

const INTERACTION_HELP: Record<string, string> = {
  press: "Runs when the element is tapped.",
  change: "Runs when the value changes. Use $value in command parameters for the new value.",
  submit: "Runs when the keypad value is submitted. Use $value for the entered digits.",
  select: "Runs when a list row is tapped. Use $value for the row's value.",
  route: "Runs when a crosspoint is selected. Use $input and $output.",
  audio_route: "Audio-follow-video route. Use $input and $output.",
  mute_route: "Runs when an output's mute is toggled. Use $output and $mute.",
  audio_mute_route: "Audio mute when audio-follow-video is on. Use $output and $mute.",
};

export function BindingProperties({ element, project, onChange }: BindingPropertiesProps) {
  const bindings = (element.bindings || {}) as Record<string, unknown>;
  const show = (bindings.show || {}) as Record<string, unknown>;
  const doMap = (bindings.do || {}) as Record<string, unknown>;
  const cap: BindingCapability = BINDING_CAPABILITIES[element.type] || {};

  const deviceIds = new Set(project.devices.map((d) => d.id));
  const macroIds = new Set(project.macros.map((m) => m.id));
  const pageIds = new Set(project.ui.pages.map((p) => p.id));

  const commit = (nextShow: Record<string, unknown>, nextDo: Record<string, unknown>) => {
    const next: Record<string, unknown> = { ...bindings };
    if (Object.keys(nextShow).length > 0) next.show = nextShow;
    else delete next.show;
    if (Object.keys(nextDo).length > 0) next.do = nextDo;
    else delete next.do;
    onChange({ bindings: next as UIElement["bindings"] });
  };

  const setShowKey = (key: string, value: unknown) => {
    const nextShow = { ...show };
    if (value === null || value === undefined) delete nextShow[key];
    else nextShow[key] = value;
    commit(nextShow, doMap);
  };

  const setDoKey = (interaction: string, actions: Record<string, unknown>[] | null) => {
    const nextDo = { ...doMap };
    if (!actions || actions.length === 0) delete nextDo[interaction];
    else nextDo[interaction] = actions;
    commit(show, nextDo);
  };

  // --- status helpers (inline broken/incomplete badges, matching the old panel) ---
  const actionDangling = (a: Record<string, unknown>): string | null => {
    if (a.action === "device.command" && a.device && !deviceIds.has(a.device as string)) return `Device "${a.device}" not found`;
    if (a.action === "macro" && a.macro && !macroIds.has(a.macro as string)) return `Macro "${a.macro}" not found`;
    if (a.action === "navigate" && a.page && !pageIds.has(a.page as string)) return `Page "${a.page}" not found`;
    return null;
  };
  const actionIncomplete = (a: Record<string, unknown>): boolean => {
    if (a.action === "device.command") return !a.device || !a.command;
    if (a.action === "macro") return !a.macro;
    if (a.action === "state.set") return !a.key;
    if (a.action === "navigate") return !a.page;
    if (a.action === "value_map") return !a.map || Object.keys(a.map as object).length === 0;
    return !a.action;
  };
  const actionsStatus = (raw: unknown): CardStatus | null => {
    const actions = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? [raw] : [];
    for (const a of actions as Record<string, unknown>[]) {
      const d = actionDangling(a);
      if (d) return { kind: "broken", text: d };
    }
    if ((actions as Record<string, unknown>[]).some(actionIncomplete)) return { kind: "incomplete", text: "Incomplete" };
    return null;
  };
  const keyStatus = (binding: Record<string, unknown> | undefined): CardStatus | null => {
    const key = binding?.key as string | undefined;
    if (key?.startsWith("device.")) {
      const deviceId = key.split(".")[1];
      if (!deviceIds.has(deviceId)) return { kind: "broken", text: `Device "${deviceId}" not found` };
    }
    return null;
  };

  // --- SHOWS cards ---
  const showCards: ReactNode[] = [];

  if (cap.value) {
    const valueBinding = (show.value as Record<string, unknown>) || null;
    if (cap.value.editor === "text") {
      showCards.push(
        <Card key="value" title={cap.value.label || "Text"} status={keyStatus(valueBinding ?? undefined)}>
          <TextBindingEditor
            value={valueBinding}
            project={project}
            onChange={(v) => setShowKey("value", v)}
            onClear={() => setShowKey("value", null)}
          />
        </Card>,
      );
    } else {
      const primary = cap.does?.[0]?.interaction;
      const addDeviceCommand = () => {
        const key = String(valueBinding?.key || "");
        if (!primary) return;
        const deviceId = key.split(".")[1] || "";
        const existing = (doMap[primary] as Record<string, unknown>[] | undefined) ?? [];
        setDoKey(primary, [...existing, { action: "device.command", device: deviceId, command: "", params: {} }]);
      };
      const hasDeviceCommand = !!primary && ((doMap[primary] as Record<string, unknown>[] | undefined) ?? [])
        .some((a) => a?.action === "device.command");
      showCards.push(
        <Card key="value" title={cap.value.label || "Value"} status={keyStatus(valueBinding ?? undefined)}>
          <ValueSourceEditor
            binding={valueBinding}
            link={!!cap.value.link}
            hasDeviceCommand={hasDeviceCommand}
            onChange={(v) => setShowKey("value", v)}
            onAddCommand={addDeviceCommand}
          />
        </Card>,
      );
    }
  }

  if (cap.items) {
    const itemsBinding = (show.items as Record<string, unknown>) || undefined;
    showCards.push(
      <Card key="items" title="Items" help="Populate list rows dynamically from a state key pattern (use * as a wildcard).">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <input
            value={String(itemsBinding?.key_pattern || "")}
            onChange={(e) => setShowKey("items", e.target.value ? { source: "state", key_pattern: e.target.value } : null)}
            placeholder="device.matrix.input_*_name"
            style={inputStyle}
          />
          <div style={hintStyle}>Leave blank to use the static items configured under Basic.</div>
        </div>
      </Card>,
    );
  }

  if (cap.look) {
    const lookBinding = (show.look as Record<string, unknown>) || null;
    let body: ReactNode;
    if (cap.look === "color") {
      body = (
        <ColorBindingEditor value={lookBinding} onChange={(v) => setShowKey("look", v)} onClear={() => setShowKey("look", null)} />
      );
    } else if (cap.look === "select_feedback") {
      body = (
        <SelectFeedbackEditor
          value={lookBinding}
          options={element.options ?? []}
          onChange={(v) => setShowKey("look", v)}
          onClear={() => setShowKey("look", null)}
        />
      );
    } else {
      body = (
        <FeedbackBindingEditor
          value={lookBinding}
          onChange={(v) => setShowKey("look", v)}
          onClear={() => setShowKey("look", null)}
          showImageField={element.type === "button"}
          showConditionalLabel={element.type === "button"}
        />
      );
    }
    showCards.push(
      <Card key="look" title="Appearance" help="Change the element's look based on a state value." status={keyStatus(lookBinding ?? undefined)}>
        {body}
      </Card>,
    );
  }

  // Visible-when is universal — every element gets the card.
  const visibleWhen = (show.visible_when as ConditionGroup) || undefined;
  showCards.push(
    <Card key="visible_when" title="Visible when…">
      <VisibleWhenEditor value={visibleWhen} onChange={(g) => setShowKey("visible_when", g)} />
    </Card>,
  );

  // --- DOES cards ---
  const doesCards: ReactNode[] = [];

  if (cap.buttonStyle) {
    const btnBindings: ButtonBindings = {
      press: doMap.press as Record<string, unknown>[] | undefined,
      release: doMap.release as Record<string, unknown>[] | undefined,
      hold: doMap.hold as Record<string, unknown>[] | undefined,
    };
    doesCards.push(
      <ButtonBindingEditor
        key="button"
        bindings={btnBindings}
        project={project}
        showRelease
        showLabel={false}
        showFeedback={false}
        onBindingsChange={(nb) => {
          const nextDo = { ...doMap };
          for (const slot of ["press", "release", "hold"] as const) {
            if (nb[slot]) nextDo[slot] = nb[slot] as Record<string, unknown>[];
            else delete nextDo[slot];
          }
          commit(show, nextDo);
        }}
      />,
    );
  } else {
    for (const interaction of cap.does ?? []) {
      const actions = (doMap[interaction.interaction] as Record<string, unknown>[] | undefined) ?? [];
      const tokens = EVENT_TOKENS_BY_INTERACTION[interaction.interaction];
      let body: ReactNode;
      if (interaction.editor === "select_change") {
        const single = (doMap[interaction.interaction] as Record<string, unknown> | undefined) ?? null;
        body = (
          <SelectChangeEditor
            value={Array.isArray(single) ? (single[0] as Record<string, unknown>) ?? null : single}
            project={project}
            options={element.options ?? []}
            onChange={(v) => setDoKey(interaction.interaction, v ? [v] : null)}
            onClear={() => setDoKey(interaction.interaction, null)}
            eventTokens={tokens}
          />
        );
      } else {
        body = (
          <PressBindingEditor
            value={actions}
            project={project}
            onChange={(v) => setDoKey(interaction.interaction, v)}
            onClear={() => setDoKey(interaction.interaction, null)}
            forChangeBinding={interaction.interaction === "change"}
            eventTokens={tokens}
          />
        );
      }
      doesCards.push(
        <Card
          key={interaction.interaction}
          title={interaction.label}
          help={INTERACTION_HELP[interaction.interaction]}
          status={actionsStatus(doMap[interaction.interaction])}
        >
          {body}
        </Card>,
      );
    }
  }

  // Interactive control with nothing wired — nudge the user.
  const hasAnyAction = (cap.does ?? []).some((d) => {
    const v = doMap[d.interaction];
    return Array.isArray(v) ? v.length > 0 : !!v;
  }) || (cap.buttonStyle && !!doMap.press);
  const hasTwoWay = !!(show.value as Record<string, unknown> | undefined)?.write_back;
  const isInteractive = !!cap.buttonStyle || (cap.does?.length ?? 0) > 0;
  const showUnboundWarning = isInteractive && !hasAnyAction && !hasTwoWay;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
      <Bucket label="Shows" hint="What this control reflects from live state.">
        {showCards}
      </Bucket>

      {(cap.buttonStyle || (cap.does?.length ?? 0) > 0) && (
        <Bucket label="Does" hint="What happens when the user touches this control.">
          {showUnboundWarning && (
            <div style={warnBoxStyle}>
              <AlertTriangle size={14} style={{ flexShrink: 0 }} />
              <span>This {element.type} has no action yet, so touching it does nothing.</span>
            </div>
          )}
          {doesCards}
        </Bucket>
      )}
    </div>
  );
}

// --- Value source picker with the device-aware LINK (two-way) switch ---

function ValueSourceEditor({
  binding,
  link,
  hasDeviceCommand,
  onChange,
  onAddCommand,
}: {
  binding: Record<string, unknown> | null;
  link: boolean;
  hasDeviceCommand: boolean;
  onChange: (value: Record<string, unknown> | null) => void;
  onAddCommand: () => void;
}) {
  const key = String(binding?.key || "");
  const liveValue = useConnectionStore((s) => (key ? s.liveState[key] : undefined));
  const isVar = key.startsWith("var.");
  const isDevice = key.startsWith("device.");
  const writeBack = !!binding?.write_back;

  const setKey = (newKey: string) => {
    if (!newKey) {
      onChange(null);
      return;
    }
    const v: Record<string, unknown> = { source: "state", key: newKey };
    // write_back (LINK) is only valid for a writable var.* key; a device key
    // drives the hardware through a command instead, so it never carries it.
    if (writeBack && newKey.startsWith("var.")) v.write_back = true;
    onChange(v);
  };

  const setWriteBack = (on: boolean) => {
    const v = { ...(binding || {}) };
    if (on) v.write_back = true;
    else delete v.write_back;
    onChange(v);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <VariableKeyPicker value={key} onChange={setKey} placeholder="Select state key..." />
      {key && liveValue !== undefined && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", background: "var(--bg-surface)", borderRadius: 4, fontSize: 11 }}>
          <span style={{ color: "var(--text-muted)" }}>Current value:</span>
          <span style={{ fontWeight: 500 }}>{String(liveValue)}</span>
        </div>
      )}

      {link && key && isVar && (
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
          <input type="checkbox" checked={writeBack} onChange={(e) => setWriteBack(e.target.checked)} />
          Two-way (this control can change it)
        </label>
      )}

      {link && key && isDevice && (
        hasDeviceCommand ? (
          <div style={{ ...hintStyle, color: "var(--accent)", fontStyle: "normal" }}>
            ✓ Touching this control sends a command (configured under Does).
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={hintStyle}>
              A device value is read-only here. To let this control change the device, add a command
              that uses <strong>$value</strong>.
            </div>
            <button onClick={onAddCommand} style={linkBtnStyle}>+ Add a change command</button>
          </div>
        )
      )}

      {binding && (
        <button onClick={() => onChange(null)} style={clearBtnStyle}>Remove Binding</button>
      )}
    </div>
  );
}

// --- Visible-when card body (universal conditional visibility) ---

function VisibleWhenEditor({
  value,
  onChange,
}: {
  value: ConditionGroup | undefined;
  onChange: (group: ConditionGroup | undefined) => void;
}) {
  const hasCondition = value != null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={hasCondition}
          onChange={(e) => onChange(e.target.checked ? { key: "", operator: "truthy" } : undefined)}
        />
        Show only when…
      </label>
      {hasCondition && (
        <div style={{ marginLeft: 20 }}>
          <ConditionGroupEditor
            value={value}
            onChange={onChange}
            required
            anyHint="Element is visible when any condition is true."
            allHint="Element is visible when all conditions are true."
          />
        </div>
      )}
    </div>
  );
}

// --- Layout primitives ---

interface CardStatus {
  kind: "broken" | "incomplete";
  text: string;
}

function Bucket({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.6px", textTransform: "uppercase", color: "var(--accent)" }}>
          {label}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{hint}</div>
      </div>
      {children}
    </div>
  );
}

function Card({ title, help, status, children }: { title: string; help?: string; status?: CardStatus | null; children: ReactNode }) {
  return (
    <div
      style={{
        border: `1px solid ${status?.kind === "broken" ? "var(--color-error)" : "var(--border-color)"}`,
        borderRadius: "var(--border-radius)",
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, padding: "6px 10px", background: "var(--bg-surface)" }}>
        <span style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>{title}</span>
        {status && (
          <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: status.kind === "broken" ? "var(--color-error)" : "var(--color-warning)" }}>
            <AlertTriangle size={12} />
            {status.kind === "broken" ? "Broken" : "Incomplete"}
          </span>
        )}
      </div>
      <div style={{ padding: "var(--space-sm)", background: "var(--bg-base)", borderTop: "1px solid var(--border-color)" }}>
        {help && <div style={{ ...hintStyle, marginBottom: 6 }}>{help}</div>}
        {children}
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
};

const hintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  lineHeight: 1.4,
  fontStyle: "italic",
};

const clearBtnStyle: React.CSSProperties = {
  padding: "4px 8px",
  borderRadius: "var(--border-radius)",
  fontSize: "var(--font-size-sm)",
  color: "var(--color-error)",
  background: "transparent",
  border: "1px solid var(--border-color)",
  alignSelf: "flex-start",
  cursor: "pointer",
};

const linkBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 4,
  padding: "5px 10px",
  borderRadius: "var(--border-radius)",
  border: "1px dashed var(--border-color)",
  background: "transparent",
  color: "var(--text-muted)",
  fontSize: 12,
  cursor: "pointer",
};

const warnBoxStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "var(--space-sm)",
  borderRadius: "var(--border-radius)",
  background: "rgba(245,158,11,0.1)",
  border: "1px solid rgba(245,158,11,0.25)",
  fontSize: 12,
  color: "#d97706",
  lineHeight: 1.4,
};
