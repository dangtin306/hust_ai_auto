#!/usr/bin/env python3
from __future__ import annotations

import platform
import subprocess
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

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

ATTACHMENT_STATE_JS = r"""
const targetName = String(arguments[0] || "").toLowerCase();

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

function isVisible(el) {
  if (!el || !el.getBoundingClientRect) return false;
  const style = window.getComputedStyle(el);
  if (!style) return false;
  if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || "1") === 0) {
    return false;
  }
  const rect = el.getBoundingClientRect();
  return !!rect && rect.width > 0 && rect.height > 0;
}

function textOf(el) {
  return String(el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
}

const all = [];
collectElementsDeep(document.documentElement || document, all);

const fileInputs = all.filter((el) => {
  if (!el || !el.tagName) return false;
  if (el.tagName.toLowerCase() !== "input") return false;
  return String(el.getAttribute("type") || "").toLowerCase() === "file";
});
const hasFiles = fileInputs.some((el) => el.files && el.files.length > 0);

const previewSelector =
  'img[src^="blob:"], img[src^="data:image/"], [data-testid*="attachment"], [data-testid*="upload"], [class*="attachment"], [class*="upload"], [aria-label*="image"], [aria-label*="attachment"]';
let hasPreview = false;
let fileLikeTextCount = 0;
let fileNameHit = false;

for (const el of all) {
  if (!isVisible(el)) continue;
  const txt = textOf(el);
  if (targetName && txt.includes(targetName)) fileNameHit = true;
  if (/\b[\w .-]+\.(png|jpe?g|webp|gif|bmp|tiff?)\b/i.test(txt)) fileLikeTextCount += 1;
  if (!hasPreview && el.matches) {
    try {
      if (el.matches(previewSelector)) hasPreview = true;
    } catch (_) {}
  }
}

return {
  hasFiles,
  hasPreview,
  fileNameHit,
  fileLikeTextCount,
  ok: hasFiles || hasPreview || fileNameHit || fileLikeTextCount > 0,
};
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


def _paste_with_shortcut_windows(press_enter: bool, chat_input=None) -> bool:
    # Prefer Selenium key events first so we stay in the exact chat element.
    if chat_input is not None:
        try:
            chat_input.send_keys(Keys.chord(Keys.CONTROL, "v"))
            if press_enter:
                chat_input.send_keys(Keys.ENTER)
            return True
        except Exception:
            pass

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


def _normalize_attachment_state(state: dict | None = None) -> dict:
    raw = state or {}
    return {
        "ok": bool(raw.get("ok")),
        "hasFiles": bool(raw.get("hasFiles")),
        "hasPreview": bool(raw.get("hasPreview")),
        "fileNameHit": bool(raw.get("fileNameHit")),
        "fileLikeTextCount": int(raw.get("fileLikeTextCount") or 0),
    }


def _read_attachment_state(driver, image_name: str = "") -> dict:
    try:
        state = driver.execute_script(ATTACHMENT_STATE_JS, image_name or "") or {}
    except Exception:
        return _normalize_attachment_state()
    return _normalize_attachment_state(state)


def _attachment_state_changed(before: dict, after: dict) -> bool:
    if bool(after.get("hasFiles")) and not bool(before.get("hasFiles")):
        return True
    if bool(after.get("hasPreview")) and not bool(before.get("hasPreview")):
        return True
    if bool(after.get("fileNameHit")) and not bool(before.get("fileNameHit")):
        return True
    before_count = int(before.get("fileLikeTextCount") or 0)
    after_count = int(after.get("fileLikeTextCount") or 0)
    if after_count > before_count:
        return True
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
        before = _read_attachment_state(driver, image_name=image_path.name)
        if not _copy_image_to_clipboard_windows(image_path):
            return False, "copy_clipboard_fail"
        if not _paste_with_shortcut_windows(press_enter, chat_input=chat_input):
            return False, "paste_shortcut_fail"
        for _ in range(5):
            time.sleep(0.2)
            state = _read_attachment_state(driver, image_name=image_path.name)
            if _attachment_state_changed(before, state):
                return True, "clipboard_windows_detected"
            if state.get("ok") and before.get("ok") and (state.get("fileNameHit") or before.get("fileNameHit")):
                return True, "clipboard_windows_existing_attachment"
        return False, "paste_no_attachment_detected"

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
        before = _read_attachment_state(driver, image_name=image_path.name)
        file_input.send_keys(str(image_path))
        files_count = driver.execute_script(
            "const el = arguments[0]; return el.files ? el.files.length : 0;",
            file_input,
        )
        if int(files_count or 0) > 0:
            time.sleep(0.25)
            return True, "file_input_send_keys_files_present"
        for _ in range(6):
            time.sleep(0.2)
            state = _read_attachment_state(driver, image_name=image_path.name)
            if _attachment_state_changed(before, state):
                return True, "file_input_send_keys_state_changed"
            if state.get("ok") and before.get("ok") and (state.get("fileNameHit") or before.get("fileNameHit")):
                return True, "file_input_existing_attachment"
        return False, "file_input_no_attachment_detected"
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

    system = platform.system().lower()

    attempt_errors: list[str] = []
    clipboard_tried = False

    # On Windows, clipboard paste is usually more stable for VS Code Codex chat.
    if system == "windows" and chat_input is not None and frame_path is not None:
        if _switch_to_frame_path(driver, frame_path):
            clipboard_tried = True
            ok, method = _paste_image_from_clipboard(
                driver=driver,
                image_path=resolved,
                chat_input=chat_input,
                press_enter=press_enter,
            )
            if ok:
                return True, {"method": method, "image_path": str(resolved)}
            attempt_errors.append(f"clipboard:{method}")

    if chat_input is not None and frame_path is not None and not clipboard_tried:
        if _switch_to_frame_path(driver, frame_path):
            ok, method = _paste_image_from_clipboard(
                driver=driver,
                image_path=resolved,
                chat_input=chat_input,
                press_enter=press_enter,
            )
            if ok:
                return True, {"method": method, "image_path": str(resolved)}
            attempt_errors.append(f"clipboard:{method}")

    ok, method = _attach_via_file_input(driver, resolved)
    if frame_path is not None:
        _switch_to_frame_path(driver, frame_path)
    else:
        driver.switch_to.default_content()
    if ok:
        return True, {"method": method, "image_path": str(resolved)}
    attempt_errors.append(f"file_input:{method}")
    return False, {"error": method, "attempts": attempt_errors, "image_path": str(resolved)}
