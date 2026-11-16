"""Startup order of the CLI entry point."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import main as main_module


def test_logging_is_configured_before_dpi_awareness_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ensure_dpi_awareness logs the level Windows applied (or that it refused).
    # That line only reaches nexus_autodl.log if the file sink already exists.
    calls: list[str] = []
    monkeypatch.setattr(
        main_module, "configure_logging", lambda verbose: calls.append("logging")
    )
    monkeypatch.setattr(
        main_module, "ensure_dpi_awareness", lambda: calls.append("dpi")
    )
    monkeypatch.setattr(
        main_module, "run_app", lambda config, simulate: calls.append("run")
    )

    result = CliRunner().invoke(main_module.main, ["--simulate"])

    assert result.exit_code == 0, result.output
    assert calls == ["logging", "dpi", "run"]
