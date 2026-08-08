// Vitest setup: register jest-dom matchers (toBeInTheDocument, …) and clean the
// rendered DOM between tests so each case starts from a blank document.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
