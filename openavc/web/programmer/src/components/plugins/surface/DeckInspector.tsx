/**
 * The rail's idle state: the unit itself.
 *
 * What the inspector shows when no control is selected — the deck's identity
 * and connection, its name, whether it runs its own layout or the shared one,
 * and the actions that change that (give it its own, move a layout, forget it).
 */
import { useState } from "react";
import { CopyButton } from "../../shared/CopyButton";
import { pageMenuConfirmStyle, panelLabelStyle } from "./styles";

export function DeckInspector({
  serial,
  name,
  model,
  connected,
  isVirtual,
  deckCount,
  isOwn,
  sharedCount,
  brightness,
  fallbackBrightness,
  onRename,
  onBrightness,
  onIdentify,
  onGiveOwnLayout,
  onUseSharedLayout,
  moveTargets,
  onMoveLayoutTo,
  onRemoveVirtual,
  onForget,
  virtualModels,
  deviceLabel,
  onAddVirtual,
  hasTouchscreen = false,
  customZoneCount = 0,
  onOpenStrip,
  transport = "",
  address = "",
  networkStatus = "",
  onRemoveNetwork,
  onAddNetwork,
}: {
  serial: string;
  name: string;
  model: string;
  connected: boolean;
  isVirtual: boolean;
  deckCount: number;
  isOwn: boolean;
  sharedCount: number;
  brightness?: number;
  fallbackBrightness: number;
  onRename: (name: string) => void;
  onBrightness: (level: number | undefined) => void;
  onIdentify?: () => void;
  onGiveOwnLayout?: () => void;
  onUseSharedLayout?: () => void;
  moveTargets: { serial: string; label: string; hasOwn: boolean }[];
  onMoveLayoutTo: (serial: string) => void;
  onRemoveVirtual?: () => void;
  onForget?: () => void;
  virtualModels: string[];
  deviceLabel: string;
  onAddVirtual: (model: string) => void;
  // Touch strip summary row (decks that have one).
  hasTouchscreen?: boolean;
  customZoneCount?: number;
  onOpenStrip?: () => void;
  // Network decks (transport "network"): address line + entry removal.
  transport?: string;
  address?: string;
  networkStatus?: string;
  onRemoveNetwork?: () => void;
  onAddNetwork?: () => void;
}) {
  const [confirmShared, setConfirmShared] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmForget, setConfirmForget] = useState(false);
  const [confirmNetRemove, setConfirmNetRemove] = useState(false);
  const [moveTarget, setMoveTarget] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addModel, setAddModel] = useState(virtualModels[0] ?? "");
  const level = typeof brightness === "number" ? brightness : fallbackBrightness;

  return (
    <div
      style={{
        width: 300,
        flexShrink: 0,
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-lg)",
        maxHeight: "100%",
        overflow: "auto",
      }}
    >
      {/* Identity */}
      <div>
        <input
          value={name}
          placeholder="Name this deck"
          title="Friendly name shown everywhere (e.g. Lectern, Tech Booth)"
          onChange={(e) => onRename(e.target.value)}
          style={{
            width: "100%",
            padding: "var(--space-xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
            color: "var(--text-primary)",
            fontSize: "var(--font-size-base)",
            fontWeight: 600,
          }}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            marginTop: "var(--space-xs)",
            fontSize: 11,
            color: "var(--text-muted)",
            flexWrap: "wrap",
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: connected ? "var(--color-success)" : "var(--text-muted)",
            }}
          />
          {connected ? "Connected" : "Not connected"}
          {model && <> · {model}</>}
          {isVirtual && <> · virtual</>}
          {transport === "network" && <> · network</>}
          {transport === "network" && !connected && networkStatus &&
            networkStatus !== "removed" && <> · {networkStatus}</>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
          <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
            {serial}
          </code>
          <CopyButton value={serial} title="Copy serial" />
        </div>
        {transport === "network" && address && (
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
            <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {address}
            </code>
            <CopyButton value={address} title="Copy address" />
          </div>
        )}
      </div>

      {/* Touch strip — what it's showing, and the way into the zone editor */}
      {hasTouchscreen && onOpenStrip && (
        <div>
          <label style={panelLabelStyle}>Touch Strip</label>
          <div
            style={{
              display: "flex", alignItems: "center",
              justifyContent: "space-between", gap: "var(--space-sm)",
            }}
          >
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {customZoneCount > 0
                ? `${customZoneCount} custom zone${customZoneCount === 1 ? "" : "s"}`
                : "One readout per dial"}
            </span>
            <button
              onClick={onOpenStrip}
              style={{
                padding: "2px 10px", borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)", color: "var(--text-secondary)",
                fontSize: 11, cursor: "pointer",
              }}
            >
              Customize…
            </button>
          </div>
        </div>
      )}

      {/* Brightness — a property of this unit, not of any layout */}
      {connected && (
        <div>
          <label style={panelLabelStyle}>Brightness</label>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <input
              type="range"
              min={0}
              max={100}
              value={level}
              onChange={(e) => onBrightness(Number(e.target.value))}
              style={{ flex: 1, accentColor: "var(--accent)" }}
            />
            <span style={{ fontSize: "var(--font-size-sm)", width: 38, textAlign: "right" }}>
              {level}%
            </span>
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
            Just this deck.{" "}
            {typeof brightness === "number" && (
              <button
                onClick={() => onBrightness(undefined)}
                style={{ color: "var(--accent)", cursor: "pointer", background: "none", fontSize: 10 }}
              >
                Use the shared level ({fallbackBrightness}%)
              </button>
            )}
          </div>
        </div>
      )}

      {/* Actions on the unit */}
      {onIdentify && (
        <button onClick={onIdentify} style={deckActionBtnStyle} title="Flash this deck's keys so you can tell which one it is">
          Identify — flash this deck
        </button>
      )}

      {/* Layout ownership (only meaningful with more than one known deck) */}
      {deckCount > 1 && (
        <div>
          <label style={panelLabelStyle}>Layout</label>
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: "var(--space-sm)" }}>
            {isOwn ? (
              <>Shows <strong>its own layout</strong>.</>
            ) : (
              <>Shows the <strong>shared layout</strong>{sharedCount > 1 ? ` (with ${sharedCount - 1} other deck${sharedCount > 2 ? "s" : ""})` : ""}.</>
            )}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
            {onGiveOwnLayout && (
              <button
                onClick={onGiveOwnLayout}
                style={deckActionBtnStyle}
                title="Starts as a copy of the shared layout; other decks keep sharing"
              >
                Give this deck its own layout
              </button>
            )}
            {onUseSharedLayout && !confirmShared && (
              <button onClick={() => setConfirmShared(true)} style={deckActionBtnStyle}>
                Use the shared layout instead
              </button>
            )}
            {onUseSharedLayout && confirmShared && (
              <InlineConfirm
                question={`Delete ${name || model || "this deck"}'s own layout and show the shared one? This can't be undone.`}
                onYes={() => {
                  onUseSharedLayout();
                  setConfirmShared(false);
                }}
                onNo={() => setConfirmShared(false)}
              />
            )}
            {isOwn && moveTargets.length > 0 && moveTarget === null && (
              <button
                onClick={() => setMoveTarget(moveTargets[0].serial)}
                style={deckActionBtnStyle}
                title="Re-key this layout (and the deck's name) onto another deck — e.g. a replacement unit"
              >
                Move this layout to another deck...
              </button>
            )}
            {moveTarget !== null && (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
                <select
                  value={moveTarget}
                  onChange={(e) => setMoveTarget(e.target.value)}
                  style={{
                    padding: "4px 6px",
                    borderRadius: "var(--border-radius)",
                    border: "1px solid var(--border-color)",
                    background: "var(--bg-surface)",
                    color: "var(--text-primary)",
                    fontSize: 12,
                  }}
                >
                  {moveTargets.map((t) => (
                    <option key={t.serial} value={t.serial}>
                      {t.label} ({t.serial}){t.hasOwn ? " — replaces its own layout" : ""}
                    </option>
                  ))}
                </select>
                <div style={{ display: "flex", gap: "var(--space-xs)" }}>
                  <button
                    onClick={() => {
                      if (moveTarget) onMoveLayoutTo(moveTarget);
                      setMoveTarget(null);
                    }}
                    style={{ ...pageMenuConfirmStyle, background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
                  >
                    Move
                  </button>
                  <button onClick={() => setMoveTarget(null)} style={pageMenuConfirmStyle}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Virtual / network / remembered-unit upkeep */}
      {(onRemoveVirtual || onForget || onRemoveNetwork) && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          {onRemoveNetwork && !confirmNetRemove && (
            <button
              onClick={() => setConfirmNetRemove(true)}
              style={{ ...deckActionBtnStyle, color: "var(--color-error)" }}
              title="Stop connecting to this deck over the network. A layout of its own is kept and can be moved to another deck."
            >
              Remove network deck
            </button>
          )}
          {onRemoveNetwork && confirmNetRemove && (
            <InlineConfirm
              question={`Stop connecting to ${name || address || serial}?`}
              onYes={() => {
                onRemoveNetwork();
                setConfirmNetRemove(false);
              }}
              onNo={() => setConfirmNetRemove(false)}
            />
          )}
          {onRemoveVirtual && !confirmRemove && (
            <button
              onClick={() => setConfirmRemove(true)}
              style={{ ...deckActionBtnStyle, color: "var(--color-error)" }}
              title="Remove this virtual deck. A layout of its own is kept and can be moved to another deck."
            >
              Remove virtual deck
            </button>
          )}
          {onRemoveVirtual && confirmRemove && (
            <InlineConfirm
              question="Remove this virtual deck?"
              onYes={() => {
                onRemoveVirtual();
                setConfirmRemove(false);
              }}
              onNo={() => setConfirmRemove(false)}
            />
          )}
          {onForget && !confirmForget && (
            <button
              onClick={() => setConfirmForget(true)}
              style={{ ...deckActionBtnStyle, color: "var(--color-error)" }}
              title="Drop this deck's name, settings, and saved layout"
            >
              Forget this deck
            </button>
          )}
          {onForget && confirmForget && (
            <InlineConfirm
              question={`Forget ${name || serial}? Its saved layout is deleted.`}
              onYes={() => {
                onForget();
                setConfirmForget(false);
              }}
              onNo={() => setConfirmForget(false)}
            />
          )}
        </div>
      )}

      {/* Add a virtual or network unit */}
      {(virtualModels.length > 0 || onAddNetwork) && (
        <div
          style={{
            marginTop: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-xs)",
          }}
        >
          {onAddNetwork && (
            <button
              onClick={onAddNetwork}
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                border: "1px dashed var(--border-color)",
                background: "transparent",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
                width: "100%",
              }}
              title="Add a deck reached over the network (Network Dock or built-in Ethernet)"
            >
              + Network {deviceLabel}
            </button>
          )}
          {virtualModels.length > 0 && (!adding ? (
            <button
              onClick={() => setAdding(true)}
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                border: "1px dashed var(--border-color)",
                background: "transparent",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
                width: "100%",
              }}
              title="Add a software unit that runs exactly like plugged-in hardware"
            >
              + Virtual {deviceLabel}
            </button>
          ) : (
            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <select
                value={addModel}
                onChange={(e) => setAddModel(e.target.value)}
                style={{
                  flex: 1,
                  padding: "4px 6px",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                  background: "var(--bg-surface)",
                  color: "var(--text-primary)",
                  fontSize: 12,
                }}
              >
                {virtualModels.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
              <button
                onClick={() => {
                  if (addModel) onAddVirtual(addModel);
                  setAdding(false);
                }}
                style={{ ...pageMenuConfirmStyle, background: "var(--accent-bg)", color: "var(--text-on-accent)" }}
              >
                Add
              </button>
              <button onClick={() => setAdding(false)} style={pageMenuConfirmStyle}>
                Cancel
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function InlineConfirm({
  question,
  onYes,
  onNo,
}: {
  question: string;
  onYes: () => void;
  onNo: () => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", fontSize: 12 }}>
      <span style={{ color: "var(--color-error, #ef4444)" }}>{question}</span>
      <div style={{ display: "flex", gap: "var(--space-xs)" }}>
        <button onClick={onYes} style={pageMenuConfirmStyle}>Yes</button>
        <button onClick={onNo} style={pageMenuConfirmStyle}>No</button>
      </div>
    </div>
  );
}

const deckActionBtnStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-sm)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
  textAlign: "left",
};
