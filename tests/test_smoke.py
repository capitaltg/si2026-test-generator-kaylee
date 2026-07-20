"""Smoke tests: prove the scaffold's two doors both open.

"Smoke test" just means the most basic check that the thing turns on at all,
before we test any real behavior.
"""
from __future__ import annotations

from testgen import generate
from testgen.cli import main


def test_library_door_generate_is_callable():
    result = generate(rows=5, seed=42)
    assert result["rows"] == 5
    assert result["seed"] == 42


def test_cli_door_runs_and_returns_success_code():
    # main() returns 0 on success, like a real command-line program.
    assert main(["--rows", "3", "--seed", "42"]) == 0
