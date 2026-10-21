"""Standard-library setup and supervised local-dashboard startup."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

CHECK_REQUIREMENTS = """
import importlib.metadata as metadata
import sys
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
missing = []
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    requirement = Requirement(line)
    if requirement.marker and not requirement.marker.evaluate():
        continue
    try:
        version = metadata.version(requirement.name)
        if version not in requirement.specifier:
            missing.append(str(requirement))
    except metadata.PackageNotFoundError:
        missing.append(str(requirement))
print(", ".join(missing))
sys.exit(bool(missing))
"""


def source_revision(root: Path) -> str | None:
    """Fingerprint server code so the launcher never reuses a stale process."""
    digest = hashlib.sha256()
    found = False
    for name in ("main.py", "core.py", "search.py", "requirements.txt"):
        path = root / name
        try:
            content = path.read_bytes()
        except OSError:
            continue
        found = True
        digest.update(name.encode("utf-8"))
        digest.update(content)
    return digest.hexdigest()[:16] if found else None


def environment_python(root: Path) -> Path:
    for name in (".venv", "venv"):
        candidate = (
            root / name / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        if candidate.exists():
            return candidate
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def check_environment(
    python: Path, requirements: Path, minimum: tuple[int, int]
) -> tuple[bool, str]:
    if not python.exists():
        return False, "No project environment. Run the START launcher to set it up."
    try:
        version = subprocess.run(
            [str(python), "-c", "import sys; print('%s.%s' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        if tuple(map(int, version.split("."))) < minimum:
            return (
                False,
                f"Environment Python {version} is too old; requires {minimum[0]}.{minimum[1]}+.",
            )
        result = subprocess.run(
            [str(python), "-c", CHECK_REQUIREMENTS, str(requirements)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return False, f"Project environment cannot run: {error}"


def ensure_environment(
    root: Path, minimum: tuple[int, int], update: bool = False, log=print
) -> Path:
    if sys.version_info < minimum:
        raise RuntimeError(f"Install Python {minimum[0]}.{minimum[1]} or newer.")
    python = environment_python(root)
    if not python.exists():
        log("Creating isolated .venv...")
        subprocess.run([sys.executable, "-m", "venv", str(root / ".venv")], check=True)
    requirements = root / "requirements.txt"
    # Validate interpreter separately so pip cannot conceal a broken/old environment.
    version_check = subprocess.run(
        [str(python), "-c", f"import sys; sys.exit(sys.version_info < {minimum!r})"],
        timeout=15,
        check=False,
    )
    if version_check.returncode:
        raise RuntimeError(
            f"Environment {python} cannot run Python {minimum[0]}.{minimum[1]}+. Install a supported Python and rename the old environment before relaunching."
        )
    ready, reason = check_environment(python, requirements, minimum)
    if update or not ready:
        log(f"Installing project dependencies{': ' + reason if reason else ''}...")
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        setup_log = logs / "setup.log"
        command = [str(python), "-m", "pip", "install", "-r", str(requirements)]
        if update:
            command.append("--upgrade")
        with setup_log.open("a", encoding="utf-8") as output:
            result = subprocess.run(
                command,
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode:
            raise RuntimeError(f"Dependency installation failed. See {setup_log}")
        ready, reason = check_environment(python, requirements, minimum)
        if not ready:
            raise RuntimeError(
                f"Dependencies are still unavailable: {reason}. See {setup_log}"
            )
    else:
        log(
            "Project environment and dependency versions are ready (no installation needed)."
        )
    return python


def health_matches(url: str, app: str, root: Path) -> bool:
    try:
        # Loopback readiness must not be routed through a system proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/healthz", timeout=0.5) as response:
            data = json.load(response)
        expected_revision = source_revision(root)
        return (
            data.get("app") == app
            and data.get("root") == str(root.resolve())
            and (
                expected_revision is None
                or data.get("source_revision") == expected_revision
            )
        )
    except (OSError, ValueError, TypeError):
        return False


@dataclass
class Dashboard:
    url: str
    process: subprocess.Popen | None
    log_file: Path

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def start_dashboard(
    root: Path, python: Path, app: str, port: int, arguments, timeout=20
) -> Dashboard:
    url = f"http://127.0.0.1:{port}"
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    log_file = logs / "server.log"
    if health_matches(url, app, root):
        return Dashboard(url, None, log_file)
    state_file = logs / "server.json"
    try:
        saved_port = int(json.loads(state_file.read_text(encoding="utf-8"))["port"])
        if 1 <= saved_port <= 65535:
            saved_url = f"http://127.0.0.1:{saved_port}"
            if health_matches(saved_url, app, root):
                return Dashboard(saved_url, None, log_file)
    except (OSError, ValueError, TypeError, KeyError):
        pass
    # Never open another application's service when our preferred port is occupied.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with log_file.open("a", encoding="utf-8") as output:
        output.write(f"\nStarting {app} at {url}\n")
        output.flush()
        process = subprocess.Popen(
            [str(python), *arguments(port)],
            cwd=root,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    dashboard = Dashboard(url, process, log_file)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"{app} exited with code {process.returncode}. See {log_file}"
                )
            if health_matches(url, app, root):
                state_file.write_text(json.dumps({"port": port}), encoding="utf-8")
                return dashboard
            time.sleep(0.1)
        raise RuntimeError(
            f"{app} did not become ready within {timeout}s. See {log_file}"
        )
    except BaseException:
        dashboard.stop()
        raise


def run_dashboard(dashboard: Dashboard, no_browser: bool = False) -> int:
    print(f"Dashboard ready: {dashboard.url}")
    print(f"Server log: {dashboard.log_file}")
    try:
        if not no_browser:
            try:
                if not webbrowser.open(dashboard.url):
                    print(
                        "Browser could not be opened automatically. Open the URL above."
                    )
            except OSError as error:
                print(f"Browser could not open: {error}. Open the URL above.")
        if dashboard.process is None:
            print("Reusing this project's existing server.")
            return 0
        print("Keep this window open. Press Ctrl+C to stop the server.")
        return dashboard.process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        dashboard.stop()
