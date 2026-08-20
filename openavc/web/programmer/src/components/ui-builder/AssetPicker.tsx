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
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        {currentName ? (
          <img
            src={api.getAssetUrl(currentName)}
            alt={currentName}
            style={{
              width: 32,
              height: 32,
              objectFit: "cover",
              borderRadius: 4,
              border: "1px solid var(--border-color)",
            }}
          />
        ) : (
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 4,
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
            padding: "3px 8px",
            borderRadius: 3,
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
              padding: "2px 4px",
              fontSize: 10,
              color: "var(--text-muted)",
              borderRadius: 3,
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
