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

CHECK_VSCODE_UPDATE_JS = r"""
function normalizeText(text) {
  return (text || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function isVisible(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  if (!style) return false;
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
    return false;
  }
  const rect = el.getBoundingClientRect();
  return !!rect && rect.width > 0 && rect.height > 0;
}

const items = [...document.querySelectorAll('.notification-list-item, .notification-toast')]
  .filter((it) => isVisible(it));
if (!items.length) {
  return { has_notification: false, notification_count: 0, update_like_count: 0 };
}

let updateLikeCount = 0;
for (const item of items) {
  const t = normalizeText(item.innerText || item.textContent || '');
  if (
    t.includes('restart visual studio code') ||
    t.includes('update') ||
    t.includes('install') ||
    t.includes('error') ||
    t.includes('not connected')
  ) {
    updateLikeCount += 1;
  }
}

const first = items[0];
const r = first.getBoundingClientRect();
return {
  has_notification: true,
  notification_count: items.length,
  update_like_count: updateLikeCount,
  x: Math.round(r.left),
  y: Math.round(r.top),
  w: Math.round(r.width),
  h: Math.round(r.height),
};
"""

DISMISS_VSCODE_UPDATE_JS = r"""
function normalizeText(text) {
  return (text || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function isVisible(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  if (!style) return false;
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
    return false;
  }
  const rect = el.getBoundingClientRect();
  return !!rect && rect.width > 0 && rect.height > 0;
}

function toClickable(el, root) {
  let cur = el;
  for (let i = 0; i < 8 && cur; i += 1) {
    if (root && !root.contains(cur)) break;
    const tag = (cur.tagName || '').toLowerCase();
    const role = normalizeText(cur.getAttribute('role') || '');
    if (tag === 'button' || role === 'button' || tag === 'a') return cur;
    cur = cur.parentElement;
  }
  return el;
}

function isExcludedAction(el) {
  const label = normalizeText(
    (el.getAttribute('aria-label') || '') + ' ' +
    (el.getAttribute('title') || '') + ' ' +
    (el.innerText || el.textContent || '')
  );
  return (
    label.includes('update now') ||
    label.includes('release notes') ||
    label === 'later' ||
    label.endsWith(' later') ||
    /(^|\s)yes(\s|$)/.test(label) ||
    /(^|\s)no(\s|$)/.test(label) ||
    label.includes('open devices and boards view') ||
    label.includes('work offline')
  );
}

function fireMouse(el, type) {
  el.dispatchEvent(
    new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      view: window,
      buttons: 1,
    })
  );
}

function clickElement(el) {
  if (!el) return false;
  try { el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' }); } catch (_) {}
  const chain = [el, el.parentElement].filter(Boolean);
  for (const target of chain) {
    try {
      fireMouse(target, 'mouseover');
      fireMouse(target, 'mousemove');
      fireMouse(target, 'mousedown');
      fireMouse(target, 'mouseup');
      fireMouse(target, 'click');
    } catch (_) {}
  }
  try { el.click(); return true; } catch (_) { return false; }
}

function clickTopRightClosePoint(item) {
  const r = item.getBoundingClientRect();
  const points = [
    { x: r.right - 14, y: r.top + 18 },
    { x: r.right - 22, y: r.top + 18 },
    { x: r.right - 14, y: r.top + 26 },
    { x: r.right - 30, y: r.top + 20 },
  ];
  for (const p of points) {
    const at = document.elementFromPoint(
      Math.max(1, Math.min(window.innerWidth - 1, p.x)),
      Math.max(1, Math.min(window.innerHeight - 1, p.y))
    );
    if (!at) continue;
    if (!item.contains(at)) continue;
    const target = toClickable(at, item);
    if (!target || !item.contains(target)) continue;
    if (isExcludedAction(target)) continue;
    if (clickElement(target)) return { ok: true, target, point: p };
  }
  return { ok: false };
}

function clickCloseInItem(item) {
  const selectorHits = item.querySelectorAll(
    '.notification-list-item-toolbar-container .action-label.codicon.codicon-close, .codicon-close, [aria-label*="Close" i], [title*="Close" i]'
  );
  for (const raw of selectorHits) {
    if (!isVisible(raw)) continue;
    const target = toClickable(raw, item);
    if (!target || !item.contains(target)) continue;
    if (isExcludedAction(target)) continue;
    if (clickElement(target)) return { ok: true, target };
  }
  return { ok: false };
}

function clickGlobalClear() {
  const clearBtn = document.querySelector(
    '.notifications-toasts .action-label.codicon.codicon-notifications-clear, [aria-label*="Clear Notification" i]'
  );
  if (!clearBtn || !isVisible(clearBtn)) return { ok: false };
  const target = toClickable(clearBtn, document.body);
  if (!target || isExcludedAction(target)) return { ok: false };
  if (!clickElement(target)) return { ok: false };
  return { ok: true, target };
}

const MAX_CLOSE_COUNT = 6;
let closed = 0;
for (let step = 0; step < MAX_CLOSE_COUNT; step += 1) {
  const items = [...document.querySelectorAll('.notification-list-item, .notification-toast')]
    .filter((it) => isVisible(it));
  if (!items.length) {
    if (closed > 0) {
      return { clicked: true, closed_count: closed, reason: 'closed_all_visible_notifications' };
    }
    return { clicked: false, closed_count: 0, reason: 'no_notification_banner' };
  }

  const item = items[0];
  const byPoint = clickTopRightClosePoint(item);
  if (byPoint.ok) {
    closed += 1;
    continue;
  }
  const bySelector = clickCloseInItem(item);
  if (bySelector.ok) {
    closed += 1;
    continue;
  }

  const byGlobalClear = clickGlobalClear();
  if (byGlobalClear.ok) {
    return {
      clicked: true,
      closed_count: Math.max(closed, 1),
      reason: 'clicked_global_clear_notification',
      cls: (byGlobalClear.target.getAttribute('class') || '').toString().slice(0, 140),
    };
  }

  return {
    clicked: closed > 0,
    closed_count: closed,
    reason: closed > 0 ? 'closed_some_notifications' : 'close_x_not_found',
  };
}

return {
  clicked: closed > 0,
  closed_count: closed,
  reason: closed > 0 ? 'max_close_count_reached' : 'close_x_not_found',
};
"""

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


