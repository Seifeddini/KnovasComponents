"""environment: the scanning machine, and whether the Knovas API host answers from it (a TCP connection only)."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from ..util import error_code, result
from . import Context, Output

FACTS = {
    "os": "operating system and release of the scanning machine",
    "python": "Python version the tool runs on",
    "in_container": "whether the tool runs inside a container",
    "docker.present": "whether a docker command is on the PATH",
    "docker.version": "the docker client's version, or null",
    "cpus": "number of logical CPUs",
    "memory_bytes": "physical memory in bytes, or null if it cannot be read",
    "disk_free_bytes": "free disk space where the output folder is, in bytes",
    "api_host_set": "whether intake.yaml names a Knovas API host",
    "api_reachable": "whether a TCP connection to knovas.api_host:api_port succeeded (connect only, nothing sent)",
    "api_error": "why the connection failed (a code), or null",
    "proxy_vars": "names of the proxy environment variables that are set (never their values)",
}

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
              "all_proxy")


def _memory_bytes() -> int | None:
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        elif sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        elif sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    return None


def _in_container() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        return any(k in Path("/proc/1/cgroup").read_text() for k in ("docker", "kubepods", "containerd"))
    except OSError:
        return False


def _docker() -> dict:
    exe = shutil.which("docker")
    if not exe:
        return {"present": False, "version": None}
    try:
        out = subprocess.run([exe, "version", "--format", "{{.Client.Version}}"], capture_output=True, text=True,
                             timeout=15)
        version = out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        version = None
    return {"present": True, "version": version}


def tcp_reachable(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str | None]:
    """Open a TCP connection and close it at once. Nothing is sent."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, None
    except OSError as exc:
        return False, error_code(exc)


def run(ctx: Context) -> list[Output]:
    knovas = ctx.knovas or {}
    host, port = knovas.get("api_host"), knovas.get("api_port")
    if host and port:
        reachable, err = tcp_reachable(str(host), int(port))
    else:
        reachable, err = False, "not_configured"
    disk_dir = Path(ctx.out_dir) if ctx.out_dir else Path.cwd()
    try:
        free = shutil.disk_usage(disk_dir).free
    except OSError:
        free = None
    facts = {
        "os": f"{platform.system()} {platform.release()}".strip(),
        "python": platform.python_version(),
        "in_container": _in_container(),
        "docker": _docker(),
        "cpus": os.cpu_count(),
        "memory_bytes": _memory_bytes(),
        "disk_free_bytes": free,
        "api_host_set": bool(host),
        "api_reachable": reachable,
        "api_error": err,
        "proxy_vars": sorted(v for v in PROXY_VARS if os.environ.get(v)),
    }
    # An API host that does not answer is a finding, not a failure of the probe.
    return [Output(result("environment", "ok", None, facts), {"api": f"{host}:{port}" if host else None})]
