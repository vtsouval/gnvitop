#!/usr/bin/env python3
"""Local browser controls for the unmodified gnvitop package."""
import hmac
import json
import os
from pathlib import Path
import plistlib
import html
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE = Path.home() / "Library/Application Support/gnvitop"
DOMAIN = f"gui/{os.getuid()}"
WORKER = "local.gnvitop"
MANAGER = "local.gnvitop.control"
PLISTS = Path.home() / "Library/LaunchAgents"
URL = "http://127.0.0.1:5050"
UPSTREAM = "http://127.0.0.1:5051"
PACKAGE = Path(__file__).parent
LOCK = threading.RLock()
TOKEN_FILE = BASE / "control-token"
STATE_FILE = BASE / "control-state.json"


def launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=15)


def job(label):
    result = launchctl("print", f"{DOMAIN}/{label}")
    match = re.search(r"^\s*pid = (\d+)$", result.stdout, re.M)
    return result.returncode == 0, int(match[1]) if match else None


def ready():
    try:
        with urlopen(UPSTREAM, timeout=1) as response:
            return response.status == 200 and b"gnvitop" in response.read(4096)
    except (URLError, OSError, TimeoutError):
        return False


def status():
    _, pid = job(WORKER)
    return {"running": bool(pid), "ready": bool(pid) and ready(), "pid": pid}


def remember(running):
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"running": running}) + "\n")
    temporary.chmod(0o600)
    temporary.replace(STATE_FILE)


def start():
    with LOCK:
        loaded, pid = job(WORKER)
        if not pid:
            # Never terminate or take over an unrelated listener.
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", 5051))
                except OSError:
                    raise RuntimeError("Port 5051 is already in use. The other application was left running.")
            if not loaded:
                result = launchctl("bootstrap", DOMAIN, str(PLISTS / f"{WORKER}.plist"))
                if result.returncode and not job(WORKER)[0]:
                    raise RuntimeError("Could not load the monitoring service: " + result.stderr.strip())
            result = launchctl("kickstart", f"{DOMAIN}/{WORKER}")
            if result.returncode:
                raise RuntimeError("Could not start monitoring: " + result.stderr.strip())
        for _ in range(60):
            if ready():
                remember(True)
                return status()
            time.sleep(0.2)
        raise RuntimeError("Monitoring did not become ready. See gnvitop-error.log in the installation folder.")


def stop():
    with LOCK:
        loaded, pid = job(WORKER)
        if loaded:
            result = launchctl("bootout", f"{DOMAIN}/{WORKER}")
            if result.returncode and job(WORKER)[0]:
                raise RuntimeError("Could not stop monitoring: " + result.stderr.strip())
        # bootout terminates only this registered service and its child processes.
        for _ in range(50):
            if not job(WORKER)[1]:
                remember(False)
                return status()
            time.sleep(0.1)
        raise RuntimeError("The monitoring service is still stopping.")


class Handler(BaseHTTPRequestHandler):
    server_version = "gnvitop-control"

    def log_message(self, *_):
        pass

    def reply(self, code, body, content_type="application/json", nonce=""):
        if isinstance(body, dict):
            body = json.dumps(body)
        body = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-src {UPSTREAM}; img-src 'self' data:; manifest-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        if self.headers.get("Host") not in {"127.0.0.1:5050", "localhost:5050"}:
            self.reply(403, {"error": "Local access only"})
            return False
        return True

    def do_GET(self):
        if not self.valid_host():
            return
        if self.path == "/":
            nonce = secrets.token_urlsafe(24)
            page = (PACKAGE / "desktop.html").read_text().replace("__NONCE__", nonce).replace("__TOKEN__", self.server.token)
            from .preferences import load
            group_names = [g["name"] for g in load()["groups"]]
            badges = "".join("<span>" + html.escape(name) + "</span>" for name in group_names)
            self.reply(200, page.replace("__GROUPS__", badges), "text/html; charset=utf-8", nonce)
        elif self.path == "/control/status":
            self.reply(200, {"app": "gnvitop-control", **status()})
        elif self.path == "/icon.svg":
            self.reply(200, (PACKAGE / "icon.svg").read_bytes(), "image/svg+xml")
        elif self.path == "/manifest.json":
            self.reply(200, {"name": "gnvitop", "short_name": "gnvitop", "start_url": "/", "display": "standalone", "background_color": "#0f172a", "theme_color": "#0f172a", "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]})
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if not self.valid_host():
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + self.headers.get("Host", ""):
            return self.reply(403, {"error": "Cross-origin control is not allowed"})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.reply(403, {"error": "Cross-site control is not allowed"})
        if not hmac.compare_digest(self.headers.get("X-Gnvitop-Token", ""), self.server.token):
            return self.reply(403, {"error": "Reload this page before using the controls"})
        if self.path not in {"/control/start", "/control/stop", "/control/restart"}:
            return self.reply(404, {"error": "Not found"})
        try:
            with LOCK:
                if self.path == "/control/restart":
                    stop()
                result = stop() if self.path == "/control/stop" else start()
            self.reply(200, result)
        except Exception as exc:
            self.reply(500, {"error": str(exc)})


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 5050), Handler)
    server.token = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(server.token)
    try:
        desired = json.loads(STATE_FILE.read_text()).get("running", False)
    except (FileNotFoundError, ValueError):
        desired = False
    if desired:
        try:
            start()
        except Exception as exc:
            print(f"Automatic start: {exc}", file=sys.stderr, flush=True)
    server.serve_forever()


