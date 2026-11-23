/** The children of one type a parameter's live picker offers (ParamInput),
 *  and what each is called now.
 *
 *  Children register as the driver discovers them, so each picker fetches
 *  its own list. On a project device it fetches again once live state says
 *  the list itself moved (a child registering or going, or a label saved in
 *  the project, which the server mirrors into the child's `label`), and reads
 *  each child's name from live state in the server's order (`liveChildName`),
 *  as the Child Entities panel does: a device reports and changes its names
 *  after the fetch. The device audit's pickers (`ParamPickers`) read the
 *  audit's own children and stay as fetched. */
import { useEffect, useState } from "react";
import * as api from "../../api/restClient";
import { liveChildName } from "../../api/childStateVars";
import type { ChildEntityEntry, ChildEntityTypeSchema } from "../../api/types";
import { useConnectionStore } from "../../store/connectionStore";
import type { ParamPickers } from "./ParamInput";
import { useSettled } from "./useSettled";

/** Where a picker's children come from. */
export interface ChildSource {
  childType?: string;
  deviceId?: string;
  pickers?: ParamPickers;
}

export interface LiveChildren {
  /** The children, `undefined` while the first fetch is out. */
  children: ChildEntityEntry[] | undefined;
  /** What each child (by its padded id) is called now; empty when the
   *  picker does not follow a project device. "" for a child nothing names. */
  names: Map<string, string>;
}

interface Fetched {
  found: ChildEntityEntry[];
  schema?: ChildEntityTypeSchema;
}

function fetchChildren({ childType, deviceId, pickers }: ChildSource): Promise<Fetched> {
  if (pickers) return pickers.loadChildren(childType!).then((found) => ({ found }));
  return api.listChildEntitiesByType(deviceId!, childType!).then((resp) => ({
    found: resp.children,
    schema: resp.schema,
  }));
}

/** What live state says about one device's children of one type that a
 *  fetched list cannot follow by itself: how many keys they hold (a child
 *  registering or going) and each reserved `label`. A name the device
 *  reports is read live and needs no new fetch, so it is not part of this. */
export function childRosterStamp(liveState: Record<string, unknown>, prefix: string): string {
  let count = 0;
  const labels: string[] = [];
  for (const [key, value] of Object.entries(liveState)) {
    if (!key.startsWith(prefix)) continue;
    count++;
    if (isLabelKey(key, prefix)) labels.push(`${key.slice(prefix.length)}=${String(value)}`);
  }
  return `${count}\u0000${labels.join("\u0000")}`;
}

/** `<prefix><padded id>.label`: a child's own reserved label, not a deeper key. */
function isLabelKey(key: string, prefix: string): boolean {
  return key.endsWith(".label") && key.indexOf(".", prefix.length) === key.length - 6;
}

/** Every child's live name, one string, so a tick that names nothing
 *  redraws nothing. */
function namesKey(
  children: ChildEntityEntry[],
  typeDef: ChildEntityTypeSchema | undefined,
  liveState: Record<string, unknown>,
  prefix: string,
): string {
  return children.map((c) => liveChildName(c, typeDef, liveState, prefix)).join("\u0000");
}

function namesOf(children: ChildEntityEntry[] | undefined, key: string): Map<string, string> {
  const out = new Map<string, string>();
  if (!key || !children) return out;
  const names = key.split("\u0000");
  children.forEach((c, i) => out.set(c.local_id_padded, names[i] ?? ""));
  return out;
}

export function useLiveChildren(source: ChildSource): LiveChildren {
  const { childType, deviceId, pickers } = source;
  const [children, setChildren] = useState<ChildEntityEntry[] | undefined>(undefined);
  const [typeDef, setTypeDef] = useState<ChildEntityTypeSchema | undefined>(undefined);
  const prefix = childType && deviceId && !pickers ? `device.${deviceId}.${childType}.` : "";
  const stamp = useSettled(
    useConnectionStore((s) => (prefix ? childRosterStamp(s.liveState, prefix) : "")),
    400,
  );
  const reachable = !!deviceId || !!pickers;
  useEffect(() => {
    if (!childType || !reachable) return;
    let cancelled = false;
    fetchChildren(source)
      .then(({ found, schema }) => {
        if (cancelled) return;
        setChildren(found);
        setTypeDef(schema);
      })
      .catch(() => {
        if (!cancelled) setChildren([]);
      });
    return () => {
      cancelled = true;
    };
    // `pickers` is read at load time; a new object for the same source must
    // not refetch on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [childType, deviceId, reachable, stamp]);
  const key = useConnectionStore((s) =>
    prefix && children ? namesKey(children, typeDef, s.liveState, prefix) : "",
  );
  return { children, names: namesOf(children, key) };
}
