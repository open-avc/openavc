/** Settings > Network > Accept connections from: the choice on screen against
 *  the one saved and the one the server runs, the server's answer for it (the
 *  preview), and what the "Change who can connect?" question and the restart
 *  dialog say about it.
 *
 *  Each comparison means something different. Against the saved choice: the
 *  choice has changed, and once one of its fields was edited it is being
 *  saved, so Save asks the server first whether it cuts this browser off.
 *  Against the running choice: it needs a restart, so choosing the running
 *  one again stops asking for one. The server reads an adapter by identity,
 *  so two choices are compared by `sameListenChoice`, never as objects. */
import { useEffect, useState } from "react";
import * as api from "../../api/restClient";
import type { AdapterIdentity, ListenBlock, ListenMode, ListenPreview, SystemConfig } from "../../api/restClient";
import type { CutOffFacts } from "../../components/system/ListenCutOff";
import { ownScreenOpensProgrammer, sameListenChoice } from "../../components/system/adapterChoice";

type Network = SystemConfig["network"];

/** A choice of who can connect, as the preview and PATCH take it. */
export function listenBody(mode: ListenMode, adapter: AdapterIdentity) {
  return mode === "adapter"
    ? { accept_connections_from: mode, accept_connections_adapter: adapter }
    : { accept_connections_from: mode };
}

/** The choice on screen: the saved one, or changed and not saved. An empty
 *  choice reads as this computer only, and only a chosen adapter carries one. */
export function shownListenChoice(
  saved: Network | undefined,
  unsaved: Partial<Network> | undefined,
): { mode: ListenMode; adapter: AdapterIdentity } {
  const shown = saved ? { ...saved, ...(unsaved ?? {}) } : null;
  const mode = (shown?.accept_connections_from || "this_computer") as ListenMode;
  return { mode, adapter: mode === "adapter" ? (shown?.accept_connections_adapter ?? {}) : {} };
}

/** What the server is running, standing in for its answer while that has not
 *  come or did not: only while the saved choice is on screen, and only with
 *  addresses to show. An environment-held address is not something the
 *  preview takes, so its panel addresses come from here. */
export function runningAsPreview(running: ListenBlock | undefined, changed: boolean): ListenPreview | null {
  if (changed || !running) return null;
  if (running.network_addresses.length === 0) return null;
  return {
    mode: running.mode, adapter_name: running.adapter_name, label: running.label,
    panel_addresses: running.network_addresses, connected_through: "",
    serves_this_address: true, keeps_this_connection: true, safety_net: false,
    problem: null, consequences: [], held_by: running.held_by,
    cloud_remote_access: false,
  };
}

/** What the cut-off question and the restart dialog say, from the server's
 *  answer for the change. */
export function cutOffFacts(
  p: ListenPreview,
  where: { programmerUrls: string[]; localUrl: string },
  context: { pageHost: string; deploymentType: string; runningLabel: string | undefined },
): CutOffFacts {
  return {
    connectedThrough: p.connected_through || context.pageHost,
    mode: p.mode,
    adapterName: p.adapter_name,
    programmerUrls: p.mode === "adapter" ? where.programmerUrls : [],
    localUrl: where.localUrl,
    ownScreenOpensProgrammer: ownScreenOpensProgrammer(context.deploymentType),
    // The safety net goes back to the choice the server is running.
    previousLabel: context.runningLabel ?? "the previous choice",
    minutes: 10,
    cloudPaired: p.cloud_remote_access,
    consequences: p.consequences.map((c) => c.message),
  };
}

/** A field of the choice was edited and not saved. */
function choiceEdited(unsaved: Partial<Network> | undefined): boolean {
  return !!unsaved && ("accept_connections_from" in unsaved || "accept_connections_adapter" in unsaved);
}

/** Where the choice on screen stands against the saved one and the running one. */
export function listenStatus(
  choice: { mode: ListenMode; adapter: AdapterIdentity },
  config: SystemConfig | null,
  unsaved: Partial<Network> | undefined,
) {
  const running = config?._listen;
  const changed = !!config && !sameListenChoice(
    choice,
    { mode: config.network.accept_connections_from, adapter: config.network.accept_connections_adapter },
  );
  return {
    changed,
    /** A change the screen shows and the server does not have saved yet. */
    saving: changed && choiceEdited(unsaved),
    /** Not what the server runs, so it takes effect at a restart. */
    needsRestart: !!running && !sameListenChoice(choice, { mode: running.mode, adapter: running.adapter }),
  };
}

/** The server's answer for the choice on screen, asked 150 ms after the
 *  choice settles and again whenever the config is read (a save may have
 *  changed the adapters); null while none has come, or when asking failed. */
function useServerAnswer(config: SystemConfig | null, mode: ListenMode, adapter: AdapterIdentity) {
  const [answer, setAnswer] = useState<ListenPreview | null>(null);
  const choiceKey = JSON.stringify([mode, adapter]);
  useEffect(() => {
    if (!config) return;
    let stale = false;
    const timer = window.setTimeout(() => {
      api.previewListen(listenBody(mode, adapter))
        .then((p) => { if (!stale) setAnswer(p); })
        .catch(() => { if (!stale) setAnswer(null); });
    }, 150);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
    // choiceKey stands for mode and adapter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [choiceKey, config]);
  return answer;
}

/** The choice on screen, where it stands, and the server's answer for it. */
export function useListenChoice(config: SystemConfig | null, unsaved: Partial<Network> | undefined, deploymentType: string) {
  const choice = shownListenChoice(config?.network, unsaved);
  const answer = useServerAnswer(config, choice.mode, choice.adapter);
  const status = listenStatus(choice, config, unsaved);
  const running = config?._listen;
  return {
    ...choice,
    ...status,
    running,
    /** Something on the network can connect. */
    reachesNetwork: choice.mode !== "this_computer",
    preview: answer ?? runningAsPreview(running, status.changed),
    /** The server's answer for this choice, asked now (Save's question). */
    ask: () => api.previewListen(listenBody(choice.mode, choice.adapter)),
    facts: (p: ListenPreview, programmerUrls: string[], localUrl: string) =>
      cutOffFacts(p, { programmerUrls, localUrl }, {
        pageHost: window.location.hostname, deploymentType, runningLabel: running?.label,
      }),
  };
}

export type ListenChoice = ReturnType<typeof useListenChoice>;
