import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

// A new driver's warnings are held back until its first Save, then shown.
// What shipped broken: the first Save turns the draft into a saved driver,
// and the editor dropped the platform's issue list on that change as if
// another driver had been opened. Nothing asked again until the draft was
// edited, so the warnings appeared only after reopening the driver.

const validateDriverDefinition = vi.fn();
vi.mock("../../api/restClient", () => ({
  validateDriverDefinition: (...args: unknown[]) => validateDriverDefinition(...args),
}));

import { DriverEditor } from "./DriverEditor";
import type { DriverDefinition } from "../../api/types";

const WARNING = "Command 'set_level' parameter 'lvl' has no label.";

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
  commands: {},
  polling: { interval: 5, queries: ["Q1\r", "Q2\r", "Q3\r"] },
} as unknown as DriverDefinition;

function editor(props: { isNew: boolean; originalId: string | null; draft?: DriverDefinition }) {
  return (
    <DriverEditor
      draft={props.draft ?? DRAFT}
      dirty={props.isNew}
      saving={false}
      error={null}
      isNew={props.isNew}
      readOnly={false}
      originalId={props.originalId}
      commentLines={0}
      addedConnectionFields={[]}
      onUpdate={() => {}}
      onSave={() => {}}
      onExport={() => {}}
      onDuplicate={() => {}}
    />
  );
}

/** Past the editor's validation debounce, with the reply delivered. */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 450));
  });
}

beforeEach(() => {
  validateDriverDefinition.mockReset();
  validateDriverDefinition.mockResolvedValue({
    valid: true,
    issues: [{ severity: "warning", path: "", message: WARNING }],
  });
});

describe("Driver Builder issues on a new driver", () => {
  it("shows the warnings once the first save turns the draft into a saved driver", async () => {
    const { rerender } = render(editor({ isNew: true, originalId: null }));
    await settle();
    expect(validateDriverDefinition).toHaveBeenCalledTimes(1);
    // Held back while the draft is new and unsaved.
    expect(screen.queryByText(WARNING)).toBeNull();

    // The first save: same draft, now under its own id.
    rerender(editor({ isNew: false, originalId: "acme_widget" }));
    await waitFor(() => expect(screen.getByText(WARNING)).toBeTruthy(), { timeout: 1500 });
  });

  it("drops the previous driver's warnings when another driver is opened", async () => {
    const { rerender } = render(editor({ isNew: false, originalId: "acme_widget" }));
    await waitFor(() => expect(screen.getByText(WARNING)).toBeTruthy(), { timeout: 1500 });

    validateDriverDefinition.mockReturnValue(new Promise(() => {}));
    const other = { ...DRAFT, id: "acme_other", name: "Acme Other" } as DriverDefinition;
    rerender(editor({ isNew: false, originalId: "acme_other", draft: other }));
    expect(screen.queryByText(WARNING)).toBeNull();
  });
});

describe("Driver Builder section counts", () => {
  it("says queries, not querys", async () => {
    render(editor({ isNew: false, originalId: "acme_widget" }));
    fireEvent.click(screen.getByRole("button", { name: "Behavior" }));
    expect(screen.getByText("3 queries")).toBeTruthy();
    expect(screen.queryByText("3 querys")).toBeNull();
    await settle();
  });
});
