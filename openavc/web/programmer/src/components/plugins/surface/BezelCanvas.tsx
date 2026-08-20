/**
 * The live picture of the deck.
 *
 * Drawn as the physical object: keys, side touch keys, info screen, touch
 * strip, and dials in hardware order inside a dark shell, with the deck's
 * identity etched on the lower edge. Cells show the deck's real rendering
 * (live mirror) and fall back to a schematic before it populates. Click =
 * edit, Shift+click (or the hover ▶) = press.
 */
import { useState } from "react";
import { Pin, Play } from "lucide-react";
import { ElementIcon } from "../../ui-builder/ElementIcon";
import type { ButtonAssignment, DialAssignment, WorkbenchSelection } from "./types";

const CANVAS_KEY_PX = 72;

const CANVAS_GAP = 6;

export function BezelCanvas({
  name,
  model,
  connected,
  isVirtual,
  rows,
  columns,
  keyCount,
  touchKeyCount,
  dialCount,
  hasTouchscreen,
  hasInfoScreen,
  images,
  liveImagesValid,
  touchKeyColors,
  selection,
  lockedIndexes,
  inputFlash,
  currentPage,
  getAssignment,
  getDial,
  customZoneCount,
  zoneBounds,
  onSelect,
  onSimulate,
}: {
  name: string;
  model: string;
  connected: boolean;
  isVirtual: boolean;
  rows: number;
  columns: number;
  keyCount: number;
  touchKeyCount: number;
  dialCount: number;
  hasTouchscreen: boolean;
  hasInfoScreen: boolean;
  images: Record<string, string>;
  liveImagesValid: boolean;
  touchKeyColors: string[];
  selection: WorkbenchSelection;
  lockedIndexes: Set<number | undefined>;
  inputFlash: { kind: string; index: number } | null;
  currentPage: number;
  getAssignment: (index: number, page?: number) => ButtonAssignment | undefined;
  getDial: (index: number) => DialAssignment | undefined;
  customZoneCount: number;
  zoneBounds: { x: number; w: number }[];
  onSelect: (sel: WorkbenchSelection) => void;
  onSimulate?: (payload: Record<string, unknown>) => void;
}) {
  const gridWidth = columns * CANVAS_KEY_PX + (columns - 1) * CANVAS_GAP;
  const [stripHover, setStripHover] = useState(false);

  return (
    <div
      style={{
        display: "inline-flex",
        flexDirection: "column",
        gap: CANVAS_GAP,
        padding: "var(--space-lg)",
        background: "#0c0c14",
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--border-color)",
      }}
    >
      {/* LCD keys */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${columns}, ${CANVAS_KEY_PX}px)`,
          gridTemplateRows: `repeat(${rows}, ${CANVAS_KEY_PX}px)`,
          gap: CANVAS_GAP,
        }}
      >
        {Array.from({ length: keyCount }, (_, i) => (
          <KeyCell
            key={i}
            index={i}
            image={liveImagesValid ? images[`key_${i}`] : undefined}
            assignment={getAssignment(i, currentPage)}
            selected={selection.kind === "key" && selection.index === i}
            locked={lockedIndexes.has(i)}
            flashing={inputFlash?.kind === "key" && inputFlash.index === i}
            onSelect={() => onSelect({ kind: "key", index: i })}
            onPress={onSimulate ? () => onSimulate({ type: "key", index: i }) : undefined}
          />
        ))}
      </div>

      {/* Info screen flanked by the side touch keys (color-only) */}
      {(hasInfoScreen || touchKeyCount > 0) && (
        <div style={{ display: "flex", alignItems: "center", gap: CANVAS_GAP }}>
          {touchKeyCount > 0 && (
            <CanvasTouchKey
              color={touchKeyColors[0] || (getAssignment(keyCount, currentPage)?.bg_color ?? "")}
              selected={selection.kind === "key" && selection.index === keyCount}
              locked={lockedIndexes.has(keyCount)}
              flashing={inputFlash?.kind === "key" && inputFlash.index === keyCount}
              onSelect={() => onSelect({ kind: "key", index: keyCount })}
              onPress={onSimulate ? () => onSimulate({ type: "key", index: keyCount }) : undefined}
            />
          )}
          {hasInfoScreen && (
            <button
              onClick={() => onSelect({ kind: "screen" })}
              title="Info screen: click to set what it shows"
              style={{
                flex: 1,
                height: Math.max(34, Math.round((gridWidth * 0.55 * 58) / 248)),
                borderRadius: "var(--border-radius)",
                overflow: "hidden",
                background: "#000",
                border:
                  selection.kind === "screen"
                    ? "2px solid var(--accent)"
                    : "1px solid #2a2a3a",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
                color: "#445",
                fontSize: "var(--font-size-2xs)",
              }}
            >
              {liveImagesValid && images["screen"] ? (
                <img
                  src={images["screen"]}
                  draggable={false}
                  style={{ height: "100%", display: "block" }}
                  alt=""
                />
              ) : (
                "info screen"
              )}
            </button>
          )}
          {touchKeyCount > 1 && (
            <CanvasTouchKey
              color={touchKeyColors[1] || (getAssignment(keyCount + 1, currentPage)?.bg_color ?? "")}
              selected={selection.kind === "key" && selection.index === keyCount + 1}
              locked={lockedIndexes.has(keyCount + 1)}
              flashing={inputFlash?.kind === "key" && inputFlash.index === keyCount + 1}
              onSelect={() => onSelect({ kind: "key", index: keyCount + 1 })}
              onPress={onSimulate ? () => onSimulate({ type: "key", index: keyCount + 1 }) : undefined}
            />
          )}
        </div>
      )}

      {/* Touch strip */}
      {hasTouchscreen && (
        <div
          onClick={(e) => {
            const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            const x = Math.round(((e.clientX - rect.left) / rect.width) * 800);
            if (e.shiftKey) {
              onSimulate?.({ type: "touch", x });
              return;
            }
            const hit = zoneBounds.findIndex((b) => x >= b.x && x < b.x + b.w);
            if (customZoneCount > 0) {
              const zone =
                hit >= 0 ? hit : Math.min(
                  customZoneCount - 1,
                  Math.floor(x / (800 / customZoneCount))
                );
              onSelect({ kind: "strip", zone });
            } else {
              // Clicking the strip edits the strip. (The default readouts
              // are configured on their dials — the editor says so.)
              onSelect({ kind: "strip", zone: null });
            }
          }}
          onMouseEnter={() => setStripHover(true)}
          onMouseLeave={() => setStripHover(false)}
          title="Touch strip: click to edit, Shift+click to tap it"
          style={{
            width: gridWidth,
            height: Math.round(gridWidth / 8),
            borderRadius: "var(--border-radius)",
            overflow: "hidden",
            background: "#000",
            border:
              selection.kind === "strip"
                ? "2px solid var(--accent)"
                : "1px solid #2a2a3a",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#445",
            fontSize: "var(--font-size-2xs)",
            position: "relative",
          }}
        >
          {liveImagesValid && images["touchscreen"] ? (
            <img
              src={images["touchscreen"]}
              draggable={false}
              style={{ width: "100%", height: "100%", display: "block" }}
              alt=""
            />
          ) : (
            "touch strip"
          )}
          {/* Hovering reveals the zone boundaries — the strip is editable. */}
          {stripHover && zoneBounds.length > 1 &&
            zoneBounds.slice(1).map((b, i) => (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: `${(b.x / 800) * 100}%`,
                  top: 4, bottom: 4, width: 1,
                  background: "rgba(255,255,255,0.25)",
                  pointerEvents: "none",
                }}
              />
            ))}
          {/* A real touch on the hardware flashes the touched zone. */}
          {inputFlash?.kind === "touch" && (() => {
            const b =
              zoneBounds.find(
                (zb) => inputFlash.index >= zb.x && inputFlash.index < zb.x + zb.w
              ) ?? { x: 0, w: 800 };
            return (
              <div
                style={{
                  position: "absolute",
                  left: `${(b.x / 800) * 100}%`,
                  width: `${(b.w / 800) * 100}%`,
                  top: 0, bottom: 0,
                  border: "2px solid #f59e0b",
                  borderRadius: "var(--border-radius)",
                  pointerEvents: "none",
                }}
              />
            );
          })()}
        </div>
      )}

      {/* Dials */}
      {dialCount > 0 && (
        <div style={{ display: "flex", justifyContent: "space-around" }}>
          {Array.from({ length: dialCount }, (_, i) => {
            const dial = getDial(i);
            const isSelected = selection.kind === "dial" && selection.index === i;
            const hasAssignment =
              !!dial?.label || !!dial?.adjust?.key ||
              !!dial?.cw?.length || !!dial?.ccw?.length || !!dial?.press?.length;
            return (
              <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "var(--space-xs)" }}>
                <button
                  onClick={(e) => {
                    if (e.shiftKey) {
                      onSimulate?.({ type: "dial_push", index: i });
                      return;
                    }
                    onSelect({ kind: "dial", index: i });
                  }}
                  title={`Dial ${i + 1}${dial?.label ? `: ${dial.label}` : ""} · click to edit, Shift+click to press`}
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: "50%",
                    background: "#16161f",
                    border: isSelected
                      ? "2px solid var(--accent)"
                      : "1px solid #2a2a3a",
                    boxShadow:
                      inputFlash?.kind === "dial" && inputFlash.index === i
                        ? "0 0 0 3px #f59e0b"
                        : undefined,
                    cursor: "pointer",
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      top: 4,
                      left: "50%",
                      width: 2,
                      height: 9,
                      marginLeft: -1,
                      background: hasAssignment ? "var(--accent)" : "#3a3a4e",
                      borderRadius: "var(--radius-sm)",
                    }}
                  />
                </button>
                <div style={{ fontSize: "var(--font-size-2xs)", color: "#667", maxWidth: 64, textAlign: "center", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {dial?.label || `Dial ${i + 1}`}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* The shell carries the unit's identity */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "var(--space-sm)",
          marginTop: "var(--space-2xs)",
          fontSize: "var(--font-size-2xs)",
          color: "#556",
          userSelect: "none",
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: connected ? "var(--color-success)" : "var(--text-muted)",
          }}
        />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: Math.max(160, gridWidth - 40) }}>
          {name}
          {model && name !== model ? ` · ${model}` : ""}
          {isVirtual ? " · virtual" : ""}
          {!connected ? " · not connected" : ""}
        </span>
      </div>
    </div>
  );
}

function KeyCell({
  index,
  image,
  assignment,
  selected,
  locked,
  flashing,
  onSelect,
  onPress,
}: {
  index: number;
  image?: string;
  assignment: ButtonAssignment | undefined;
  selected: boolean;
  locked: boolean;
  flashing: boolean;
  onSelect: () => void;
  onPress?: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const hasAssignment =
    !!assignment?.label || !!assignment?.icon || !!assignment?.bindings?.press?.length;

  return (
    <div
      style={{ position: "relative", width: CANVAS_KEY_PX, height: CANVAS_KEY_PX }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={(e) => {
          if (e.shiftKey && onPress) {
            onPress();
            return;
          }
          onSelect();
        }}
        title={`Key ${index + 1}${assignment?.label ? `: ${assignment.label}` : ""} · click to edit${onPress ? ", Shift+click to press" : ""}`}
        style={{
          width: "100%",
          height: "100%",
          padding: 0,
          borderRadius: "var(--radius-lg)",
          overflow: "hidden",
          border: selected ? "2px solid var(--accent)" : "1px solid #2a2a3a",
          boxShadow: flashing ? "0 0 0 3px #f59e0b" : undefined,
          background: image ? "#000" : assignment?.bg_color || "#101018",
          cursor: "pointer",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "var(--space-2xs)",
          color: assignment?.text_color || "#778",
        }}
      >
        {image ? (
          <img
            src={image}
            draggable={false}
            style={{ width: "100%", height: "100%", display: "block" }}
            alt=""
          />
        ) : (
          <>
            {!hasAssignment && (
              <span style={{ fontSize: "var(--font-size-2xs)", color: "#33334a" }}>{index + 1}</span>
            )}
            {assignment?.icon && (
              <ElementIcon
                name={assignment.icon}
                size={assignment.label ? 22 : 30}
                color={assignment?.text_color || "#99a"}
              />
            )}
            {assignment?.label && (
              <span
                style={{
                  fontSize: "var(--font-size-2xs)",
                  maxWidth: CANVAS_KEY_PX - 10,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {assignment.label}
              </span>
            )}
          </>
        )}
      </button>
      {locked && (
        <span
          title="Locked: same on every page"
          style={{
            position: "absolute",
            top: 3,
            left: 3,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 14,
            height: 14,
            borderRadius: "var(--border-radius)",
            background: "rgba(12,12,20,0.75)",
            color: "var(--accent)",
            pointerEvents: "none",
          }}
        >
          <Pin size={9} />
        </span>
      )}
      {onPress && hovered && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPress();
          }}
          title="Press this key"
          style={{
            position: "absolute",
            top: 3,
            right: 3,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 16,
            height: 16,
            borderRadius: "var(--border-radius)",
            background: "rgba(12,12,20,0.8)",
            color: "#cdd",
            cursor: "pointer",
          }}
        >
          <Play size={9} />
        </button>
      )}
    </div>
  );
}

function CanvasTouchKey({
  color,
  selected,
  locked,
  flashing,
  onSelect,
  onPress,
}: {
  color: string;
  selected: boolean;
  locked: boolean;
  flashing: boolean;
  onSelect: () => void;
  onPress?: () => void;
}) {
  return (
    <button
      onClick={(e) => {
        if (e.shiftKey && onPress) {
          onPress();
          return;
        }
        onSelect();
      }}
      title={`Touch key · click to edit${onPress ? ", Shift+click to tap" : ""}${locked ? " · locked" : ""}`}
      style={{
        width: 22,
        height: 44,
        borderRadius: "var(--radius-lg)",
        flexShrink: 0,
        border: selected ? "2px solid var(--accent)" : "1px solid #2a2a3a",
        boxShadow: flashing ? "0 0 0 3px #f59e0b" : undefined,
        background: color && color !== "#000000" ? color : "#101018",
        cursor: "pointer",
      }}
    />
  );
}