def request(action=None):
    headers = {"X-Gnvitop-Token": TOKEN_FILE.read_text()} if action else {}
    req = Request(URL + (f"/control/{action}" if action else "/control/status"), data=b"" if action else None, headers=headers)
    with urlopen(req, timeout=25) as response:
        return json.load(response)


def install():
    """Install per-user launchd jobs; no administrator access is required."""
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    PLISTS.mkdir(parents=True, exist_ok=True)
    config = BASE / "ssh-config"
    if not config.exists():
        config = Path.home() / ".ssh/config"
    environment = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONUNBUFFERED": "1", "GNVITOP_CONTROL_ORIGIN": URL}
    common = {"WorkingDirectory": str(BASE), "EnvironmentVariables": environment, "ThrottleInterval": 10}
    worker = {**common, "Label": WORKER, "ProgramArguments": [sys.executable, "-m", "gnvitop", "--foreground", "--host", "127.0.0.1", "--port", "5051", "--no-browser", "--ssh-config", str(config)], "RunAtLoad": False, "KeepAlive": False, "StandardOutPath": str(BASE / "gnvitop.log"), "StandardErrorPath": str(BASE / "gnvitop-error.log")}
    manager = {**common, "Label": MANAGER, "ProgramArguments": [sys.executable, "-m", "gnvitop.desktop", "--serve"], "RunAtLoad": True, "KeepAlive": True, "StandardOutPath": str(BASE / "control.log"), "StandardErrorPath": str(BASE / "control-error.log")}
    for definition in [worker, manager]:
        path = PLISTS / (definition["Label"] + ".plist")
        path.write_bytes(plistlib.dumps(definition))
        path.chmod(0o600)


def ensure_controller():
    if not (PLISTS / f"{MANAGER}.plist").exists() or not (PLISTS / f"{WORKER}.plist").exists():
        install()
    loaded, pid = job(MANAGER)
    if not loaded:
        result = launchctl("bootstrap", DOMAIN, str(PLISTS / f"{MANAGER}.plist"))
        if result.returncode and not job(MANAGER)[0]:
            raise RuntimeError("Cannot load the local launcher: " + result.stderr.strip())
    elif not pid:
        launchctl("kickstart", f"{DOMAIN}/{MANAGER}")
    for _ in range(100):
        try:
            if request().get("app") == "gnvitop-control":
                return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("The local launcher is not ready. Check control-error.log in the installation folder.")


def cli(args):
    if args == ["--serve"]:
        return serve()
    if args == ["--install"]:
        return install()
    if args and args[0] in {"--help", "-h"}:
        print("gnvitop on this Mac\n  gnvitop                 Start in background and open Safari\n  gnvitop stop            Stop monitoring\n  gnvitop restart         Restart monitoring and open Safari\n  gnvitop status          Show monitoring status\n  gnvitop --no-browser    Start in background and print the link\n  gnvitop --foreground    Run the official CLI in this terminal\n\nSafari controls: " + URL + "\n\nOther official options (for example --agent, --tui, --version) are passed through.\n")
        return subprocess.call([sys.executable, "-m", "gnvitop", "--foreground", "--help"])
    no_browser = "--no-browser" in args
    command_args = [a for a in args if a != "--no-browser"]
    managed = not command_args or command_args in [["start"], ["stop"], ["status"], ["restart"]]
    if not managed:
        args = [a for a in args if a != "--foreground"]
        if not any(a == "--ssh-config" or a.startswith("--ssh-config=") for a in args):
            args += ["--ssh-config", str(BASE / "ssh-config")]
        if not any(a == "--host" or a.startswith("--host=") for a in args):
            args += ["--host", "127.0.0.1"]
        # Keep foreground runs away from the managed controller and dashboard.
        if not any(a in {"-p", "--port"} or a.startswith("--port=") for a in args):
            args += ["--port", "5052"]
        os.execv(sys.executable, [sys.executable, "-m", "gnvitop", "--foreground", *args])
    action = command_args[0] if command_args else "start"
    ensure_controller()
    result = request(None if action == "status" else action)
    print("gnvitop: " + ("monitoring in background" if result["running"] else "monitoring stopped"))
    if sys.stdout.isatty():
        print(f"\033]8;;{URL}\033\\Open gnvitop\033]8;;\033\\ — {URL}")
    else:
        print("Open gnvitop: " + URL)
    if result["running"]:
        print("You can close Terminal. Stop from the webpage or run: gnvitop stop")
    if action in {"start", "restart"} and not no_browser:
        subprocess.run(["/usr/bin/open", "-a", "Safari", URL], check=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(cli(sys.argv[1:]))
    except (OSError, RuntimeError, URLError) as exc:
        print(f"gnvitop: {exc}", file=sys.stderr)
        sys.exit(1)
