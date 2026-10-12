import { describe, expect, it } from "vitest";
import type { DriverDefinition } from "../../api/types";
import type { ValidationIssue } from "./validateDriver";
import { saveErrorText, type SaveRefusal } from "./saveRefusal";

const TABS = [
  { id: "general", label: "General" },
  { id: "connection", label: "Connection" },
  { id: "behavior", label: "Behavior" },
];

const SEND = "commands.command_1.send: '{lvel}' resolves to nothing (did you mean 'lvl'?).";
const PORT = "default_config.port: must be a number";
const TEXT = `The driver was not saved. Fix this first:\n${SEND}`;

function draft(): DriverDefinition {
  return { id: "acme_amp", name: "Acme Amp" } as DriverDefinition;
}

function issue(message: string, section: ValidationIssue["section"], severity: ValidationIssue["severity"] = "error"): ValidationIssue {
  return { message, section, severity };
}

function refusal(d: DriverDefinition, errors = [SEND], text = TEXT): SaveRefusal {
  return { text, errors, draft: d };
}

describe("the header after a refused save", () => {
  it("says where to look when the issue list already shows the error", () => {
    const d = draft();
    expect(saveErrorText(TEXT, refusal(d), d, [issue(SEND, "behavior")], TABS)).toBe(
      "The driver was not saved. Fix the error on the Behavior tab.",
    );
  });

  it("names every tab that holds one, in tab order", () => {
    const d = draft();
    const text = `The driver was not saved. Fix these first:\n${SEND}\n${PORT}`;
    const issues = [issue(SEND, "behavior"), issue(PORT, "connection")];
    expect(saveErrorText(text, refusal(d, [SEND, PORT], text), d, issues, TABS)).toBe(
      "The driver was not saved. Fix the errors on the Connection and Behavior tabs.",
    );
  });

  it("keeps the whole refusal while the list does not show it yet", () => {
    const d = draft();
    expect(saveErrorText(TEXT, refusal(d), d, [], TABS)).toBe(TEXT);
    expect(saveErrorText(TEXT, refusal(d), d, [issue(SEND, "behavior", "warning")], TABS)).toBe(TEXT);
  });

  it("drops the refusal once the driver has been edited", () => {
    const d = draft();
    expect(saveErrorText(TEXT, refusal(d), { ...d }, [], TABS)).toBeNull();
  });

  it("shows any other error as it is", () => {
    const d = draft();
    expect(saveErrorText("ID and Name are required", null, d, [], TABS)).toBe("ID and Name are required");
    expect(saveErrorText("Network error", refusal(d), d, [issue(SEND, "behavior")], TABS)).toBe(
      "Network error",
    );
    expect(saveErrorText(null, refusal(d), d, [], TABS)).toBeNull();
  });
});
