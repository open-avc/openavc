import { useState, useEffect, useRef, useCallback } from "react";
import type { UIElement } from "../../../api/types";
import * as wsClient from "../../../api/wsClient";

interface Props {
  element: UIElement;
  previewMode: boolean;
  liveState: Record<string, unknown>;
}

export function KeypadRenderer({ element, previewMode }: Props) {
  const [buffer, setBuffer] = useState("");
  const autoSendTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const maxDigits = element.digits ?? 4;
  const autoSend = element.auto_send ?? false;
  const autoSendDelay = element.auto_send_delay_ms ?? 1500;
  const keypadStyle = element.keypad_style ?? "numeric";
  const showDisplay = element.show_display ?? true;

  // Reset buffer when leaving preview mode
  useEffect(() => {
    if (!previewMode) {
      setBuffer("");
      if (autoSendTimerRef.current) {
        clearTimeout(autoSendTimerRef.current);
        autoSendTimerRef.current = null;
      }
    }
  }, [previewMode]);

  // Clean up timer on unmount
  useEffect(() => {
    return () => {
      if (autoSendTimerRef.current) {
        clearTimeout(autoSendTimerRef.current);
      }
    };
  }, []);

  const submitValue = useCallback(
    (value: string) => {
      if (!value) return;
      wsClient.send({
        type: "ui.submit",
        element_id: element.id,
        value,
      });
      setBuffer("");
      if (autoSendTimerRef.current) {
        clearTimeout(autoSendTimerRef.current);
        autoSendTimerRef.current = null;
      }
    },
    [element.id],
  );

  const handleDigit = useCallback(
    (digit: string) => {
      if (!previewMode) return;
      setBuffer((prev) => {
        if (prev.length >= maxDigits) return prev;
        const next = prev + digit;
        // Auto-submit when max digits reached
        if (autoSend && next.length >= maxDigits) {
          // Use setTimeout so the state update completes first
          setTimeout(() => submitValue(next), 0);
          return next;
        }
        // Auto-submit after delay
        if (autoSend) {
          if (autoSendTimerRef.current) {
            clearTimeout(autoSendTimerRef.current);
          }
          autoSendTimerRef.current = setTimeout(() => {
            submitValue(next);
          }, autoSendDelay);
        }
        return next;
      });
    },
    [previewMode, maxDigits, autoSend, autoSendDelay, submitValue],
  );

  const handleClear = useCallback(() => {
    if (!previewMode) return;
    setBuffer("");
    if (autoSendTimerRef.current) {
      clearTimeout(autoSendTimerRef.current);
      autoSendTimerRef.current = null;
    }
  }, [previewMode]);

  const handleEnter = useCallback(() => {
    if (!previewMode) return;
    submitValue(buffer);
  }, [previewMode, buffer, submitValue]);

  // Build button grid rows
  const rows: string[][] = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
  ];
  const bottomRow =
    keypadStyle === "phone" ? ["*", "0", "#"] : ["C", "0", "\u23CE"];

  const displayText = previewMode
    ? buffer || ""
    : "\u2014".repeat(maxDigits);

  const textColor = String(element.style.text_color || "#ffffff");
  const fontSize = element.style.font_size
    ? Number(element.style.font_size)
    : 14;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        padding: "6px",
        gap: "4px",
        boxSizing: "border-box",
      }}
    >
      {/* Label */}
      {element.label && (
        <div
          style={{
            fontSize: Math.max(fontSize - 2, 10),
            color: "#cccccc",
            textAlign: "center",
            flexShrink: 0,
            lineHeight: 1.2,
          }}
        >
          {element.label}
        </div>
      )}

      {/* Display area */}
      {showDisplay && (
        <div
          style={{
            background: "#1a1a1a",
            borderRadius: "4px",
            padding: "6px 10px",
            fontFamily: "'Courier New', Courier, monospace",
            fontSize: Math.max(fontSize + 2, 16),
            color: textColor,
            textAlign: "right",
            minHeight: "28px",
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            flexShrink: 0,
            border: "1px solid rgba(255,255,255,0.1)",
            overflow: "hidden",
          }}
        >
          {displayText || "\u00A0"}
        </div>
      )}

      {/* Keypad grid */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "3px",
          flex: 1,
          minHeight: 0,
        }}
      >
        {[...rows, bottomRow].map((row, rowIdx) => (
          <div
            key={rowIdx}
            style={{
              display: "flex",
              gap: "3px",
              flex: 1,
              minHeight: 0,
            }}
          >
            {row.map((key) => {
              const isAction = key === "C" || key === "\u23CE";
              let onClick: (() => void) | undefined;
              if (previewMode) {
                if (key === "C") onClick = handleClear;
                else if (key === "\u23CE") onClick = handleEnter;
                else onClick = () => handleDigit(key);
              }

              return (
                <KeypadButton
                  key={key}
                  label={key}
                  onClick={onClick}
                  previewMode={previewMode}
                  isAction={isAction}
                  textColor={textColor}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Individual keypad button with hover effect. */
function KeypadButton({
  label,
  onClick,
  previewMode,
  isAction,
  textColor,
}: {
  label: string;
  onClick?: () => void;
  previewMode: boolean;
  isAction: boolean;
  textColor: string;
}) {
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);

  const bg = pressed && previewMode
    ? "#555555"
    : hovered && previewMode
      ? "#4a4a4a"
      : "#3a3a3a";

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => {
        setHovered(false);
        setPressed(false);
      }}
      onMouseDown={() => setPressed(true)}
      onMouseUp={() => setPressed(false)}
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: bg,
        borderRadius: "6px",
        color: isAction ? "#90caf9" : textColor,
        fontSize: "14px",
        fontWeight: isAction ? 600 : 500,
        cursor: previewMode ? "pointer" : "default",
        userSelect: "none",
        transition: "background 0.1s",
        minHeight: 0,
      }}
    >
      {label}
    </div>
  );
}
