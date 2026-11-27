import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * Two triggers in one macro with the same id: each still has its own card,
 * and the one that never fires says so when it is opened.
 */

const mocks = vi.hoisted(() => ({ testTrigger: vi.fn(async () => ({ status: "completed" })) }));
vi.mock("../../api/restClient", () => ({ testTrigger: mocks.testTrigger }));

import { TriggerList } from "./TriggerList";
import type { MacroIssue } from "./macroLint";

const TRIGGERS = [
  { id: "t1", type: "schedule", enabled: true, cron: "0 22 * * *" },
  { id: "t1", type: "schedule", enabled: true, cron: "0 6 * * *" },
];

const ISSUES: MacroIssue[] = [{
  scope: "trigger", index: 0, path: "triggers[0]",
  message: "trigger id 't1' is also the id of triggers[1]", action: "schedule",
  same_id_as: { index: 1 },
}];

describe("two triggers sharing an id", () => {
  it("draws a card for each, and the first says it never fires", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container } = render(
      <TriggerList triggers={TRIGGERS as never} issues={ISSUES} devices={[]} allMacros={[]} onUpdate={() => {}} />,
    );
    // No duplicate-key warning, which is React dropping or doubling a card.
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();

    const marks = container.querySelectorAll('[title^="Trigger 1: This trigger never fires"]');
    expect(marks).toHaveLength(1);

    // Opening the card (a click anywhere on its header) shows the sentence.
    await userEvent.click(marks[0]);
    expect(screen.getByText(
      "This trigger never fires because Trigger 2 has the same id. Delete this trigger and add it again.",
    )).toBeTruthy();
  });

  it("turns Fire now off on the trigger that never fires, which would run the other one", async () => {
    render(
      <TriggerList triggers={TRIGGERS as never} issues={ISSUES} devices={[]} allMacros={[]} onUpdate={() => {}} />,
    );
    const [first, second] = screen.getAllByRole("button", { name: /Fire now/ });
    expect((first as HTMLButtonElement).disabled).toBe(true);
    expect(first.getAttribute("title")).toBe("Fire now is off for a trigger that never fires. Delete it and add it again.");
    expect((second as HTMLButtonElement).disabled).toBe(false);

    await userEvent.click(first);
    expect(mocks.testTrigger).not.toHaveBeenCalled();
    await userEvent.click(second);
    expect(mocks.testTrigger).toHaveBeenCalledWith("t1");
  });
});
