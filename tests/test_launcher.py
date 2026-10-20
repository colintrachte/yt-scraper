"""Offline launcher regressions: no browser, mailbox access, or downloads."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from helper_scripts import launch_support as support

SERVER = """
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"app": "TestApp", "root": str(Path(__file__).resolve().parent)}).encode())
    def log_message(self, *args):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


def test_dependency_versions_and_missing_packages(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pip>=0\n", encoding="utf-8")
    assert support.check_environment(Path(sys.executable), requirements, (3, 10))[0]
    requirements.write_text(
        "nonexistent-launcher-test-package==1.0\n", encoding="utf-8"
    )
    ready, reason = support.check_environment(
        Path(sys.executable), requirements, (3, 10)
    )
    assert not ready
    assert "nonexistent-launcher-test-package" in reason


def test_ready_environment_does_not_install(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setattr(support, "environment_python", lambda _: python)
    monkeypatch.setattr(support, "check_environment", lambda *_: (True, ""))
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(support.subprocess, "run", run)
    assert support.ensure_environment(tmp_path, (3, 10), log=lambda _: None) == python
    assert not any("pip" in command for command in commands)


def test_missing_dependencies_are_installed_before_launch(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.touch()
    (tmp_path / "requirements.txt").write_text("example-package\n", encoding="utf-8")
    monkeypatch.setattr(support, "environment_python", lambda _: python)
    checks = iter(((False, "example-package"), (True, "")))
    monkeypatch.setattr(support, "check_environment", lambda *_: next(checks))
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(support.subprocess, "run", run)
    assert support.ensure_environment(tmp_path, (3, 10), log=lambda _: None) == python
    install_command = [
        str(python),
        "-m",
        "pip",
        "install",
        "-r",
        str(tmp_path / "requirements.txt"),
    ]
    assert install_command in commands


def test_failed_venv_creation_never_installs_globally(tmp_path, monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(support.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        support.ensure_environment(tmp_path, (3, 10), log=lambda _: None)
    assert len(commands) == 1
    assert commands[0][1:3] == ["-m", "venv"]


def test_startup_failure_preserves_log(tmp_path):
    with pytest.raises(RuntimeError, match="exited with code"):
        support.start_dashboard(
            tmp_path,
            Path(sys.executable),
            "TestApp",
            0,
            lambda _: ["-c", "raise RuntimeError('startup sentinel')"],
        )
    assert "startup sentinel" in (tmp_path / "logs" / "server.log").read_text(
        encoding="utf-8"
    )


def test_occupied_port_fallback_and_reuse_without_killing_existing_server(tmp_path):
    import socket

    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        first = support.start_dashboard(
            tmp_path,
            Path(sys.executable),
            "TestApp",
            port,
            lambda selected: [str(script), str(selected)],
        )
        try:
            assert first.url != f"http://127.0.0.1:{port}"
            assert support.health_matches(first.url, "TestApp", tmp_path)
            assert not support.health_matches(first.url, "OtherApp", tmp_path)
            second = support.start_dashboard(
                tmp_path,
                Path(sys.executable),
                "TestApp",
                port,
                lambda selected: [str(script), str(selected)],
            )
            assert second.url == first.url
            assert second.process is None
            second.stop()
            assert first.process.poll() is None
        finally:
            first.stop()
        assert first.process.poll() is not None


def test_readiness_timeout_cleans_up_owned_child(tmp_path, monkeypatch):
    original = support.subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(support.subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="did not become ready"):
        support.start_dashboard(
            tmp_path,
            Path(sys.executable),
            "TestApp",
            0,
            lambda _: ["-c", "import time; time.sleep(30)"],
            timeout=0.2,
        )
    assert children[0].poll() is not None


def test_check_mode_never_starts_or_installs(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "launcher_bootstrap_test", ROOT / "helper_scripts" / "bootstrap.py"
    )
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--check"])
    monkeypatch.setattr(
        bootstrap, "ensure_environment", lambda *_: pytest.fail("Check attempted setup")
    )
    assert bootstrap.main() == 1


def test_real_application_starts_and_reports_readiness(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "launcher_config_smoke", ROOT / "helper_scripts" / "bootstrap.py"
    )
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

    def arguments(port):
        return [
            str(ROOT / "main.py"),
            "--no-venv",
            "--no-browser",
            "--port",
            str(port),
            "--out",
            str(tmp_path / "output"),
        ]

    dashboard = support.start_dashboard(
        ROOT, support.environment_python(ROOT), config.APP, 0, arguments
    )
    try:
        assert support.health_matches(dashboard.url, config.APP, ROOT)
    finally:
        dashboard.stop()