def _switch_to_frame_path(driver, frame_path: list[int]) -> bool:
    driver.switch_to.default_content()
    for frame_index in frame_path:
        frames = driver.find_elements("css selector", "iframe, frame")
        if frame_index >= len(frames):
            return False
        driver.switch_to.frame(frames[frame_index])
    return True


def _scan_and_dismiss_in_frame_tree(
    driver,
    depth: int,
    max_depth: int,
    frame_path: list[int],
) -> dict:
    try:
        result = driver.execute_script(DISMISS_VSCODE_UPDATE_JS) or {}
    except Exception:
        result = {}

    if bool(result.get("clicked")):
        result["frame_path"] = frame_path
        return result

    if depth >= max_depth:
        return {"clicked": False, "reason": result.get("reason", "max_depth_reached")}

    child_frames = driver.find_elements("css selector", "iframe, frame")
    last_reason = result.get("reason", "no_notification_banner_or_no_close_x")
    for frame_index, frame in enumerate(child_frames):
        try:
            driver.switch_to.frame(frame)
            found = _scan_and_dismiss_in_frame_tree(
                driver=driver,
                depth=depth + 1,
                max_depth=max_depth,
                frame_path=frame_path + [frame_index],
            )
            driver.switch_to.parent_frame()
            if bool(found.get("clicked")):
                return found
            if found.get("reason"):
                last_reason = str(found.get("reason"))
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                _switch_to_frame_path(driver, frame_path)
    return {"clicked": False, "reason": last_reason}


def dismiss_vscode_update_notification(
    driver,
    max_depth: int = 8,
) -> dict:
    try:
        driver.switch_to.default_content()
    except Exception:
        return {"clicked": False, "reason": "switch_default_content_failed"}

    result = _scan_and_dismiss_in_frame_tree(
        driver=driver,
        depth=0,
        max_depth=max_depth,
        frame_path=[],
    )
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return result


def _scan_for_update_banner_in_frame_tree(
    driver,
    depth: int,
    max_depth: int,
    frame_path: list[int],
) -> dict:
    try:
        result = driver.execute_script(CHECK_VSCODE_UPDATE_JS) or {}
    except Exception:
        result = {}

    if bool(result.get("has_notification")):
        return {"has_notification": True, "frame_path": frame_path, "notification_count": int(result.get("notification_count") or 0), "update_like_count": int(result.get("update_like_count") or 0)}

    if depth >= max_depth:
        return {"has_notification": False}

    child_frames = driver.find_elements("css selector", "iframe, frame")
    for frame_index, frame in enumerate(child_frames):
        try:
            driver.switch_to.frame(frame)
            found = _scan_for_update_banner_in_frame_tree(
                driver=driver,
                depth=depth + 1,
                max_depth=max_depth,
                frame_path=frame_path + [frame_index],
            )
            driver.switch_to.parent_frame()
            if bool(found.get("has_notification")):
                return found
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                _switch_to_frame_path(driver, frame_path)
    return {"has_notification": False}


def has_vscode_update_notification(
    driver,
    max_depth: int = 8,
) -> dict:
    try:
        driver.switch_to.default_content()
    except Exception:
        return {"has_notification": False, "reason": "switch_default_content_failed"}

    result = _scan_for_update_banner_in_frame_tree(
        driver=driver,
        depth=0,
        max_depth=max_depth,
        frame_path=[],
    )
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return result
