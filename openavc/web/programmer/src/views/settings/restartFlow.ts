/** The restart Settings asks for: "Restart to apply changes?", and the dialog
 *  that restarts the server and waits for it, with where it waits.
 *
 *  Where it waits is worked out from the SAVED config, because Restart is only
 *  offered with nothing unsaved: the server's own listener when this page
 *  reached the server directly when Settings opened, else this page's origin
 *  (a proxy in between, `restartOrigin`). A saved change of who can connect
 *  that the server is not running yet can move this page, on the same scheme
 *  and port: to 127.0.0.1 for a browser on this computer at an address the
 *  choice leaves out, or, for a browser it cuts off, to the new choice's own
 *  addresses (`restartPlan`). Once a restart has handed the page over, the
 *  server it would ask can no longer be reached from here. */
import { useEffect, useRef, useState } from "react";
import * as api from "../../api/restClient";
import type { ListenPreview, SystemConfig } from "../../api/restClient";
import { restartPlan, type RestartPlan } from "../../components/system/adapterChoice";
import { configuredOrigin, restartOrigin, runningOrigin, withHost } from "../../components/shared/restartPollHelpers";
import { listenBody } from "./listenChoice";

/** Where the restart dialog waits, worked out when it opens. */
export interface RestartSetup {
  plan: RestartPlan;
  /** The server's answer for the saved choice, when one was waiting. */
  preview: ListenPreview | null;
}

/** The addresses the restart dialog waits at, from where this page is, where
 *  it reached the server when Settings opened, and the saved config. */
export function restartTargets(
  page: { origin: string; protocol: string },
  openedAt: string | null,
  config: SystemConfig | null,
  plan: RestartPlan,
) {
  const origin = restartOrigin(page.origin, openedAt, configuredOrigin(config, page.origin));
  const targetScheme = new URL(origin).protocol === "https:" ? "https" : "http";
  const currentScheme = page.protocol === "https:" ? "https" : "http";
  const urlFor = (host: string) => new URL("/programmer", host ? withHost(origin, host) : origin).toString();
  const urls = plan.hosts.map(urlFor);
  const localUrl = new URL("/programmer", withHost(origin, "localhost")).toString();
  return {
    /** The Programmer at each address the plan names, best first. */
    urls,
    /** The Programmer on this computer's own screen. */
    localUrl,
    targetUrl: plan.cutOff ? (urls[0] ?? localUrl) : urls[0],
    alsoTry: plan.cutOff ? urls : urls.slice(1),
    isProtocolSwitch: currentScheme !== targetScheme,
    expectsNewCert: currentScheme === "http" && targetScheme === "https",
  };
}

/** The restart prompt and the restart dialog, and what the dialog needs. */
export function useRestart(config: SystemConfig | null) {
  // Where this page reached the server when Settings opened, from the first
  // config read; it decides whether the page reached the server directly.
  const openedAt = useRef<string | null>(null);
  useEffect(() => {
    if (config) openedAt.current ??= runningOrigin(config, window.location.origin);
  }, [config]);
  const [asking, setAsking] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [setup, setSetup] = useState<RestartSetup | null>(null);

  /** Open the restart dialog, having worked out where it waits. */
  const start = async (cfg: SystemConfig | null = config) => {
    let preview: ListenPreview | null = null;
    if (cfg?._listen?.restart_needed) {
      const n = cfg.network;
      try {
        preview = await api.previewListen(listenBody(n.accept_connections_from, n.accept_connections_adapter ?? {}));
      } catch {
        preview = null; // the dialog then waits where it always has
      }
    }
    setSetup({ plan: restartPlan(preview), preview });
    setDialogOpen(true);
  };

  return {
    /** "Restart to apply changes?" is open. */
    asking,
    ask: () => setAsking(true),
    stopAsking: () => setAsking(false),
    dialogOpen,
    start,
    // A manual close means the person is bailing out (the success path
    // navigates away first), so whatever asked for a restart stays up.
    closeDialog: () => setDialogOpen(false),
    setup,
    /** A restart has handed this page over to another address. */
    handedOver: !!setup?.plan.cutOff,
    /** Where the open dialog waits, from the plan worked out when it opened. */
    targets: () => restartTargets(
      window.location, openedAt.current, config, setup?.plan ?? { cutOff: false, hosts: [""] },
    ),
  };
}

export type Restart = ReturnType<typeof useRestart>;
