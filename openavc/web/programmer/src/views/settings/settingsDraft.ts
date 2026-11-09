/** The Settings page's edit buffer: the config the server sent, the changes
 *  not saved yet (by section and key, so a save sends only what changed), the
 *  two merged as the screen shows them, and whether a change made since
 *  Settings opened needs a restart to take effect.
 *
 *  Nothing clears that restart flag but leaving Settings: it is still owed
 *  after the save, until the restart hands the page over or Settings is
 *  opened again. */
import { useCallback, useEffect, useState } from "react";
import * as api from "../../api/restClient";
import type { SystemConfig } from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import { showError } from "../../store/toastStore";

/** Change one setting on screen, by section and key. */
export type UpdateSetting = <S extends keyof SystemConfig>(
  section: S,
  key: keyof SystemConfig[S],
  value: SystemConfig[S][keyof SystemConfig[S]],
) => void;

/** A changed setting takes effect only at a restart: the HTTP port, Short
 *  URLs, and every HTTPS setting (the server reads its certificate and ports
 *  only at startup). Who can connect needs one too, tracked against what the
 *  server runs (listenChoice.ts), so choosing the running choice again clears
 *  it. Reach devices through applies with no restart. */
export function changeNeedsRestart(section: string, key: string): boolean {
  if (section === "tls") return true;
  return section === "network" && (key === "http_port" || key === "port80_redirect");
}

export function useSettingsDraft() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [dirty, setDirty] = useState<Partial<SystemConfig>>({});
  const [restartNeeded, setRestartNeeded] = useState(false);

  useEffect(() => {
    api.getSystemConfig()
      .then(setConfig)
      .catch((e) => showError("Failed to load config: " + parseApiError(e)));
  }, []);

  const update: UpdateSetting = useCallback((section, key, value) => {
    setDirty((prev) => ({
      ...prev,
      [section]: { ...(prev[section] as Record<string, unknown> ?? {}), [key]: value },
    }));
    if (changeNeedsRestart(section, String(key))) setRestartNeeded(true);
  }, []);

  /** A section as on screen: saved, with any unsaved change over it. */
  const merged = useCallback(
    <S extends keyof SystemConfig>(section: S): SystemConfig[S] => {
      if (!config) return {} as SystemConfig[S];
      return { ...config[section], ...(dirty[section] as Partial<SystemConfig[S]> ?? {}) };
    },
    [config, dirty],
  );

  return {
    config,
    /** Replace the saved config (a card that changed it on the server). */
    setConfig,
    dirty,
    update,
    merged,
    restartNeeded,
    /** A save went through: the fresh config, with nothing left unsaved. */
    saved: (fresh: SystemConfig) => {
      setConfig(fresh);
      setDirty({});
    },
  };
}
