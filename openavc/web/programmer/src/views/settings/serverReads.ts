/** What Settings reads from the server when it opens, beside the config
 *  (settingsDraft.ts) and SSH (AccessCards.tsx): what this install offers,
 *  the network adapters, and the certificate HTTPS is running. A read that
 *  fails leaves the page usable: no kiosk or panel screen, no adapters, no
 *  certificate shown; only the update settings say they could not be read. */
import { useCallback, useEffect, useState } from "react";
import * as api from "../../api/restClient";
import type { NetworkAdapter, NetworkInterface, TlsStatus } from "../../api/restClient";

/** What this install offers Settings, from the version read: a kiosk, a
 *  panel screen it can dim, and, where something else installs updates, the
 *  server's sentence that says so ("" when the update settings are this
 *  screen's; null until it answers). */
export function useInstallFacts() {
  const [kioskAvailable, setKioskAvailable] = useState(false);
  const [panelDimAvailable, setPanelDimAvailable] = useState(false);
  const [updateGuidance, setUpdateGuidance] = useState<string | null>(null);

  useEffect(() => {
    api.getSystemVersion().then((v) => {
      setKioskAvailable(v.kiosk_available);
      setPanelDimAvailable(v.panel_dim_available);
      setUpdateGuidance(v.update_guidance ?? "");
    }).catch(() => setUpdateGuidance("Update settings could not be loaded. Reload this page to try again."));
  }, []);

  return { kioskAvailable, panelDimAvailable, updateGuidance };
}

/** The adapters: those with an address (the trusted certificate's choices)
 *  and one entry per adapter, connected or not (what the two adapter pickers
 *  list). Read again on Refresh; a failed read keeps what was shown. */
export function useAdapters() {
  const [adapters, setAdapters] = useState<NetworkAdapter[]>([]);
  const [interfaces, setInterfaces] = useState<NetworkInterface[]>([]);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    api.getNetworkAdapters()
      .then((r) => {
        setAdapters(r.adapters);
        setInterfaces(r.interfaces ?? []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return { adapters, interfaces, loading, reload };
}

export type Adapters = ReturnType<typeof useAdapters>;

/** The certificate HTTPS is running, read again after a save and by the
 *  trusted certificate; null when the read fails. */
export function useCertificateStatus() {
  const [status, setStatus] = useState<TlsStatus | null>(null);

  const load = useCallback(() => {
    api.getTlsStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { status, setStatus, load };
}
