/**
 * A matrix's fields in the Basic section: its sources and destinations, the
 * keys it reads and writes per destination, its lock, mute and audio follow,
 * its presets and its look.
 *
 * A matrix axis is either a list somebody wrote out or a generator that stands
 * for one (`matrixModel.ts` expands it, as the panel does). The fields here
 * author the generator: a count, a key with a `*` in it, a row of labels, which
 * covers a frame whose ports run 1..N. An axis written out entry by entry
 * carries per-entry values and keys these fields cannot express, so they step
 * aside rather than flatten it back, and the setup row is where it is edited.
 */
import { useState } from "react";
import { Plus, X } from "lucide-react";
import type { ProjectConfig, UIElement } from "../../../api/types";
import { MATRIX_PANEL_WRITABLE_PREFIXES } from "../../../api/uiBindingReach.gen";
import { pxToRem, remToPx } from "../uiBuilderHelpers";
import { isPlainObject } from "../authoredValues";
import { resolveMatrixAxis } from "../matrixModel";
import { MatrixSetupDialog } from "../MatrixSetupDialog";
import { FieldHint, FieldRow, SubSection, type FieldProps } from "./FieldRow";
import { StyleColorRow } from "./StyleRows";
import { useRowKeys } from "./rowKeys";

interface MatrixPreset {
  name: string;
  macro?: string;
}

type MatrixAxis = "sources" | "destinations";
type MatrixConfig = Record<string, unknown> | undefined;

export function MatrixFields({
  element,
  project,
  macros,
  onChange,
}: FieldProps & { project: ProjectConfig; macros: { id: string; name: string }[] }) {
  return (
    <>
      <MatrixSetupRow element={element} project={project} onChange={onChange} />
      <FieldRow label="Style">
        <select value={element.matrix_style || "crosspoint"} onChange={(e) => onChange({ matrix_style: e.target.value })} style={{ flex: 1 }}>
          <option value="crosspoint">Crosspoint Grid</option>
          <option value="list">List (Dropdowns)</option>
          <option value="tiles">Tiles (One Card Per Destination)</option>
        </select>
      </FieldRow>
      <SourceAxisFields element={element} onChange={onChange} />
      <DestinationAxisFields element={element} onChange={onChange} />
      <NameKeyFields element={element} onChange={onChange} />
      <AudioFollowFields element={element} onChange={onChange} />
      <LockFields element={element} onChange={onChange} />
      <MuteFields element={element} onChange={onChange} />
      <GeneratorLabels element={element} onChange={onChange} axis="sources" title="Source Labels" />
      <GeneratorLabels element={element} onChange={onChange} axis="destinations" title="Destination Labels" />

      <MatrixPresetsEditor
        presets={element.matrix_config?.presets as MatrixPreset[] || []}
        macros={macros}
        onChange={(presets) => onChange({ matrix_config: { ...element.matrix_config, presets: presets.length > 0 ? presets : undefined } })}
      />

      <MatrixAppearance element={element} onChange={onChange} />
    </>
  );
}

/**
 * The way into the device picker, and the one line that says what it did.
 *
 * The fields under it author the GENERATOR, which covers a frame whose ports
 * run 1..N and nothing else. The picker writes the axes out entry by entry,
 * which is what a patched frame, string port ids or destinations on two
 * devices need, so once it has run those fields step aside
 * and this row is where the lists are edited from.
 */
