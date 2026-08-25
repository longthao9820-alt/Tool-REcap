"""Bounded subprocess command runner."""

from __future__ import annotations

import subprocess
from typing import Sequence

from ...ports.command_runner import CommandResult


class SubprocessCommandRunner:
    def run(self, argv: Sequence[str], timeout_s: float) -> CommandResult:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
