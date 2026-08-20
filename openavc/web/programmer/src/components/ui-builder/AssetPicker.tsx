import { useState } from "react";
import { Image } from "lucide-react";
import * as api from "../../api/restClient";
import { AssetBrowserModal } from "../assets/AssetBrowser";

interface AssetPickerProps {
  value: string;
  onChange: (ref: string) => void;
}

/**
 * Inline image-asset picker used by element property fields. Shows a small
 * thumbnail + button; opens the shared asset browser modal in image-only mode.
 */
export function AssetPicker({ value, onChange }: AssetPickerProps) {
  const [open, setOpen] = useState(false);
  const currentName = value?.replace("assets://", "") || "";

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
        {currentName ? (
          <img
            src={api.getAssetUrl(currentName)}
            alt={currentName}
            style={{
              width: 32,
              height: 32,
              objectFit: "cover",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
            }}
          />
        ) : (
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: "var(--border-radius)",
              border: "1px dashed var(--border-color)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-muted)",
            }}
          >
            <Image size={16} />
          </div>
        )}
        <button
          onClick={() => setOpen(true)}
          style={{
            padding: "var(--space-xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-sm)",
            color: "var(--accent)",
            background: "var(--bg-base)",
            border: "1px solid var(--border-color)",
          }}
        >
          {currentName ? "Change" : "Choose Image"}
        </button>
        {currentName && (
          <button
            onClick={() => onChange("")}
            style={{
              padding: "var(--space-2xs) var(--space-xs)",
              fontSize: "var(--font-size-2xs)",
              color: "var(--text-muted)",
              borderRadius: "var(--border-radius)",
            }}
          >
            Clear
          </button>
        )}
      </div>
      {open && (
        <AssetBrowserModal
          filter="image"
          currentValue={value}
          onSelect={(ref) => {
            onChange(ref);
            setOpen(false);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}
