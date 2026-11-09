/**
 * The parts of a device's config edited on its page rather than in the Edit
 * dialog, each drawn only when the driver asks for it: the no-code protocol
 * editor (`inline_protocol: true`, the generic devices), the IR code set
 * (`ir_codes: true`), and a row editor for each setting declared
 * `type: "table"` (a register map, a scene list), found from the schema rather
 * than a flag of its own. Each writes into the device config and says so
 * through `onSaved`, which reads the device again.
 *
 * The page only places the group, between Device Settings and Send Command. A
 * new editor of this kind is one more entry here, plus one in the Edit dialog
 * (DeviceDialogs.tsx) when its field would otherwise show there as a raw box:
 * the dialog leaves `ir_codes` out (`hiddenRawConfigKeys`), and a table setting
 * says to set it up on the device page.
 */
import { InlineProtocolEditor } from "./InlineProtocolEditor";
import { IrCodesEditor } from "./IrCodesEditor";
import { ConfigTableEditor } from "./ConfigTableEditor";

/** The settings the driver declares as tables, in the order it lists them. */
function tableFields(driverInfo: Record<string, unknown>): [string, Record<string, unknown>][] {
  const schema = (driverInfo.config_schema ?? {}) as Record<string, Record<string, unknown>>;
  return Object.entries(schema).filter(([, field]) => field?.type === "table");
}

export function DeviceConfigEditors({
  deviceId,
  driverInfo,
  connected,
  onSaved,
}: {
  deviceId: string;
  /** The running driver's declaration (DeviceInfo.driver_info), if known yet. */
  driverInfo: Record<string, unknown> | undefined;
  connected: boolean;
  onSaved: () => void;
}) {
  if (!driverInfo) return null;
  return (
    <>
      {driverInfo.inline_protocol === true && (
        <InlineProtocolEditor
          deviceId={deviceId}
          driverInfo={driverInfo}
          connected={connected}
          onSaved={onSaved}
        />
      )}
      {driverInfo.ir_codes === true && (
        <IrCodesEditor deviceId={deviceId} connected={connected} onSaved={onSaved} />
      )}
      {tableFields(driverInfo).map(([key, field]) => (
        <ConfigTableEditor
          key={key}
          deviceId={deviceId}
          fieldKey={key}
          fieldSchema={field}
          connected={connected}
          onSaved={onSaved}
        />
      ))}
    </>
  );
}
