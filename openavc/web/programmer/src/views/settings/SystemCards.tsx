/** Settings' plain cards: Appearance (kept in this browser, never saved),
 *  Logging, Updates and Kiosk (saved to system.json with the page's Save).
 *  Where the update settings are managed, and whether this install has a
 *  kiosk, come from the server (serverReads.ts). */
import { useState } from "react";
import { heldSetting, type EnvHold } from "../../components/system/EnvHeldNote";
import { cardStyle, inputStyle, selectStyle } from "../../components/system/settingsStyles";
import { FieldRow, NumberBox, SwitchRow } from "./settingsControls";
import type { SettingsDraft } from "./settingsDraft";

type Theme = "dark" | "light";

/** The theme this browser keeps, dark when it keeps none. index.html reads
 *  the same key before the IDE loads, so the first paint is already in it. */
function keptTheme(): Theme {
  return (localStorage.getItem("openavc-theme") as Theme) || "dark";
}

function keepTheme(theme: Theme) {
  localStorage.setItem("openavc-theme", theme);
  document.documentElement.dataset.theme = theme;
}

export function AppearanceCard() {
  const [theme, setTheme] = useState<Theme>(keptTheme);
  return (
    <div style={cardStyle}>
      <FieldRow label="Theme">
        <select
          style={selectStyle}
          value={theme}
          onChange={(e) => {
            const t = e.target.value as Theme;
            setTheme(t);
            keepTheme(t);
          }}
        >
          <option value="dark">Dark</option>
          <option value="light">Light</option>
        </select>
      </FieldRow>
    </div>
  );
}

export function LoggingCard({ draft, hold }: { draft: SettingsDraft; hold: EnvHold }) {
  const { update } = draft;
  const log = draft.merged("logging");
  return (
    <div style={cardStyle}>
      <FieldRow label="Log level" htmlFor="cfg-log-level" held={heldSetting(hold, "logging", "level")}>
        <select
          id="cfg-log-level"
          style={selectStyle}
          value={log.level}
          onChange={(e) => update("logging", "level", e.target.value)}
        >
          <option value="debug">Debug</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
        </select>
      </FieldRow>
      <SwitchRow
        title="File logging"
        help="Write logs to disk in the data directory."
        checked={log.file_enabled}
        onChange={(v) => update("logging", "file_enabled", v)}
      />
      <FieldRow label="Max file size (MB)">
        <NumberBox value={log.max_size_mb} fallback={50} onChange={(n) => update("logging", "max_size_mb", n)} />
      </FieldRow>
      <FieldRow label="Max log files">
        <NumberBox value={log.max_files} fallback={5} onChange={(n) => update("logging", "max_files", n)} />
      </FieldRow>
    </div>
  );
}

/** `guidance` is the server's sentence for an install whose updates are
 *  managed elsewhere, shown in place of the settings; "" when they are this
 *  screen's, null until the server has said. */
export function UpdatesCard({ draft, hold, guidance }: { draft: SettingsDraft; hold: EnvHold; guidance: string | null }) {
  return (
    <div style={cardStyle}>
      {guidance !== "" ? (
        <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>{guidance ?? "Loading update settings..."}</div>
      ) : <UpdateSettings draft={draft} hold={hold} />}
    </div>
  );
}

function UpdateSettings({ draft, hold }: { draft: SettingsDraft; hold: EnvHold }) {
  const { update } = draft;
  const upd = draft.merged("updates");
  return <>
    <SwitchRow
      title="Check for updates"
      help="Periodically check GitHub for new releases."
      held={heldSetting(hold, "updates", "check_enabled")}
      checked={upd.check_enabled}
      onChange={(v) => update("updates", "check_enabled", v)}
    />
    <FieldRow label="Channel" held={heldSetting(hold, "updates", "channel")} help="Beta includes pre-release versions.">
      <select
        style={selectStyle}
        value={upd.channel}
        onChange={(e) => update("updates", "channel", e.target.value)}
      >
        <option value="stable">Stable</option>
        <option value="beta">Beta</option>
      </select>
    </FieldRow>
    <FieldRow label="Check interval (hours)" help="How often to check for new versions.">
      <NumberBox
        value={upd.auto_check_interval_hours}
        fallback={24}
        onChange={(n) => update("updates", "auto_check_interval_hours", n)}
      />
    </FieldRow>
    <SwitchRow
      title="Notify only"
      help="Say when an update is available, but never install one on its own. An update scheduled from the cloud is skipped too; you can still install one yourself."
      checked={upd.notify_only}
      onChange={(v) => update("updates", "notify_only", v)}
    />
  </>;
}

export function KioskCard({ draft }: { draft: SettingsDraft }) {
  const { update } = draft;
  const kiosk = draft.merged("kiosk");
  return (
    <div style={cardStyle}>
      <SwitchRow
        title="Kiosk mode"
        help="Launch the Panel UI fullscreen on an attached display (e.g. Raspberry Pi with HDMI touchscreen)."
        checked={kiosk.enabled}
        onChange={(v) => update("kiosk", "enabled", v)}
      />
      <FieldRow label="Target URL" help="The URL loaded in the kiosk browser. Defaults to the local Panel UI.">
        <input
          style={inputStyle}
          value={kiosk.target_url}
          onChange={(e) => update("kiosk", "target_url", e.target.value)}
        />
      </FieldRow>
      <SwitchRow
        title="Show cursor"
        help="Show the mouse cursor on the kiosk display. Disable for touch-only panels."
        checked={kiosk.cursor_visible}
        onChange={(v) => update("kiosk", "cursor_visible", v)}
      />
    </div>
  );
}
