/** Settings' two cards that write the PROJECT rather than system.json: Panel
 *  Display and Devices. They travel with the project, so a customer sets them
 *  once and one template carries them to every panel it is deployed to. The
 *  page's one Save button saves them, through the project store's dirty flag. */
import { FieldRow, SwitchRow } from "./settingsControls";
import { VariableKeyPicker } from "../../components/shared/VariableKeyPicker";
import { cardStyle, inputStyle } from "../../components/system/settingsStyles";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import type { ProjectConfig } from "../../api/types";

// Idle-dim bounds, mirroring what the server clamps to on the way out
// (openavc/api/routes/system.py). Applied on BLUR, never per keystroke:
// clamping as the user types makes a field with a floor impossible to fill in,
// because the first digit of "15" snaps to the minimum and the second lands
// after it -- typing 15 gets you 55. Blur still shows the corrected number
// before the save, so the field never claims a value that will not take.
const DIM_MINUTES_MIN = 1;
const DIM_MINUTES_MAX = 120;
// 0 is not a percentage but a mode -- blackout -- so the control's floor is 0
// while the lowest real dim level is DIM_PERCENT_MIN.
const DIM_PERCENT_MIN = 1;
const DIM_PERCENT_MAX = 90;
const DIM_BLACKOUT = 0;

// The lowest brightness the Programmer will set. Not a hardware limit -- the
// panel's own floor is lower -- but a panel dimmer than this cannot be read,
// and the control that would undo it is on somebody's laptop, not the panel.
// There used to be a "Clear" button beside this slider meaning "stop managing
// this panel". It restored nothing, so clicking it at 1% left the panel dark
// with its own control reading "not set": a stuck screen two clicks away. It
// was removed rather than fixed, because the local-override case it existed
// for is already covered -- the shell writes brightness only when this value
// CHANGES, so a panel's own brightness control keeps working regardless.
const BRIGHTNESS_MIN = 10;

const clampMinutes = (n: number) =>
  Number.isFinite(n) ? Math.min(Math.max(n, DIM_MINUTES_MIN), DIM_MINUTES_MAX) : 5;

const clampPercent = (n: number) => {
  if (!Number.isFinite(n)) return 20;
  if (n <= DIM_BLACKOUT) return DIM_BLACKOUT;
  return Math.min(Math.max(n, DIM_PERCENT_MIN), DIM_PERCENT_MAX);
};

/** Write one project setting. Merges through the project as it is now, so a
 *  write to `display` never drops `devices`, and the other way round. */
export function updateProjectSetting(group: "display" | "devices", key: string, value: unknown): void {
  const st = useProjectStore.getState();
  const cur = st.project?.settings;
  st.update({
    settings: {
      display: { ...(cur?.display ?? {}) },
      devices: { ...(cur?.devices ?? {}) },
      [group]: { ...(cur?.[group] ?? {}), [key]: value },
    },
  } as Partial<ProjectConfig>);
}

/** How the panel's screen behaves: its brightness, and dimming when idle.
 *  Drawn only where this instance drives a screen it can dim. */
export function PanelDisplayCard() {
  // Selected one primitive at a time: a selector that builds an object returns
  // a new reference every render and crashes React 19.
  const dimEnabled = useProjectStore((s) => s.project?.settings?.display?.idle_dim_enabled ?? true);

  return (
    <div style={cardStyle}>
      <BrightnessField />
      <SwitchRow
        title="Dim the panel when idle"
        help="Fades the panel down after a period with no touches. A panel left on the same page all day and night can leave a permanent ghost of it on the screen, and a dimmer picture ages the display more slowly."
        checked={dimEnabled}
        onChange={(v) => updateProjectSetting("display", "idle_dim_enabled", v)}
      />
      {dimEnabled && <>
        <DimAfterField />
        <DimToField />
        <StayBrightField />
        <WakingTouchField />
      </>}
    </div>
  );
}

/** The brightness every panel is set to, or each panel's own. */
function BrightnessField() {
  const panelBrightness = useProjectStore((s) => s.project?.settings?.display?.brightness_percent ?? null);

  return (
    <FieldRow
      label="Brightness"
      help={panelBrightness === null
        ? "Each panel is using whatever its own screen is set to. Move the slider to set them all from here."
        : "The panel's normal brightness. The panel's own brightness control still works on top of this, until you change this value again."}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
        <input
          type="range"
          min={BRIGHTNESS_MIN}
          max={100}
          style={{ flex: 1, maxWidth: 260 }}
          value={panelBrightness ?? 100}
          onChange={(e) => updateProjectSetting("display", "brightness_percent", Number(e.target.value))}
        />
        <span style={{ fontSize: "var(--font-size-sm)", minWidth: 74, color: "var(--text-muted)" }}>
          {panelBrightness === null ? "Panel's own" : `${panelBrightness}%`}
        </span>
      </div>
    </FieldRow>
  );
}

