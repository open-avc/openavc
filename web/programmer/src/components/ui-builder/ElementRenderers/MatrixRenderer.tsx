import { useState } from "react";
import type { UIElement } from "../../../api/types";
import * as wsClient from "../../../api/wsClient";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

/**
 * Resolve the current route map: output index (1-based) -> input index (1-based).
 * In design mode, returns a diagonal demo (output N -> input N).
 */
function resolveRoutes(
  element: UIElement,
  previewMode: boolean,
  liveState: Record<string, unknown>,
  outputCount: number,
  inputCount: number,
): Map<number, number> {
  const routes = new Map<number, number>();
  const pattern = element.matrix_config?.route_key_pattern;

  if (previewMode && pattern) {
    for (let out = 1; out <= outputCount; out++) {
      const key = pattern.replace("*", String(out));
      const val = liveState[key];
      if (val !== undefined && val !== null) {
        routes.set(out, Number(val));
      }
    }
  } else {
    // Demo: diagonal routing
    for (let out = 1; out <= outputCount; out++) {
      if (out <= inputCount) {
        routes.set(out, out);
      }
    }
  }

  return routes;
}

/**
 * Resolve labels for inputs or outputs.
 * Priority: state key pattern -> static labels -> default "In N" / "Out N".
 */
function resolveLabels(
  count: number,
  staticLabels: string[] | undefined,
  keyPattern: string | undefined,
  liveState: Record<string, unknown>,
  previewMode: boolean,
  prefix: string,
): string[] {
  const labels: string[] = [];
  for (let i = 1; i <= count; i++) {
    let label: string | undefined;

    // Try state key pattern first (only in preview mode)
    if (previewMode && keyPattern) {
      const key = keyPattern.replace("*", String(i));
      const val = liveState[key];
      if (val !== undefined && val !== null && String(val).trim() !== "") {
        label = String(val);
      }
    }

    // Fall back to static labels
    if (!label && staticLabels && staticLabels[i - 1]) {
      label = staticLabels[i - 1];
    }

    // Fall back to default
    if (!label) {
      label = `${prefix} ${i}`;
    }

    labels.push(label);
  }
  return labels;
}

function handleCrosspointClick(
  element: UIElement,
  previewMode: boolean,
  inputIndex: number,
  outputIndex: number,
) {
  if (!previewMode) return;
  wsClient.send({
    type: "ui.route",
    element_id: element.id,
    input: inputIndex,
    output: outputIndex,
  });
}

export function MatrixRenderer({ element, previewMode, liveState }: Props) {
  const config = element.matrix_config ?? {};
  const inputCount = config.input_count ?? 4;
  const outputCount = config.output_count ?? 4;
  const matrixStyle = element.matrix_style ?? "crosspoint";

  const activeColor = String(
    element.style.crosspoint_active_color ?? "#4CAF50",
  );
  const inactiveColor = String(
    element.style.crosspoint_inactive_color ?? "#333333",
  );
  const headerBg = String(element.style.header_bg ?? "#1a1a2e");
  const cellSize = Number(element.style.cell_size ?? 44);

  const routes = resolveRoutes(
    element,
    previewMode,
    liveState,
    outputCount,
    inputCount,
  );

  const inputLabels = resolveLabels(
    inputCount,
    config.input_labels,
    config.input_key_pattern,
    liveState,
    previewMode,
    "In",
  );

  const outputLabels = resolveLabels(
    outputCount,
    config.output_labels,
    config.output_key_pattern,
    liveState,
    previewMode,
    "Out",
  );

  const rotateHeaders = inputCount > 4;

  if (matrixStyle === "list") {
    return (
      <ListView
        element={element}
        previewMode={previewMode}
        inputCount={inputCount}
        outputCount={outputCount}
        inputLabels={inputLabels}
        outputLabels={outputLabels}
        routes={routes}
        activeColor={activeColor}
      />
    );
  }

  // Crosspoint view
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        boxSizing: "border-box",
        overflow: "hidden",
      }}
    >
      {/* Label */}
      {element.label && (
        <div
          style={{
            fontSize: 12,
            color: "#cccccc",
            padding: "6px 8px 2px",
            textAlign: "center",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            flexShrink: 0,
          }}
        >
          {element.label}
        </div>
      )}

      {/* Scrollable grid area */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "4px",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `${cellSize + 20}px repeat(${inputCount}, ${cellSize}px)`,
            gridTemplateRows: `${rotateHeaders ? cellSize + 20 : cellSize}px repeat(${outputCount}, ${cellSize}px)`,
            gap: "1px",
            width: "fit-content",
          }}
        >
          {/* Top-left empty corner cell */}
          <div
            style={{
              backgroundColor: headerBg,
              borderRadius: "4px 0 0 0",
            }}
          />

          {/* Input header labels across the top */}
          {inputLabels.map((label, i) => (
            <div
              key={`in-${i}`}
              style={{
                backgroundColor: headerBg,
                display: "flex",
                alignItems: rotateHeaders ? "flex-end" : "center",
                justifyContent: "center",
                padding: "2px",
                overflow: "hidden",
                borderRadius:
                  i === inputCount - 1 ? "0 4px 0 0" : undefined,
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  color: "#bbbbbb",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  maxWidth: rotateHeaders ? cellSize + 16 : cellSize - 4,
                  display: "inline-block",
                  textAlign: "center",
                  ...(rotateHeaders
                    ? {
                        transform: "rotate(-45deg)",
                        transformOrigin: "center bottom",
                      }
                    : {}),
                }}
              >
                {label}
              </span>
            </div>
          ))}

          {/* Output rows */}
          {outputLabels.map((outLabel, outIdx) => {
            const outputNum = outIdx + 1;
            const activeInput = routes.get(outputNum);

            return inputLabels.map((_, inIdx) => {
              const inputNum = inIdx + 1;
              const isActive = activeInput === inputNum;

              if (inIdx === 0) {
                // Output label cell + first crosspoint
                return [
                  <div
                    key={`out-label-${outIdx}`}
                    style={{
                      backgroundColor: headerBg,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "flex-end",
                      paddingRight: 6,
                      borderRadius:
                        outIdx === outputCount - 1
                          ? "0 0 0 4px"
                          : undefined,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 10,
                        color: "#bbbbbb",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        maxWidth: cellSize + 14,
                      }}
                    >
                      {outLabel}
                    </span>
                  </div>,
                  <CrosspointCell
                    key={`cp-${outIdx}-${inIdx}`}
                    isActive={isActive}
                    activeColor={activeColor}
                    inactiveColor={inactiveColor}
                    cellSize={cellSize}
                    previewMode={previewMode}
                    onClick={() =>
                      handleCrosspointClick(
                        element,
                        previewMode,
                        inputNum,
                        outputNum,
                      )
                    }
                  />,
                ];
              }

              return (
                <CrosspointCell
                  key={`cp-${outIdx}-${inIdx}`}
                  isActive={isActive}
                  activeColor={activeColor}
                  inactiveColor={inactiveColor}
                  cellSize={cellSize}
                  previewMode={previewMode}
                  onClick={() =>
                    handleCrosspointClick(
                      element,
                      previewMode,
                      inputNum,
                      outputNum,
                    )
                  }
                />
              );
            });
          })}
        </div>
      </div>
    </div>
  );
}

