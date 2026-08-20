/**
 * Decks reached over the network (Elgato Network Dock, Stream Deck Studio
 * Ethernet) are explicit opt-in: an entry in the plugin's network_decks config
 * array. This dialog finds them via the plugin's scan route (where multicast
 * discovery works) and always offers add-by-address.
 */
import { useState, useCallback, useEffect } from "react";
import { X } from "lucide-react";
import { Modal } from "../../shared/Modal";
import { BASE } from "../../../api/base";
import { networkEntriesOf, networkEntryKey } from "./deckHelpers";

interface NetworkScanResult {
  host: string;
  port: number;
  name: string;
  serial: string;
  kind: string;
  already_added: boolean;
}

export function NetworkDeckDialog({
  pluginId,
  config,
  onConfigChange,
  onClose,
}: {
  pluginId: string;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [found, setFound] = useState<NetworkScanResult[] | null>(null);
  const [browseAvailable, setBrowseAvailable] = useState(true);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("5343");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; error?: string } | null>(null);

  const scan = useCallback(async () => {
    setFound(null);
    try {
      const res = await fetch(`${BASE}/plugins/${pluginId}/ext/network/scan`, {
        method: "POST",
      });
      const data = await res.json();
      setBrowseAvailable(Boolean(data.browse_available));
      setFound(Array.isArray(data.found) ? data.found : []);
    } catch {
      setBrowseAvailable(false);
      setFound([]);
    }
  }, [pluginId]);

  useEffect(() => {
    void scan();
  }, [scan]);

  const addEntry = (h: string, p: number, sn?: string) => {
    const entries = networkEntriesOf(config);
    if (entries.some((e) => networkEntryKey(e) === `${h}:${p}`)) {
      onClose();
      return;
    }
    // The advertised serial (mdns_sn) lets the plugin follow this unit to a
    // new DHCP address even before it has connected once.
    const entry: Record<string, unknown> = { host: h, port: p };
    if (sn) entry.mdns_sn = sn;
    onConfigChange({
      ...config,
      network_decks: [...((config.network_decks as unknown[]) ?? []), entry],
    });
    onClose();
  };

  const portNum = Number(port) || 5343;
  const hostTrimmed = host.trim();

  const runTest = async () => {
    if (!hostTrimmed) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(`${BASE}/plugins/${pluginId}/ext/network/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: hostTrimmed, port: portNum }),
      });
      setTestResult(await res.json());
    } catch {
      setTestResult({ success: false, error: "test failed" });
    }
    setTesting(false);
  };

  const inputStyle: React.CSSProperties = {
    padding: "var(--space-xs) var(--space-sm)",
    borderRadius: "var(--border-radius)",
    border: "1px solid var(--border-color)",
    background: "var(--bg-surface)",
    color: "var(--text-primary)",
    fontSize: "var(--font-size-sm)",
  };

  return (
    <Modal
      onClose={onClose}
      label="Add a network deck"
      overlayStyle={{ background: "rgba(0,0,0,0.5)" }}
      panelStyle={{
        width: 440,
        maxHeight: "80vh",
        overflow: "auto",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-lg)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-md)",
      }}
    >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ fontWeight: "var(--font-weight-semibold)" }}>Add a network deck</div>
          <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
            <X size={16} />
          </button>
        </div>

        {found === null && (
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
            Looking for decks on this network…
          </div>
        )}
        {found !== null && found.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
            {found.map((f) => (
              <div
                key={`${f.host}:${f.port}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "var(--space-sm)",
                  padding: "var(--space-xs) var(--space-sm)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: "var(--font-size-sm)", fontWeight: "var(--font-weight-medium)" }}>{f.name}</div>
                  <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                    {f.kind} · {f.host}:{f.port}
                    {f.serial ? ` · ${f.serial}` : ""}
                  </div>
                </div>
                {f.already_added ? (
                  <span style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>Added</span>
                ) : (
                  <button
                    onClick={() => addEntry(f.host, f.port, f.serial)}
                    style={{
                      padding: "var(--space-2xs) var(--space-md)",
                      borderRadius: "var(--border-radius)",
                      background: "var(--accent-bg)",
                      color: "var(--text-on-accent)",
                      fontSize: "var(--font-size-sm)",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    Add
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        {found !== null && found.length === 0 && (
          <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", lineHeight: "var(--line-relaxed)" }}>
            {browseAvailable
              ? "No decks answered. Automatic discovery only sees decks on this network segment. Add one by address below."
              : "Automatic discovery isn't available from this server (it doesn't cross Docker bridge networks, NAT, or VLANs). Add the deck by its address."}
          </div>
        )}
        {found !== null && (
          <button
            onClick={() => void scan()}
            style={{
              alignSelf: "flex-start",
              fontSize: "var(--font-size-xs)",
              color: "var(--text-secondary)",
              background: "var(--bg-hover)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-2xs) var(--space-md)",
              cursor: "pointer",
            }}
          >
            Scan again
          </button>
        )}

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            color: "var(--text-muted)",
            fontSize: "var(--font-size-xs)",
          }}
        >
          <span style={{ flex: 1, borderTop: "1px solid var(--border-color)" }} />
          add by address
          <span style={{ flex: 1, borderTop: "1px solid var(--border-color)" }} />
        </div>

        <div style={{ display: "flex", gap: "var(--space-xs)" }}>
          <input
            value={host}
            onChange={(e) => {
              setHost(e.target.value);
              setTestResult(null);
            }}
            placeholder="192.168.1.40"
            style={{ ...inputStyle, flex: 1 }}
          />
          <input
            value={port}
            onChange={(e) => {
              setPort(e.target.value.replace(/[^0-9]/g, ""));
              setTestResult(null);
            }}
            title="Port (5343 unless changed on the device)"
            style={{ ...inputStyle, width: 64 }}
          />
          <button
            onClick={() => void runTest()}
            disabled={!hostTrimmed || testing}
            style={{
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              color: "var(--text-secondary)",
              fontSize: "var(--font-size-sm)",
              cursor: hostTrimmed && !testing ? "pointer" : "default",
              opacity: hostTrimmed && !testing ? 1 : 0.5,
            }}
          >
            {testing ? "Testing…" : "Test"}
          </button>
          <button
            onClick={() => addEntry(hostTrimmed, portNum)}
            disabled={!hostTrimmed}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              fontSize: "var(--font-size-sm)",
              fontWeight: "var(--font-weight-medium)",
              cursor: hostTrimmed ? "pointer" : "default",
              opacity: hostTrimmed ? 1 : 0.5,
            }}
          >
            Add deck
          </button>
        </div>
        {testResult && (
          <div
            style={{
              fontSize: "var(--font-size-xs)",
              color: testResult.success ? "var(--color-success)" : "var(--color-error)",
            }}
          >
            {testResult.success ? "Reachable, ready to add." : `Not reachable: ${testResult.error}`}
          </div>
        )}
        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", lineHeight: "var(--line-relaxed)" }}>
          The deck shows its address on its keys at power-up. For installed
          systems, set a static IP there so the address never changes.
        </div>
    </Modal>
  );
}
