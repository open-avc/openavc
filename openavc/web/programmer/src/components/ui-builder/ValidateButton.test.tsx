import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ValidateButton, issueBadge } from "./ValidateButton";

describe("the Validate button", () => {
  it("carries the number of issues Validate would list", () => {
    render(<ValidateButton onValidate={() => {}} issueCount={3} />);
    expect(screen.getByTestId("validate-issue-count")).toHaveTextContent("3");
    expect(screen.getByLabelText("3 issues")).toBeInTheDocument();
  });

  it("carries nothing for a clean project, or before the count is known", () => {
    for (const count of [0, null]) {
      const { unmount } = render(<ValidateButton onValidate={() => {}} issueCount={count} />);
      expect(screen.queryByTestId("validate-issue-count")).toBeNull();
      unmount();
    }
  });

  it("keeps its name and opens the report when pressed", () => {
    const onValidate = vi.fn();
    render(<ValidateButton onValidate={onValidate} issueCount={1} />);
    fireEvent.click(screen.getByTitle("Validate project for broken references"));
    expect(onValidate).toHaveBeenCalledTimes(1);
  });

  it("caps a long count", () => {
    expect(issueBadge(120)).toBe("99+");
    expect(issueBadge(1)).toBe("1");
  });
});
