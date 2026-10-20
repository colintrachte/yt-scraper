import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_launcher_translates_url_and_limit(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "test_bootstrap_cli", ROOT / "helper_scripts" / "bootstrap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ensure_environment", lambda *_: Path(sys.executable))
    run = MagicMock(return_value=type("Result", (), {"returncode": 0})())
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["bootstrap.py", "https://www.youtube.com/watch?v=abcdefghijk", "7"],
    )
    assert module.main() == 0
    command = run.call_args.args[0]
    assert command[-2:] == ["--limit", "7"]
    assert "--urls" in command
    assert "--no-venv" in command


def test_main_cli_preserves_video_limit(monkeypatch):
    import main

    monkeypatch.setattr(main, "_missing", list)
    archive = MagicMock()
    monkeypatch.setattr(main, "archive_job", archive)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--no-venv",
            "--urls",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "--limit",
            "7",
        ],
    )
    main.main_cli()
    assert archive.call_args.args[1]["limit"] == 7


def test_invalid_limit_is_rejected_before_setup(monkeypatch):
    import main

    monkeypatch.setattr(
        main, "_ensure_venv", lambda: pytest.fail("Invalid input attempted setup")
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--limit", "-1"])
    with pytest.raises(SystemExit) as error:
        main.main_cli()
    assert error.value.code == 2


def test_missing_runtime_dependency_does_not_install_globally(monkeypatch):
    import main

    monkeypatch.setattr(main, "_missing", lambda: ["pillow"])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Runtime attempted installation"),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-venv"])
    with pytest.raises(SystemExit) as error:
        main.main_cli()
    assert error.value.code == 1
