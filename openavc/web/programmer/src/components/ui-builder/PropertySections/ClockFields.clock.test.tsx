import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { UIElement } from "../../../api/types";
import { ClockFields } from "./ClockFields";

// The Format box's placeholder is the format the panel draws for a clock left
// blank: the project schema's, by the clock's mode. The three durations read no
// format at all, so they show none.

type Case = { name: string; mode?: unknown; expected: string | null };

// The same table tests/test_clock_format_parity.py runs through the project
// schema and the panel. Read off disk: it lives outside this Vite project, and
// vitest runs with openavc/web/programmer as the cwd.
const casesPath = resolve(process.cwd(), "../../../tests/fixtures/clock_format_cases.json");
const cases: Case[] = JSON.parse(readFileSync(casesPath, "utf-8")).cases;

function formatBox(element: UIElement): HTMLInputElement {
  const { container } = render(<ClockFields element={element} onChange={() => {}} />);
  const label = [...container.querySelectorAll("label")].find((n) => n.textContent === "Format");
  const input = label?.parentElement?.querySelector("input");
  if (!input) throw new Error("no Format box");
  return input;
}

describe("the clock's Format placeholder", () => {
  it.each(cases)("$name", ({ mode, expected }) => {
    const element = { id: "c", type: "clock", ...(mode === undefined ? {} : { clock_mode: mode }) } as UIElement;
    const box = formatBox(element);
    expect(box.getAttribute("placeholder")).toBe(expected);
    expect(box.value).toBe("");
  });

  it("is a format the author wrote, shown as written", () => {
    const box = formatBox({ id: "c", type: "clock", clock_mode: "date", format: "D MMM" } as UIElement);
    expect(box.value).toBe("D MMM");
  });
});
