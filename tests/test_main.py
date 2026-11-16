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


def test_scaled_coordinates_stop_the_app_with_exit_code_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utils.dpi import CoordinatesScaledError

    def refuse(config, simulate):
        raise CoordinatesScaledError(["DISPLAY1 is 1920x1080 but reported as 1280x720"])

    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "ensure_dpi_awareness", lambda: None)
    monkeypatch.setattr(main_module, "run_app", refuse)

    result = CliRunner().invoke(main_module.main, ["--simulate"])

    assert result.exit_code == main_module.EXIT_COORDINATES_SCALED == 2


def test_app_refuses_to_start_when_windows_scales_its_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The scanner must never be built: it would capture and click with
    # coordinates that are off by the monitor's scale factor.
    import app as app_module
    from models import AppConfig
    from services import scanner as scanner_module
    from services import window_manager as wm_module
    from utils.dpi import CoordinatesScaledError

    monkeypatch.setattr(app_module, "IS_WINDOWS", True)
    monkeypatch.setattr(
        wm_module.WindowManager,
        "scaled_monitors",
        staticmethod(lambda: ["DISPLAY1 is 1920x1080 but reported as 1280x720"]),
    )

    def never(*args, **kwargs):
        raise AssertionError("Scanner must not be created")

    monkeypatch.setattr(scanner_module, "Scanner", never)

    with pytest.raises(CoordinatesScaledError, match="reported as 1280x720") as excinfo:
        app_module.run(AppConfig(), simulate=False)
    assert "python.exe" in str(excinfo.value)
