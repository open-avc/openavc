import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A refused save named its error in the header, and the Behavior tab's issue
// list showed the same error beneath it. The header now says where to look.

const validateDriverDefinition = vi.fn();
vi.mock("../../api/restClient", () => ({
  validateDriverDefinition: (...args: unknown[]) => validateDriverDefinition(...args),
}));

import { DriverEditor } from "./DriverEditor";
import type { DriverDefinition } from "../../api/types";
import type { SaveRefusal } from "./saveRefusal";

const ERROR =
  "commands.command_1.send: '{lvel}' resolves to nothing (did you mean 'lvl'?). It names no " +
  "parameter of this command and no config field, so it would be sent to the device as written";
const REFUSED = `The driver was not saved. Fix this first:\n${ERROR}`;
const SHORT = "The driver was not saved. Fix the error on the Behavior tab.";

const DRAFT = {
  id: "acme_widget",
  name: "Acme Widget",
  manufacturer: "Acme",
  category: "utility",
  version: "1.0.0",
  transport: "tcp",
  delimiter: "\r",
  default_config: { port: 4000 },
  config_schema: {},
  state_variables: {},
  responses: [],
  commands: { command_1: { label: "Level", send: "LVL {lvel}\r", params: { lvl: { type: "integer" } } } },
} as unknown as DriverDefinition;

function editor(draft: DriverDefinition, refusal: SaveRefusal | null) {
  return (
    <DriverEditor
      draft={draft}
      dirty={false}
      saving={false}
      error={REFUSED}
      saveRefusal={refusal}
      isNew={false}
      readOnly={false}
      originalId="acme_widget"
      commentLines={0}
      addedConnectionFields={[]}
      onUpdate={() => {}}
      onSave={() => {}}
      onExport={() => {}}
      onDuplicate={() => {}}
    />
  );
}

beforeEach(() => {
  validateDriverDefinition.mockReset();
  validateDriverDefinition.mockResolvedValue({
    valid: false,
    issues: [{ severity: "error", path: "commands.command_1.send", message: ERROR }],
  });
});

describe("a refused save in the Driver Builder", () => {
  it("says the problem once: the header names the tab, the tab lists the error", async () => {
    render(editor(DRAFT, { text: REFUSED, errors: [ERROR], draft: DRAFT }));
    // The whole refusal until the issue list holds the same error.
    expect(screen.getByText(/Fix this first/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(SHORT)).toBeTruthy(), { timeout: 1500 });
    expect(screen.queryByText(/Fix this first/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Behavior" }));
    expect(screen.getAllByText(ERROR, { exact: false })).toHaveLength(1);
  });

  it("clears the header once the driver has been edited", async () => {
    const { rerender } = render(editor(DRAFT, { text: REFUSED, errors: [ERROR], draft: DRAFT }));
    await waitFor(() => expect(screen.getByText(SHORT)).toBeTruthy(), { timeout: 1500 });
    rerender(editor({ ...DRAFT }, { text: REFUSED, errors: [ERROR], draft: DRAFT }));
    expect(screen.queryByText(SHORT)).toBeNull();
    expect(screen.queryByText(/Fix this first/)).toBeNull();
  });
});
