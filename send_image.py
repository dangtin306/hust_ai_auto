#!/usr/bin/env python3
from __future__ import annotations

import platform
import subprocess
import time
from pathlib import Path

from selenium.webdriver.common.by import By

FILE_INPUT_JS = r"""
function collectElementsDeep(node, out) {
  if (!node) return;
  if (node.nodeType === Node.ELEMENT_NODE) {
    out.push(node);
    if (node.shadowRoot) collectElementsDeep(node.shadowRoot, out);
  }
  const children = node.children || [];
  for (let i = 0; i < children.length; i += 1) {
    collectElementsDeep(children[i], out);
  }
}

const all = [];
collectElementsDeep(document.documentElement || document, all);
const fileInputs = all.filter((el) => {
  if (!el || !el.tagName) return false;
  if (el.tagName.toLowerCase() !== "input") return false;
  const typ = (el.getAttribute("type") || "").toLowerCase();
  return typ === "file";
});
if (!fileInputs.length) return null;

for (const el of fileInputs) {
  const accept = (el.getAttribute("accept") || "").toLowerCase();
  if (
    !accept ||
    accept.includes("image") ||
    accept.includes("png") ||
    accept.includes("jpg") ||
    accept.includes("jpeg") ||
    accept.includes("webp")
  ) {
    return el;
  }
}
return fileInputs[0];
"""


def _switch_to_frame_path(driver, frame_path: list[int]) -> bool:
    driver.switch_to.default_content()
    for frame_index in frame_path:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if frame_index >= len(frames):
            return False
        driver.switch_to.frame(frames[frame_index])
    return True


def _find_file_input_frame_path(driver, depth: int, max_depth: int, frame_path: list[int]) -> list[int] | None:
    try:
        found = driver.execute_script(FILE_INPUT_JS)
    except Exception:
        found = None
    if found is not None:
        return frame_path

    if depth >= max_depth:
        return None

    child_frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for frame_index, frame in enumerate(child_frames):
        try:
            driver.switch_to.frame(frame)
            result = _find_file_input_frame_path(
                driver=driver,
                depth=depth + 1,
                max_depth=max_depth,
                frame_path=frame_path + [frame_index],
            )
            driver.switch_to.parent_frame()
            if result is not None:
                return result
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                _switch_to_frame_path(driver, frame_path)
    return None


def _paste_with_shortcut_mac(press_enter: bool) -> bool:
    try:
        for app_name in (
            "Visual Studio Code",
            "Visual Studio Code - Insiders",
            "VSCodium",
            "Code",
        ):
            subprocess.run(
                ["osascript", "-e", f'tell application "{app_name}" to activate'],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        script_lines = [
            'tell application "System Events"',
            '  keystroke "v" using command down',
            "  delay 0.12",
        ]
        if press_enter:
            script_lines.append("  key code 36")
        script_lines.append("end tell")
        subprocess.run(["osascript", "-e", "\n".join(script_lines)], check=True)
        return True
    except Exception:
        return False


def _copy_image_to_clipboard_mac(image_path: Path) -> bool:
    ext = image_path.suffix.lower()
    classes = ["«class PNGf»", "PNG picture", "TIFF picture"]
    if ext in {".jpg", ".jpeg"}:
        classes = ["«class JPEG»", "JPEG picture", "TIFF picture"]
    elif ext in {".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        classes = ["TIFF picture", "PNG picture", "«class PNGf»"]

    escaped = str(image_path).replace("\\", "\\\\").replace('"', '\\"')
    for image_class in classes:
        script = f'set the clipboard to (read (POSIX file "{escaped}") as {image_class})'
        try:
            subprocess.run(["osascript", "-e", script], check=True)
            return True
        except Exception:
            continue
    return False


def _copy_image_to_clipboard_windows(image_path: Path) -> bool:
    escaped = str(image_path).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Drawing.Image]::FromFile('{escaped}')
[System.Windows.Forms.Clipboard]::SetImage($img)
$img.Dispose()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _paste_with_shortcut_windows(press_enter: bool) -> bool:
    keys = "^v"
    if press_enter:
        keys += "{ENTER}"
    script = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$ws = New-Object -ComObject WScript.Shell
$null = $ws.AppActivate('Visual Studio Code')
Start-Sleep -Milliseconds 120
[System.Windows.Forms.SendKeys]::SendWait('{keys}')
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _paste_image_from_clipboard(driver, image_path: Path, chat_input, press_enter: bool) -> tuple[bool, str]:
    try:
        driver.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
    except Exception:
        return False, "focus_fail"

    system = platform.system().lower()
    if system == "darwin":
        if not _copy_image_to_clipboard_mac(image_path):
            return False, "copy_clipboard_fail"
        if not _paste_with_shortcut_mac(press_enter):
            return False, "paste_shortcut_fail"
        time.sleep(0.35)
        return True, "clipboard_mac"

    if system == "windows":
        if not _copy_image_to_clipboard_windows(image_path):
            return False, "copy_clipboard_fail"
        if not _paste_with_shortcut_windows(press_enter):
            return False, "paste_shortcut_fail"
        time.sleep(0.35)
        return True, "clipboard_windows"

    return False, f"unsupported_os:{system}"


def _attach_via_file_input(driver, image_path: Path) -> tuple[bool, str]:
    driver.switch_to.default_content()
    frame_path = _find_file_input_frame_path(driver=driver, depth=0, max_depth=8, frame_path=[])
    if frame_path is None:
        return False, "file_input_not_found"
    if not _switch_to_frame_path(driver, frame_path):
        return False, "file_input_frame_switch_fail"

    try:
        file_input = driver.execute_script(FILE_INPUT_JS)
    except Exception:
        file_input = None
    if file_input is None:
        return False, "file_input_missing_after_switch"

    try:
        driver.execute_script(
            """
            const el = arguments[0];
            el.removeAttribute('hidden');
            el.style.display = 'block';
            el.style.visibility = 'visible';
            el.style.opacity = '1';
            """,
            file_input,
        )
    except Exception:
        pass

    try:
        file_input.send_keys(str(image_path))
        files_count = driver.execute_script(
            "const el = arguments[0]; return el.files ? el.files.length : 0;",
            file_input,
        )
        if int(files_count or 0) <= 0:
            return False, "file_input_no_files_after_send"
        time.sleep(0.35)
        return True, "file_input_send_keys"
    except Exception:
        return False, "file_input_send_keys_fail"


def send_image_to_chat(
    driver,
    image_path: str | Path,
    chat_input=None,
    frame_path: list[int] | None = None,
    press_enter: bool = False,
) -> tuple[bool, dict]:
    resolved = Path(image_path).expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        return False, {"error": "image_not_found", "image_path": str(resolved)}

    if chat_input is not None and frame_path is not None:
        if _switch_to_frame_path(driver, frame_path):
            ok, method = _paste_image_from_clipboard(
                driver=driver,
                image_path=resolved,
                chat_input=chat_input,
                press_enter=press_enter,
            )
            if ok:
                return True, {"method": method, "image_path": str(resolved)}

    ok, method = _attach_via_file_input(driver, resolved)
    if frame_path is not None:
        _switch_to_frame_path(driver, frame_path)
    else:
        driver.switch_to.default_content()
    if ok:
        return True, {"method": method, "image_path": str(resolved)}
    return False, {"error": method, "image_path": str(resolved)}
