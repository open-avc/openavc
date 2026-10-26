import { useMemo } from "react";
import { childReadingDeclarations, liveChildNames } from "../../api/childStateVars";
import type { ChildEntitiesListResponse, ChildEntityStateVarDef } from "../../api/types";

/** One device's child-reading declarations, each child named from live state.
 *
 *  The names are read on every live-state tick, two lookups a child; the
 *  declarations, which a loaded controller counts in tens of thousands, are
 *  rebuilt only when a name actually changes. The Child Entities panel and the
 *  State tab both author monitors against these, so both read them here. */
export function useChildReadingDeclarations(
  resp: ChildEntitiesListResponse | null,
  deviceId: string,
  liveState: Record<string, unknown>,
): Map<string, ChildEntityStateVarDef> {
  const namesKey = useMemo(
    () => JSON.stringify([...liveChildNames(resp, liveState, deviceId)]),
    [resp, liveState, deviceId],
  );
  return useMemo(
    () => childReadingDeclarations(
      resp, new Map(JSON.parse(namesKey) as [string, string][]),
    ),
    [resp, namesKey],
  );
}
