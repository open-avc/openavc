/**
 * What each device calls its commands, for a step's one-line summary:
 * "Projector → Power On" rather than "Projector → power_on".
 *
 * Read from the same device info the step's Command list is built from
 * (`GET /api/devices/{id}`), because a Generic device's commands are defined on
 * the device itself rather than in a driver. Fetched once per device while the
 * macro editor is open, and shared through a context with every step row,
 * nested ones included. A device that cannot be read, or a command with no
 * label, keeps the command's id.
 */
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import * as api from "../../api/restClient";
import type { MacroStep } from "../../api/types";

/** A command's label on a device, or undefined when there is none to show. */
export type CommandLabelOf = (deviceId: string | undefined, command: string | undefined) => string | undefined;

/** The command labels one device's info declares, by command id. */
export function commandLabelsOf(commands: Record<string, unknown> | undefined): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const [id, def] of Object.entries(commands ?? {})) {
    const label = (def as { label?: unknown } | null)?.label;
    if (typeof label === "string" && label.trim()) labels[id] = label.trim();
  }
  return labels;
}

/** Every device a device command step names, branches included. */
export function stepDeviceIds(steps: MacroStep[] | undefined): string[] {
  const ids = new Set<string>();
  const walk = (list: MacroStep[] | undefined) => {
    for (const step of list ?? []) {
      if (step.action === "device.command" && step.device) ids.add(step.device);
      walk(step.then_steps);
      walk(step.else_steps);
    }
  };
  walk(steps);
  return [...ids].sort();
}

/** Fetch the command labels of these devices; answer lookups from them. */
export function useCommandLabels(deviceIds: string[]): CommandLabelOf {
  const [labels, setLabels] = useState<Record<string, Record<string, string>>>({});
  const key = deviceIds.join("\n");

  useEffect(() => {
    let stale = false;
    for (const id of key ? key.split("\n") : []) {
      api
        .getDevice(id)
        .then((info) => {
          if (!stale) setLabels((prev) => ({ ...prev, [id]: commandLabelsOf(info.commands) }));
        })
        .catch(() => {
          // No labels for this device: its steps keep the command's id.
        });
    }
    return () => {
      stale = true;
    };
  }, [key]);

  return useCallback(
    (deviceId, command) => (deviceId && command ? labels[deviceId]?.[command] : undefined),
    [labels],
  );
}

/** The lookup the macro editor provides to its step rows. */
export const CommandLabelContext = createContext<CommandLabelOf>(() => undefined);

export function useCommandLabel(): CommandLabelOf {
  return useContext(CommandLabelContext);
}