function MatrixSetupRow({
  element,
  project,
  onChange,
}: FieldProps & { project: ProjectConfig }) {
  const [open, setOpen] = useState(false);
  const written =
    matrixAxisIsList(element.matrix_config, "sources") ||
    matrixAxisIsList(element.matrix_config, "destinations");

  return (
    <>
      <FieldRow label="From device">
        <button
          type="button"
          onClick={() => setOpen(true)}
          style={{
            flex: 1,
            padding: "4px 8px",
            borderRadius: "var(--border-radius)",
            border: "1px dashed var(--accent)",
            background: "rgba(138,180,147,0.08)",
            color: "var(--accent)",
            fontSize: 11,
            cursor: "pointer",
          }}
          title="Fill the sources, destinations and route keys in from what the driver declares"
        >
          {written ? "Set up again from a device…" : "Set up from a device…"}
        </button>
      </FieldRow>
      {written && (
        <FieldHint>
          {matrixList(element.matrix_config, "sources").length} sources and{" "}
          {matrixList(element.matrix_config, "destinations").length} destinations are
          written out. Re-open this to rename, reorder or pick up new ports.
        </FieldHint>
      )}
      {open && (
        <MatrixSetupDialog
          element={element}
          project={project}
          onApply={onChange}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

/** The sources: how many the generator makes, or that they are written out. */
function SourceAxisFields({ element, onChange }: FieldProps) {
  if (matrixAxisIsList(element.matrix_config, "sources")) {
    return (
      <FieldHint>
        Sources are set as a list of {matrixList(element.matrix_config, "sources").length} entries,
        each with its own value. The count and key fields do not apply.
      </FieldHint>
    );
  }
  return <GeneratorCountField element={element} onChange={onChange} axis="sources" label="Sources" prefix="Input" />;
}

/** The destinations: how many, and the keys each one routes through. */
function DestinationAxisFields({ element, onChange }: FieldProps) {
  if (matrixAxisIsList(element.matrix_config, "destinations")) {
    return (
      <>
        <FieldHint>
          Destinations are set as a list of {matrixList(element.matrix_config, "destinations").length} entries,
          each with its own value and route key. The count and key fields do not apply.
        </FieldHint>
        {matrixListHasAudio(element.matrix_config) && <AudioReadoutField element={element} onChange={onChange} />}
      </>
    );
  }
  return (
    <>
      <GeneratorCountField element={element} onChange={onChange} axis="destinations" label="Destinations" prefix="Output" />
      <GeneratorKeyField
        element={element} onChange={onChange} axis="destinations" field="route_key"
        label="Route Key" placeholder="device.sw.output_*_source"
      />
      <FieldHint>
        Use * for the destination number (1-based)
      </FieldHint>
      <GeneratorKeyField
        element={element} onChange={onChange} axis="destinations" field="audio_route_key"
        label="Audio Route Key" placeholder="device.sw.output_*_audio_source"
      />
      <FieldHint>
        Optional. When set, a destination whose audio is on a different
        source names it on the panel. Leave it empty when the device keeps
        audio with the video.
      </FieldHint>
    </>
  );
}

/** The readout's off switch for destinations written out with their own audio keys. */
function AudioReadoutField({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Audio Readout">
        <input
          type="checkbox"
          checked
          onChange={() => onChange({
            matrix_config: matrixDropListAudio(element.matrix_config),
          })}
        />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          Name the audio source when it differs from the video
        </span>
      </FieldRow>
      <FieldHint>
        Read from the device, per destination. Turn it off when the device
        keeps audio with the video: what it reports then is left over from
        the last time audio was switched separately, and naming it on the
        panel is wrong. Matrix Setup puts it back.
      </FieldHint>
    </>
  );
}

/** How many entries an axis's generator makes; a new one is named `<prefix> N`. */
function GeneratorCountField({
  element,
  onChange,
  axis,
  label,
  prefix,
}: FieldProps & { axis: MatrixAxis; label: string; prefix: string }) {
  return (
    <FieldRow label={label}>
      <input
        type="number"
        value={Number(matrixFrom(element.matrix_config, axis).count ?? 4)}
        onChange={(e) => onChange({
          matrix_config: withGeneratorCount(element.matrix_config, axis, Number(e.target.value), prefix),
        })}
        min={1}
        max={32}
        style={{ flex: 1 }}
      />
    </FieldRow>
  );
}

/** One key the generator fills in per entry, `*` standing for the entry's number. */
function GeneratorKeyField({
  element,
  onChange,
  axis,
  field,
  label,
  placeholder,
}: FieldProps & { axis: MatrixAxis; field: string; label: string; placeholder: string }) {
  return (
    <FieldRow label={label}>
      <input
        value={String(matrixFrom(element.matrix_config, axis)[field] || "")}
        onChange={(e) => onChange({ matrix_config: setMatrixFrom(element.matrix_config, axis, { [field]: e.target.value || undefined }) })}
        placeholder={placeholder}
        style={{ flex: 1, fontSize: 11 }}
      />
    </FieldRow>
  );
}

/** Where each axis still a generator reads its live names from. */
function NameKeyFields({ element, onChange }: FieldProps) {
  const sourcesListed = matrixAxisIsList(element.matrix_config, "sources");
  const destinationsListed = matrixAxisIsList(element.matrix_config, "destinations");
  return (
    <>
      {!sourcesListed && (
        <GeneratorKeyField
          element={element} onChange={onChange} axis="sources" field="label_key"
          label="Source Name Key" placeholder="device.sw.input_*_name"
        />
      )}
      {!destinationsListed && (
        <GeneratorKeyField
          element={element} onChange={onChange} axis="destinations" field="label_key"
          label="Destination Name Key" placeholder="device.sw.output_*_name"
        />
      )}
      {/* Only when there is a name-key field above it to explain. */}
      {(!sourcesListed || !destinationsListed) && (
        <FieldHint>
          Live names from state. * = the entry's number
        </FieldHint>
      )}
    </>
  );
}

/** Whether the matrix carries a `do` binding for this action. */
function hasDoBinding(element: UIElement, action: string): boolean {
  return !!(element.bindings as { do?: Record<string, unknown> })?.do?.[action];
}

function AudioFollowFields({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Audio Follow">
        <input
          type="checkbox"
          checked={element.matrix_config?.audio_follow_video ?? false}
          onChange={(e) => onChange({ matrix_config: { ...element.matrix_config, audio_follow_video: e.target.checked } })}
        />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Route audio with video</span>
      </FieldRow>
      {element.matrix_config?.audio_follow_video && !hasDoBinding(element, "audio_route") && (
        <FieldHint tone="warning" italic>
          Add an Audio Route binding in the Bindings tab to send the audio route command.
        </FieldHint>
      )}
      {element.matrix_config?.audio_follow_video && (
        <FieldHint italic>
          Only enable this if your matrix needs a separate audio route command. Many switchers route audio together with video automatically, so leave this off for those.
        </FieldHint>
      )}
    </>
  );
}

function LockFields({ element, onChange }: FieldProps) {
  const shown = element.matrix_config?.show_lock === true;
  return (
    <>
      <FieldRow label="Show Lock">
        <input
          type="checkbox"
          checked={shown}
          onChange={(e) => onChange({ matrix_config: { ...element.matrix_config, show_lock: e.target.checked || undefined } })}
        />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Lock buttons per destination</span>
      </FieldRow>
      {shown && !matrixAxisIsList(element.matrix_config, "destinations") && (
        <>
          <GeneratorKeyField
            element={element} onChange={onChange} axis="destinations" field="lock_key"
            label="Lock Key" placeholder="var.lock_*"
          />
          <FieldHint>
            A variable per destination, holding whether it is locked. Use * for
            the destination number.
          </FieldHint>
        </>
      )}
      {shown && matrixLockUnbacked(element.matrix_config) && (
        <FieldHint tone="warning" italic>
          Without a variable, a lock is only remembered by the panel that set
          it: it is forgotten when the page redraws, and no other panel sees it.
        </FieldHint>
      )}
    </>
  );
}

function MuteFields({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Show Mute">
        <input
          type="checkbox"
          checked={element.matrix_config?.show_mute !== false}
          onChange={(e) => onChange({ matrix_config: { ...element.matrix_config, show_mute: e.target.checked } })}
        />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Mute buttons per output</span>
      </FieldRow>
      <MuteWiringHints element={element} />
    </>
  );
}

/** What the mute buttons still need before they send anything. */
function MuteWiringHints({ element }: { element: UIElement }) {
  return (
    <>
      {/* Only once somebody asked for mute. The box reads ticked when the key
          is absent (which is what the panel does too), so warning on that
          state would put an orange line about a Bindings tab on every matrix
          the moment it was dropped, for a button the panel does not draw
          until the binding exists. */}
      {element.matrix_config?.show_mute === true && !hasDoBinding(element, "mute_route") && (
        <FieldHint tone="warning" italic>
          Add a Mute Route binding in the Bindings tab to wire the mute buttons to a command.
        </FieldHint>
      )}
      {element.matrix_config?.show_mute !== false && element.matrix_config?.audio_follow_video && !hasDoBinding(element, "audio_mute_route") && (
        <FieldHint tone="warning" italic>
          Add an Audio Mute Route binding in the Bindings tab so the mute button also mutes audio.
        </FieldHint>
      )}
    </>
  );
}

/** The labels an axis's generator gives its entries, while it is a generator. */
function GeneratorLabels({
  element,
  onChange,
  axis,
  title,
}: FieldProps & { axis: MatrixAxis; title: string }) {
  if (matrixAxisIsList(element.matrix_config, axis)) return null;
  return (
    <MatrixLabelEditor
      title={title}
      labels={(matrixFrom(element.matrix_config, axis).labels as string[] | undefined) || []}
      onChange={(labels) => onChange({ matrix_config: setMatrixFrom(element.matrix_config, axis, { labels }) })}
    />
  );
}

function MatrixAppearance({ element, onChange }: FieldProps) {
  return (
    <>
      <SubSection label="Matrix Appearance" />
      <StyleColorRow element={element} onChange={onChange} field="crosspoint_active_color" label="Active Color" />
      <StyleColorRow element={element} onChange={onChange} field="crosspoint_inactive_color" label="Inactive Color" />
      <FieldRow label="Cell Size">
        <input
          type="number"
          value={remToPx(element.style?.cell_size as number) ?? ""}
          placeholder="Auto"
          onChange={(e) => onChange({ style: { ...element.style, cell_size: e.target.value ? pxToRem(Number(e.target.value)) : undefined } })}
          min={24} max={80} style={{ flex: 1 }}
        />
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>px</span>
      </FieldRow>
      <FieldHint>
        Blank sizes the crosspoints to the element, between 44 and 72px. A number pins them.
      </FieldHint>
    </>
  );
}

/** One axis as the list it already is, or nothing while it is a generator. */
function matrixList(config: MatrixConfig, axis: MatrixAxis): unknown[] {
  const spec = config?.[axis];
  return Array.isArray(spec) ? spec : [];
}

function matrixAxisIsList(config: MatrixConfig, axis: MatrixAxis): boolean {
  return Array.isArray(config?.[axis]);
}

/** Whether any written-out destination carries an audio-route key.
 *
 *  The generator's key has a field of its own; a list has one per entry, and
 *  the setup dialog copies the keys back out on Apply, so this is the one place
 *  a matrix set up with the readout on can have it taken off. */
function matrixListHasAudio(config: MatrixConfig): boolean {
  return matrixList(config, "destinations").some(
    (entry) =>
      !!entry && typeof entry === "object" &&
      !!(entry as Record<string, unknown>).audio_route_key,
  );
}

/** The same list with every entry's audio-route key dropped. */
function matrixDropListAudio(config: MatrixConfig): Record<string, unknown> {
  const destinations = matrixList(config, "destinations").map((entry) => {
    if (!entry || typeof entry !== "object") return entry;
    const { audio_route_key: _dropped, ...rest } = entry as Record<string, unknown>;
    return rest;
  });
  return { ...config, destinations };
}

/** Whether any destination's lock would be this panel's own memory.
 *
 *  A lock nothing stores is forgotten the next time the page draws and is
 *  invisible to every other panel, which is what made it decorative. The page
 *  review says the same thing at the project level; this is the same fact
 *  beside the checkbox that turns it on. */
function matrixLockUnbacked(config: MatrixConfig): boolean {
  return resolveMatrixAxis(config, "destinations").some(
    (entry) => !MATRIX_PANEL_WRITABLE_PREFIXES.some(
      (prefix) => String(entry.lock_key ?? "").startsWith(prefix)),
  );
}

/** The generator behind one axis, or an empty one to start from. */
function matrixFrom(config: MatrixConfig, axis: MatrixAxis): Record<string, unknown> {
  const spec = config?.[axis];
  const from = isPlainObject(spec) ? spec.from : undefined;
  return isPlainObject(from) ? from : {};
}

/** That generator with some fields changed, in a whole new matrix_config. */
function setMatrixFrom(
  config: MatrixConfig,
  axis: MatrixAxis,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const spec = config?.[axis];
  const outer = isPlainObject(spec) ? spec : {};
  const from: Record<string, unknown> = { ...matrixFrom(config, axis), ...patch };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) delete from[key];
  }
  return { ...config, [axis]: { ...outer, from } };
}

/** That generator resized: the count held to 1..32, the labels padded or cut to match. */
function withGeneratorCount(
  config: MatrixConfig,
  axis: MatrixAxis,
  wanted: number,
  prefix: string,
): Record<string, unknown> {
  const count = Math.max(1, Math.min(32, wanted));
  const from = matrixFrom(config, axis);
  const labels = (Array.isArray(from.labels) ? [...from.labels] : []) as string[];
  while (labels.length < count) labels.push(`${prefix} ${labels.length + 1}`);
  return setMatrixFrom(config, axis, { count, labels: labels.slice(0, count) });
}

function MatrixLabelEditor({
  title,
  labels,
  onChange,
}: {
  title: string;
  labels: string[];
  onChange: (labels: string[]) => void;
}) {
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {labels.map((label, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ fontSize: 10, color: "var(--text-muted)", width: 16, textAlign: "right", flexShrink: 0 }}>{i + 1}</span>
            <input
              value={label}
              onChange={(e) => {
                const updated = [...labels];
                updated[i] = e.target.value;
                onChange(updated);
              }}
              style={{ flex: 1, padding: "2px 6px", fontSize: 11 }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function MatrixPresetsEditor({
  presets,
  macros,
  onChange,
}: {
  presets: MatrixPreset[];
  macros: { id: string; name: string }[];
  onChange: (presets: MatrixPreset[]) => void;
}) {
  const rowKeys = useRowKeys(presets.length);
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>Presets</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {presets.map((preset, i) => (
          <div key={rowKeys.keys[i]} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input
              value={preset.name || ""}
              onChange={(e) => {
                const updated = [...presets];
                updated[i] = { ...updated[i], name: e.target.value };
                onChange(updated);
              }}
              placeholder="Preset name"
              style={{ flex: 1, padding: "2px 6px", fontSize: 11 }}
            />
            <select
              value={preset.macro || ""}
              onChange={(e) => {
                const updated = [...presets];
                updated[i] = { ...updated[i], macro: e.target.value || undefined };
                onChange(updated);
              }}
              style={{ flex: 1, fontSize: 11, padding: "2px 4px" }}
            >
              <option value="">No macro</option>
              {macros.map((m) => (
                <option key={m.id} value={m.id}>{m.name || m.id}</option>
              ))}
            </select>
            <button
              onClick={() => {
                rowKeys.removeAt(i);
                onChange(presets.filter((_, j) => j !== i));
              }}
              style={{
                padding: "1px 5px", border: "1px solid var(--border-color)",
                borderRadius: 3, background: "transparent", color: "var(--color-error)",
                fontSize: 11, cursor: "pointer", flexShrink: 0,
              }}
            >
              <X size={10} />
            </button>
          </div>
        ))}
        <button
          onClick={() => onChange([...presets, { name: `Preset ${presets.length + 1}` }])}
          style={{
            padding: "3px 10px", borderRadius: "var(--border-radius)",
            border: "1px dashed var(--border-color)", background: "transparent",
            color: "var(--text-muted)", fontSize: 11, cursor: "pointer",
            alignSelf: "flex-start",
          }}
        >
          <Plus size={10} /> Add Preset
        </button>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", fontStyle: "italic", marginTop: 2 }}>
        Presets appear as buttons above the matrix. Each runs a macro when clicked.
      </div>
    </div>
  );
}
