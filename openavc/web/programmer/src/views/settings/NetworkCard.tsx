/** Settings > Network: who can connect, the adapter devices are reached
 *  through, the HTTP port, short URLs, and whether the Panel app's list
 *  announces this system. Who can connect is the listen choice's
 *  (listenChoice.ts); this card draws it, and what announcing means for it. */
import type { ListenMode } from "../../api/restClient";
import { heldSetting, type EnvHold } from "../../components/system/EnvHeldNote";
import { AcceptConnectionsField, ReachDevicesField } from "../../components/system/NetworkChoiceFields";
import { adapterName } from "../../components/system/adapterChoice";
import { cardStyle, fieldRow } from "../../components/system/settingsStyles";
import { FieldRow, NumberBox, SwitchRow } from "./settingsControls";
import type { ListenChoice } from "./listenChoice";
import type { SettingsDraft } from "./settingsDraft";
import type { Adapters } from "./serverReads";

/** What Advertise on the network does for the choice of who can connect. */
export function advertiseHelp(mode: ListenMode, adapterName: string): string {
  if (mode === "this_computer") return "Nothing on the network can connect, so there is nothing to announce.";
  if (mode === "adapter") {
    return `Lets the OpenAVC Panel app on the ${adapterName || "chosen"} network find this system in its list. Turning this off hides the system from the list; devices can still connect by address.`;
  }
  return "Lets the OpenAVC Panel app find this system in its list. Turning this off hides the system from the list; devices can still connect by address.";
}

export function NetworkCard({ draft, listen, adapters, hold }: {
  draft: SettingsDraft;
  listen: ListenChoice;
  adapters: Adapters;
  hold: EnvHold;
}) {
  const { update } = draft;
  const net = draft.merged("network");
  return (
    <div style={cardStyle}>
      <div style={fieldRow}>
        <AcceptField draft={draft} listen={listen} adapters={adapters} hold={hold} />
      </div>
      <div style={fieldRow}>
        <ReachDevicesField
          interfaces={adapters.interfaces}
          chosen={net.reach_devices_through ?? {}}
          resolved={draft.config?._reach_devices}
          hold={hold}
          loading={adapters.loading}
          onChange={(identity) => update("network", "reach_devices_through", identity)}
          onRefresh={adapters.reload}
        />
      </div>
      <FieldRow label="HTTP port" htmlFor="cfg-http-port" held={heldSetting(hold, "network", "http_port")}>
        <NumberBox
          id="cfg-http-port"
          value={net.http_port}
          fallback={8080}
          onChange={(n) => update("network", "http_port", n)}
        />
      </FieldRow>
      <SwitchRow
        title="Short URLs (port 80 redirect)"
        help={<>
          Lets people type addresses without a port: <code>http://&lt;server&gt;/panel</code> forwards
          to the real port automatically. Needs port 80 free on this machine. On Linux the
          OpenAVC service also needs permission to bind it (fresh installs have this; see
          the deployment guide).
        </>}
        held={heldSetting(hold, "network", "port80_redirect")}
        checked={!!net.port80_redirect}
        onChange={(v) => update("network", "port80_redirect", v)}
      />
      <AdvertiseRow draft={draft} listen={listen} interfaces={adapters.interfaces} hold={hold} />
    </div>
  );
}

/** Who can connect, with where panels will open for the choice on screen. */
function AcceptField({ draft, listen, adapters, hold }: {
  draft: SettingsDraft;
  listen: ListenChoice;
  adapters: Adapters;
  hold: EnvHold;
}) {
  const net = draft.merged("network");
  const tls = draft.merged("tls");
  return (
    <AcceptConnectionsField
      interfaces={adapters.interfaces}
      mode={listen.mode}
      adapter={listen.adapter}
      runningName={listen.running?.adapter_name ?? ""}
      problem={listen.needsRestart ? null : (listen.running?.problem ?? null)}
      preview={listen.preview}
      changed={listen.changed}
      hold={hold}
      urlFacts={{
        httpPort: net.http_port,
        // Settings cannot know whether port 80 is answering, so it always
        // prints the port.
        port80: false,
        tlsEnabled: !!tls?.enabled,
        tlsPort: tls?.port ?? 8443,
        redirectHttp: tls?.redirect_http !== false,
      }}
      onChange={(mode, adapter) => {
        draft.update("network", "accept_connections_from", mode);
        draft.update("network", "accept_connections_adapter", adapter);
      }}
    />
  );
}

/** With this computer only, it is shown off and left as saved, so choosing a
 *  network again brings back what was set. An absent discovery section
 *  advertises, as the server reads it. */
function AdvertiseRow({ draft, listen, interfaces, hold }: {
  draft: SettingsDraft;
  listen: ListenChoice;
  interfaces: Adapters["interfaces"];
  hold: EnvHold;
}) {
  const thisComputer = listen.mode === "this_computer";
  const held = heldSetting(hold, "discovery", "advertise");
  const name = adapterName(listen.adapter, interfaces, listen.running?.adapter_name ?? "");
  return (
    <SwitchRow
      title="Advertise on the network"
      help={advertiseHelp(listen.mode, name)}
      helpTestId="advertise-help"
      label="Advertise on the network"
      held={thisComputer ? { ...held, locked: "this_computer" } : held}
      checked={thisComputer ? false : draft.merged("discovery")?.advertise !== false}
      onChange={(v) => draft.update("discovery", "advertise", v)}
    />
  );
}
