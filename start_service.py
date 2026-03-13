#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def parse_host_port(debugger_address: str) -> tuple[str, int]:
    host, port_str = debugger_address.split(":", 1)
    return host, int(port_str)


def is_debug_port_ready_with_retry(
    debugger_address: str,
    timeout: float = 0.4,
    retries: int = 5,
    retry_delay: float = 1.0,
) -> bool:
    host, port = parse_host_port(debugger_address)
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
            with urlopen(f"http://{debugger_address}/json/version", timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except (OSError, URLError):
            pass
        if attempt < attempts - 1:
            time.sleep(retry_delay)
    return False


def chrome_binary_candidates() -> list[str]:
    # Keep the original macOS candidates exactly as the old behavior.
    return [
        "/Applications/Visual Studio Code.app/Contents/MacOS/Electron",
        "/Applications/Visual Studio Code - Insiders.app/Contents/MacOS/Electron",
        "/Applications/VSCodium.app/Contents/MacOS/Electron",
    ]


def windows_code_binary_candidates() -> list[str]:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
    explicit_candidates = [
        str(Path(local_app_data) / "Programs" / "Microsoft VS Code" / "Code.exe"),
        str(Path(local_app_data) / "Programs" / "Microsoft VS Code Insiders" / "Code - Insiders.exe"),
        str(Path(program_files) / "Microsoft VS Code" / "Code.exe"),
        str(Path(program_files_x86) / "Microsoft VS Code" / "Code.exe"),
        str(Path(local_app_data) / "Programs" / "VSCodium" / "VSCodium.exe"),
    ]

    path_candidates: list[str] = []
    for command in (
        "code",
        "code.cmd",
        "code-insiders",
        "code-insiders.cmd",
        "codium",
        "codium.cmd",
    ):
        hit = shutil.which(command)
        if hit:
            path_candidates.append(hit)

    ordered = path_candidates + explicit_candidates
    deduped: list[str] = []
    for item in ordered:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _launch_macos(debugger_address: str, startup_wait: float) -> bool:
    _, port = parse_host_port(debugger_address)
    chrome_binary = next((p for p in chrome_binary_candidates() if p and Path(p).exists()), None)
    if not chrome_binary:
        print("Khong tim thay binary VS Code de tu mo remote debugging.")
        return False

    user_data_dir = Path.home() / ".selenium-debug-profile"
    cmd = [
        chrome_binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Da yeu cau mo VS Code debug tren cong {port}, doi {startup_wait:.1f}s...")
    time.sleep(startup_wait)
    return is_debug_port_ready_with_retry(
        debugger_address=debugger_address,
        timeout=1.2,
        retries=6,
        retry_delay=1.0,
    )


def _launch_windows(debugger_address: str, startup_wait: float) -> bool:
    _, port = parse_host_port(debugger_address)
    code_binary = next(
        (p for p in windows_code_binary_candidates() if p and Path(p).exists()),
        None,
    )
    if not code_binary:
        print("Khong tim thay binary VS Code tren Windows de tu mo remote debugging.")
        print("Hay dam bao lenh 'code' co trong PATH hoac VS Code duoc cai dung thu muc mac dinh.")
        return False

    user_data_dir = Path(os.environ.get("TEMP", str(Path.home()))) / "vscode-selenium-profile"
    argv = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    binary_path = Path(code_binary)
    if binary_path.suffix.lower() in {".cmd", ".bat"}:
        cmd = ["cmd", "/c", code_binary, *argv]
    else:
        cmd = [code_binary, *argv]

    print(f"Dung binary VS Code tren Windows: {code_binary}")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Da yeu cau mo VS Code debug tren cong {port}, doi {startup_wait:.1f}s...")
    time.sleep(startup_wait)
    return is_debug_port_ready_with_retry(
        debugger_address=debugger_address,
        timeout=1.2,
        retries=8,
        retry_delay=1.0,
    )


def launch_chrome_debugger(debugger_address: str, startup_wait: float) -> bool:
    host, _ = parse_host_port(debugger_address)
    if host != "127.0.0.1" and host.lower() != "localhost":
        print("Chi ho tro tu mo debug target voi host localhost/127.0.0.1.")
        return False

    system = platform.system().lower()
    if system == "darwin":
        return _launch_macos(debugger_address, startup_wait)
    if system == "windows":
        return _launch_windows(debugger_address, startup_wait)

    print(f"Chua ho tro auto-launch tren he dieu hanh: {system}")
    return False