/** Minutes with no touch before the panel dims, stored as seconds. */
function DimAfterField() {
  const dimTimeout = useProjectStore((s) => s.project?.settings?.display?.idle_dim_timeout_seconds ?? 300);

  return (
    <FieldRow label="Dim after" help="Minutes with no touch before the panel dims.">
      <input
        type="number"
        min={DIM_MINUTES_MIN}
        max={DIM_MINUTES_MAX}
        style={{ ...inputStyle, maxWidth: 120 }}
        value={Math.round(dimTimeout / 60)}
        onChange={(e) => updateProjectSetting("display", "idle_dim_timeout_seconds", (parseInt(e.target.value) || 0) * 60)}
        onBlur={(e) => updateProjectSetting("display", "idle_dim_timeout_seconds", clampMinutes(parseInt(e.target.value)) * 60)}
      />
    </FieldRow>
  );
}

/** How far it dims, where 0 blacks the screen out. */
function DimToField() {
  const dimLevel = useProjectStore((s) => s.project?.settings?.display?.idle_dim_level_percent ?? 20);

  return (
    <FieldRow
      label="Dim to"
      help={dimLevel === 0
        ? "Blacked out: the screen goes as dark as the panel allows and the page is covered, so it reads as switched off. A touch brings it straight back."
        : "Percent of the panel's normal brightness, so a panel already turned down for a dark space dims further rather than brightening. Set it to 0 to black the screen out instead."}
    >
      <input
        type="number"
        min={0}
        max={DIM_PERCENT_MAX}
        style={{ ...inputStyle, maxWidth: 120 }}
        value={dimLevel}
        onChange={(e) => updateProjectSetting("display", "idle_dim_level_percent", parseInt(e.target.value) || 0)}
        onBlur={(e) => updateProjectSetting("display", "idle_dim_level_percent", clampPercent(parseInt(e.target.value)))}
      />
    </FieldRow>
  );
}

/** A value that keeps the panel bright while it is on. */
function StayBrightField() {
  const dimHoldKey = useProjectStore((s) => s.project?.settings?.display?.idle_dim_hold_state_key ?? "");
  // Present only on a panel whose software can be updated from the cloud.
  const panelSoftwareUpdates = useConnectionStore((s) => !!s.liveState["system.panel_software.version"]);

  return (
    <FieldRow
      label="Stay bright while"
      help={<>Optional. While this value is on, the panel never dims{panelSoftwareUpdates ? " and a panel software update waits to install" : ""}. A meeting in progress generates no touches, so point this at whatever the project already sets when the space is in use — a system-on variable, or a display's power state.</>}
    >
      <VariableKeyPicker
        value={dimHoldKey}
        onChange={(key) => updateProjectSetting("display", "idle_dim_hold_state_key", key)}
        showDeviceState
        placeholder="Nothing — dim on the timer alone"
      />
    </FieldRow>
  );
}

/** Whether the touch that wakes a dimmed panel also presses what it lands on. */
function WakingTouchField() {
  const dimWakePasses = useProjectStore((s) => s.project?.settings?.display?.idle_dim_wake_passes_touch ?? false);

  return (
    <SwitchRow
      title="Waking touch also presses the button"
      help="Off: the touch that wakes a dimmed panel only brings the brightness back, so nobody mutes a live room by tapping a screen they could not read. On: that touch also does whatever it landed on."
      checked={dimWakePasses}
      onChange={(v) => updateProjectSetting("display", "idle_dim_wake_passes_touch", v)}
    />
  );
}

/** How the system treats the project's devices. */
export function DevicesCard() {
  const reconnectInterval = useProjectStore((s) => s.project?.settings?.devices?.reconnect_interval_seconds ?? null);

  return (
    <div style={cardStyle}>
      <FieldRow
        label="Retry every"
        help="How often OpenAVC retries a device that has gone offline. This is the rate of connection attempts your IT department sees, so raise it on a network that is strict about them. Leave it empty to use this system's own setting."
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <input
            type="number"
            min={1}
            max={300}
            style={{ ...inputStyle, maxWidth: 120 }}
            placeholder="5"
            value={reconnectInterval ?? ""}
            onChange={(e) => updateProjectSetting(
              "devices",
              "reconnect_interval_seconds",
              e.target.value === "" ? null : (parseInt(e.target.value) || 0),
            )}
            onBlur={(e) => updateProjectSetting(
              "devices",
              "reconnect_interval_seconds",
              e.target.value === "" ? null : Math.min(Math.max(parseInt(e.target.value) || 5, 1), 300),
            )}
          />
          <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>seconds</span>
        </div>
      </FieldRow>
    </div>
  );
}
