/**
 * The values a state key is declared to take, so the Appearance card can
 * offer a value the device has not reported yet.
 *
 * The list used to come from the live reading alone, which made a feedback
 * binding for any OTHER value impossible to author until the device had been
 * put into that state: a Wireless button could not be bound to `hdmi2` while
 * the display sat on HDMI 1. The driver declares what the key can be, and
 * this reads that declaration.
 *
 * For `device.<id>.<suffix>`, in order of authority:
 *   1. the state variable's own `values` (a declared enum), or `true` /
 *      `false` for a declared boolean;
 *   2. the enum of the command parameter that writes the key: a parameter
 *      named after the key on any command (`set_input(input)`), else the one
 *      enum parameter of `set_<key>` (`set_led_indicator(mode)`).
 * A child-entity key (`display.01.input`) resolves the child's own declared
 * variable first, then matches the command by the key's last segment.
 *
 * Values are the wire strings a panel compares against, never the labels.
 * `declaredValuesForKey` is pure over the payloads the device endpoint
 * already returns; `fetchDeclaredValues` is the one fetch.
 */
import * as api from "../../../api/restClient";
import { childVarDefForSuffix } from "../../../api/childStateVars";
import type { EnumOption } from "../../../api/types";

interface DeclaredVar {
  type?: string;
  values?: ReadonlyArray<EnumOption | string>;
}
interface DeclaredParam {
  type?: string;
  values?: ReadonlyArray<EnumOption | string>;
}
interface DeclaredCommand {
  params?: Record<string, DeclaredParam>;
}

export interface DriverDeclaration {
  state_variables?: Record<string, DeclaredVar>;
  commands?: Record<string, unknown>;
}

/** The wire value of each option, whether declared bare or as {value, label}. */
export function enumOptionValues(
  values: ReadonlyArray<EnumOption | string> | undefined,
): string[] {
  if (!Array.isArray(values)) return [];
  const out: string[] = [];
  for (const v of values) {
    if (v && typeof v === "object") {
      if ("value" in v) out.push(String(v.value));
    } else if (v !== undefined && v !== null) {
      out.push(String(v));
    }
  }
  return out;
}

function valuesOfVar(def: DeclaredVar | null | undefined): string[] {
  if (!def) return [];
  const declared = enumOptionValues(def.values);
  if (declared.length > 0) return declared;
  if (def.type === "boolean") return ["true", "false"];
  return [];
}

function commandParamValues(
  commands: Record<string, unknown> | undefined,
  prop: string,
): string[] {
  if (!commands) return [];
  // A parameter named after the key, on any command: set_input(input).
  for (const cmd of Object.values(commands)) {
    const params = (cmd as DeclaredCommand | undefined)?.params;
    const vals = enumOptionValues(params?.[prop]?.values);
    if (vals.length > 0) return vals;
  }
  // Else the one enum parameter of set_<key>: set_led_indicator(mode). Two
  // enum parameters say nothing about which one is the key, so offer neither.
  const setter = commands[`set_${prop}`] as DeclaredCommand | undefined;
  const enumParams = Object.values(setter?.params ?? {}).filter(
    (p) => enumOptionValues(p?.values).length > 0,
  );
  if (enumParams.length === 1) return enumOptionValues(enumParams[0].values);
  return [];
}

export function declaredValuesForKey(
  declaration: DriverDeclaration | undefined,
  suffix: string,
  childDef?: DeclaredVar | null,
): string[] {
  const direct = declaration?.state_variables?.[suffix];
  const own = valuesOfVar(direct ?? childDef);
  if (own.length > 0) return own;
  const prop = suffix.split(".").pop() ?? suffix;
  return commandParamValues(declaration?.commands, prop);
}

/** Declared values for `device.<deviceId>.<suffix>`, from the device
 *  endpoint. Never throws: an unreachable endpoint means no declared list,
 *  and the editor falls back to the live reading alone. */
export async function fetchDeclaredValues(
  deviceId: string,
  suffix: string,
): Promise<string[]> {
  let info: Awaited<ReturnType<typeof api.getDevice>>;
  try {
    info = await api.getDevice(deviceId);
  } catch {
    return [];
  }
  const driver = (info.driver_info ?? {}) as DriverDeclaration;
  const declaration: DriverDeclaration = {
    state_variables: driver.state_variables,
    commands: driver.commands ?? info.commands,
  };
  let childDef: DeclaredVar | null = null;
  if (!declaration.state_variables?.[suffix] && suffix.split(".").length >= 3) {
    const kids = await api.listChildEntities(deviceId).catch(() => null);
    childDef = childVarDefForSuffix(kids, suffix);
  }
  return declaredValuesForKey(declaration, suffix, childDef);
}
