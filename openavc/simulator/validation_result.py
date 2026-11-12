"""What the simulator validator reports: one ``Issue`` per finding, collected per driver.

Every check takes a ``ValidationResult`` and adds to it. The two types sit in
a module of their own, importing nothing, so a check can live beside the
part of the simulator it judges without importing ``validate`` (which
imports the checks). ``validate`` re-exports both names, the import path
its callers use.
"""

from __future__ import annotations


class Issue:
    """A single validation issue."""

    def __init__(self, severity: str, check: str, message: str):
        self.severity = severity  # "error" | "warning" | "info"
        self.check = check  # e.g., "state_coverage", "command_coverage"
        self.message = message

    def __str__(self) -> str:
        if self.severity == "error":
            icon = "ERROR"
        elif self.severity == "warning":
            icon = "WARN "
        else:
            icon = "INFO "
        return f"  {icon} [{self.check}] {self.message}"


class ValidationResult:
    """Validation result for a single driver."""

    def __init__(self, driver_path: str, driver_id: str, driver_type: str):
        self.driver_path = driver_path
        self.driver_id = driver_id
        self.driver_type = driver_type  # "yaml" or "python"
        self.issues: list[Issue] = []

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def infos(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "info"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def error(self, check: str, message: str) -> None:
        self.issues.append(Issue("error", check, message))

    def warning(self, check: str, message: str) -> None:
        self.issues.append(Issue("warning", check, message))

    def info(self, check: str, message: str) -> None:
        self.issues.append(Issue("info", check, message))