/** A single crosspoint indicator cell. */
function CrosspointCell({
  isActive,
  activeColor,
  inactiveColor,
  cellSize,
  previewMode,
  onClick,
}: {
  isActive: boolean;
  activeColor: string;
  inactiveColor: string;
  cellSize: number;
  previewMode: boolean;
  onClick: () => void;
}) {
  const dotSize = isActive ? Math.max(12, cellSize * 0.45) : Math.max(8, cellSize * 0.25);

  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.03)",
        cursor: previewMode ? "pointer" : "default",
        minWidth: cellSize,
        minHeight: cellSize,
      }}
    >
      <div
        style={{
          width: dotSize,
          height: dotSize,
          borderRadius: "50%",
          backgroundColor: isActive ? activeColor : inactiveColor,
          opacity: isActive ? 1 : 0.4,
          transition: "all 0.15s ease",
          boxShadow: isActive
            ? `0 0 6px ${activeColor}88`
            : "none",
        }}
      />
    </div>
  );
}

/** List view: each output is a row with a dropdown to select the routed input. */
function ListView({
  element,
  previewMode,
  inputCount,
  outputCount,
  inputLabels,
  outputLabels,
  routes,
  activeColor,
}: {
  element: UIElement;
  previewMode: boolean;
  inputCount: number;
  outputCount: number;
  inputLabels: string[];
  outputLabels: string[];
  routes: Map<number, number>;
  activeColor: string;
}) {
  // Track local selections for immediate feedback
  const [localRoutes, setLocalRoutes] = useState<Map<number, number>>(
    new Map(),
  );

  const handleChange = (outputNum: number, inputNum: number) => {
    setLocalRoutes((prev) => {
      const next = new Map(prev);
      next.set(outputNum, inputNum);
      return next;
    });
    handleCrosspointClick(element, previewMode, inputNum, outputNum);
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        boxSizing: "border-box",
        overflow: "hidden",
      }}
    >
      {/* Label */}
      {element.label && (
        <div
          style={{
            fontSize: 12,
            color: "#cccccc",
            padding: "6px 8px 2px",
            textAlign: "center",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            flexShrink: 0,
          }}
        >
          {element.label}
        </div>
      )}

      {/* Scrollable list area */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "4px 8px",
          boxSizing: "border-box",
        }}
      >
        {outputLabels.map((outLabel, outIdx) => {
          const outputNum = outIdx + 1;
          // Prefer live state routes, fall back to local selection
          const currentInput =
            routes.get(outputNum) ?? localRoutes.get(outputNum) ?? 0;

          return (
            <div
              key={`row-${outIdx}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "4px 0",
                borderBottom: "1px solid rgba(255,255,255,0.06)",
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  color: "#bbbbbb",
                  minWidth: 60,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  flexShrink: 0,
                }}
              >
                {outLabel}
              </div>

              <div
                style={{
                  fontSize: 10,
                  color: "#666666",
                  flexShrink: 0,
                }}
              >
                &larr;
              </div>

              <select
                value={currentInput}
                onChange={(e) =>
                  handleChange(outputNum, parseInt(e.target.value, 10))
                }
                disabled={!previewMode}
                style={{
                  flex: 1,
                  minWidth: 0,
                  padding: "4px 6px",
                  borderRadius: 4,
                  border: "1px solid rgba(255,255,255,0.15)",
                  background:
                    currentInput > 0
                      ? `${activeColor}22`
                      : "rgba(255,255,255,0.05)",
                  color: "#dddddd",
                  fontSize: 11,
                  cursor: previewMode ? "pointer" : "default",
                }}
              >
                <option value={0}>-- None --</option>
                {inputLabels.map((inLabel, inIdx) => (
                  <option key={inIdx} value={inIdx + 1}>
                    {inLabel}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
      </div>
    </div>
  );
}
