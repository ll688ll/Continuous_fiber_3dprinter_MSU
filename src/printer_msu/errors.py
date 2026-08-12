"""Exception hierarchy of the printer application (printer PRD §5)."""

from __future__ import annotations


class PrinterError(Exception):
    """Base for all printer_msu errors."""


class ProfileError(PrinterError):
    """Machine profile is invalid or unreadable."""


class GcodeError(PrinterError):
    """A G-code problem tied to a source line."""

    def __init__(self, message: str, line_no: int | None = None) -> None:
        super().__init__(f"line {line_no}: {message}" if line_no else message)
        self.line_no = line_no


class PreflightError(PrinterError):
    """Preflight found error-level findings; the job must not start."""


class JobError(PrinterError):
    """A print job failed at runtime."""
