import { request } from "./base";

/**
 * What a device says about its own routing, so a matrix does not have to be
 * typed out from the manual.
 *
 * Everything a matrix element needs is already in the driver: which child type
 * holds the destinations, which property on each one holds the routed source,
 * which command routes. The inference lives on the server
 * (openavc/ui/matrix_inference.py) because it has to read the LIVE driver --
 * three shipped drivers build their declaration programmatically and a file
 * reader sees nothing in them, and only a running instance knows which ports
 * actually registered.
 *
 * A proposal is a proposal (matrix plan D3). The picker ticks, renames and
 * reorders it, and what it writes into the project is an explicit list (D5).
 */

/** One thing a matrix can route from. */
export interface ProposedSource {
  value: string | number;
  label: string;
  label_key?: string;
  /**
   * What the DEVICE calls this source, where that is not what its route command
   * accepts. Present only when the driver declares both vocabularies and they
   * differ -- `at_atdm_0604a` is routed by sending "0" and reports back "Mic".
   */
  report_value?: string | number;
  /**
   * The device lists this port but is not reaching it right now (the
   * platform-reserved child `online`). Live, so it is never written into the
   * project -- it says "this row cannot route today", not "leave it out".
   */
  offline?: boolean;
}

/** One thing a matrix can route to, with its own key for what is routed there. */
export interface ProposedDestination extends ProposedSource {
  route_key?: string;
  audio_route_key?: string;
  lock_key?: string;
  route?: Record<string, unknown>[];
}

export interface MatrixProposal {
  /** Stable within a device: "<child type>.<routed-source property>", with a
   *  plane suffix where a device routes two planes off one property (a combined
   *  "all streams" mode beside the individual ones). */
  id: string;
  device_id: string;
  /** What the picker shows, e.g. "Decoders \u00b7 Video". */
  label: string;
  destination_child_type: string;
  route_property: string;
  source_child_type: string | null;
  command: string | null;
  /** The command's own label ("Route Source to Display"), which is how the rest
   *  of the IDE names it; `command` is its wire id. */
  command_label: string;
  /** The id of the plane carrying this one's audio, where the device switches
   *  audio with its own command -- what the "move the audio with it" tick wires
   *  up. Null when audio is not routed separately (or this IS the audio plane). */
  audio_plane_id: string | null;
  confidence: "high" | "medium" | "low";
  /** True when the destinations are the ports that REGISTERED, false when they
   *  are the range the driver declares (an unconnected device). */
  from_roster: boolean;
  /** One sentence naming what was read, so the guess can be checked. */
  why: string;
  /** What this cannot know, said before it is applied rather than after. */
  warnings: string[];
  sources: ProposedSource[];
  destinations: ProposedDestination[];
  /** The Video route action list, ready for bindings.do.route. */
  route: Record<string, unknown>[] | null;
}

export interface MatrixProposalsResponse {
  device_id: string;
  /** True when at least one port actually registered. False means the lists
   *  come from what the driver DECLARES -- a configured but unconnected
   *  switcher has a driver object and an empty roster, and calling that live is
   *  how a 4x4 on the bench offers a convincing sixteen outputs. */
  live: boolean;
  proposals: MatrixProposal[];
}

export async function getMatrixProposals(
  deviceId: string,
): Promise<MatrixProposalsResponse> {
  return request<MatrixProposalsResponse>(
    `/ui/matrix-proposals/${encodeURIComponent(deviceId)}`,
  );
}
