/**
 * A secret command parameter on a surface that saves it: a macro step, a
 * control's action. Drawn as Edit Device draws a password: "Saved on this
 * system" with Change, or a box whose own Save sends what was typed.
 *
 * What is typed stays in this box until its Save: the server files it and
 * answers a marker naming it, and only the marker reaches the step (and so
 * the project, its saves and its undo history). The value is never read back.
 */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import * as api from "../../api/restClient";
import type { DriverParamDef } from "../../api/types";
import { paramLabel } from "./paramLabel";
import { SavedLoginField, type SavedLoginState } from "./SavedLoginField";
import { savedParamName } from "./savedParams";

// What this tab already knows: a value it saved, or a name it asked about.
const known = new Map<string, boolean>();

async function saveParam(value: string): Promise<string> {
  const marker = await api.saveParamValue(value);
  const name = savedParamName(marker);
  if (name) known.set(name, true);
  return marker;
}

/** Whether a value is saved here under `name`: undefined while asking. */
function useSavedHere(name: string | null): boolean | undefined {
  const [saved, setSaved] = useState<boolean | undefined>(() => (name ? known.get(name) : undefined));
  useEffect(() => {
    setSaved(name ? known.get(name) : undefined);
    if (!name || known.has(name)) return;
    let cancelled = false;
    const settle = (found: boolean) => {
      known.set(name, found);
      if (!cancelled) setSaved(found);
    };
    api.savedParamState(name).then(settle, () => settle(false));
    return () => {
      cancelled = true;
    };
  }, [name]);
  return saved;
}

function stateOf(name: string | null, saved: boolean | undefined): SavedLoginState {
  if (!name) return "none";
  if (saved === undefined) return "checking";
  return saved ? "saved" : "needs_login";
}

export function SavedParamField({
  paramKey,
  def,
  value,
  onChange,
  style,
  trailing,
}: {
  paramKey: string;
  def: Partial<DriverParamDef>;
  /** The parameter as the step holds it: empty, or a saved value's marker. */
  value: string;
  onChange: (marker: string) => void;
  style?: CSSProperties;
  /** Drawn after the box (the `$` toggle). */
  trailing?: ReactNode;
}) {
  const name = savedParamName(value);
  const saved = useSavedHere(name);
  return (
    <div style={style}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <SavedLoginField
          label={paramLabel(paramKey, def)}
          state={stateOf(name, saved)}
          saveOnLeave
          onSave={async (typed) => {
            onChange(await saveParam(typed));
          }}
        />
      </div>
      {trailing}
    </div>
  );
}
