import type {
  ActionCondition,
  ActionVisibleWhen,
  DeviceAction,
} from "../../api/types";

/**
 * Evaluate a single condition operator. Mirrors the panel runtime's
 * `_evalConditionOp` (web/panel/panel.js) and the shared backend evaluator
 * (server/core/condition_eval.py) so an action's `visible_when` behaves the
 * same wherever it's evaluated.
 */
function evalOp(op: string, actual: unknown, target: unknown): boolean {
  switch (op) {
    case "eq":
    case "equals":
    case "==":
      // Loose compare so a numeric 1 / boolean true matches a string "1"/"true".
      // eslint-disable-next-line eqeqeq
      return actual == target;
    case "ne":
    case "not_equals":
    case "!=":
      // eslint-disable-next-line eqeqeq
      return actual != target;
    case "gt":
    case ">":
      return actual != null && target != null && (actual as number) > (target as number);
    case "lt":
    case "<":
      return actual != null && target != null && (actual as number) < (target as number);
    case "gte":
    case ">=":
      return actual != null && target != null && (actual as number) >= (target as number);
    case "lte":
    case "<=":
      return actual != null && target != null && (actual as number) <= (target as number);
    case "truthy":
      return !!actual;
    case "falsy":
      return !actual;
    default:
      return false;
  }
}

function checkCondition(
  cond: ActionCondition,
  state: Record<string, unknown>,
  deviceId: string,
): boolean {
  if (!cond || typeof cond.key !== "string") return false;
  // `$id` in a condition key refers to this device, so a driver can author
  // visibility against its own state without knowing the per-instance id.
  const key = cond.key.split("$id").join(deviceId);
  return evalOp(cond.operator || "eq", state[key], cond.value);
}

/**
 * Evaluate an action's `visible_when`. Supports a single {key, operator, value}
 * condition, or an {any:[...]} (OR) / {all:[...]} (AND) group. Missing/empty
 * visible_when is always visible.
 */
export function evalVisibleWhen(
  visibleWhen: ActionVisibleWhen | null | undefined,
  state: Record<string, unknown>,
  deviceId: string,
): boolean {
  if (!visibleWhen) return true;
  const group = visibleWhen as { any?: ActionCondition[]; all?: ActionCondition[] };
  if (Array.isArray(group.any)) {
    return group.any.some((c) => checkCondition(c, state, deviceId));
  }
  if (Array.isArray(group.all)) {
    return group.all.every((c) => checkCondition(c, state, deviceId));
  }
  return checkCondition(visibleWhen as ActionCondition, state, deviceId);
}

/**
 * Whether an action should appear given the device's connection state and its
 * `visible_when`. `availability` gates on connectivity ("online" hides while
 * offline, "offline" hides while online, "always" ignores it).
 */
export function isActionVisible(
  action: DeviceAction,
  connected: boolean,
  state: Record<string, unknown>,
  deviceId: string,
): boolean {
  if (action.availability === "online" && !connected) return false;
  if (action.availability === "offline" && connected) return false;
  return evalVisibleWhen(action.visible_when, state, deviceId);
}
