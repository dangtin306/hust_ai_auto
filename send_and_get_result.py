#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from send_image import send_image_to_chat
from start_service import launch_chrome_debugger

DEEP_SCAN_JS = r"""
const selectors = arguments[0];

function isVisible(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  if (!style) return false;
  if (style.display === "none" || style.visibility === "hidden") return false;
  const rect = el.getBoundingClientRect();
  if (!rect) return false;
  return rect.width > 0 && rect.height > 0;
}

function scoreElement(el) {
  if (!el || !el.matches || !isVisible(el)) return -9999;
  if (el.matches("[disabled]") || el.getAttribute("aria-hidden") === "true") return -9999;
  const rect = el.getBoundingClientRect();
  if (!rect || rect.width < 80 || rect.height < 18) return -9999;

  const tagName = (el.tagName || "").toLowerCase();
  const roleAttr = (el.getAttribute("role") || "").toLowerCase();
  const inputLike =
    tagName === "textarea" ||
    tagName === "input" ||
    roleAttr === "textbox" ||
    el.isContentEditable ||
    (el.getAttribute("contenteditable") || "").toLowerCase() === "true";
  if (!inputLike) return -9999;

  const className = (el.className || "").toString().toLowerCase();
  const ariaLabel = (el.getAttribute("aria-label") || "").toLowerCase();
  const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
  const contextText = [className, ariaLabel, placeholder].join(" ");
  if (
    contextText.includes("terminal") ||
    contextText.includes("xterm") ||
    contextText.includes("native-edit-context")
  ) {
    return -9999;
  }
  if (el.closest(".terminal, .xterm, .xterm-screen, .xterm-helpers")) return -9999;
  if (el.closest(".monaco-editor")) return -9999;

  let score = -1000;
  for (const sel of selectors) {
    try {
      if (el.matches(sel)) score = Math.max(score, 0);
    } catch (_) {}
  }
  if (score < 0) return score;

  if (el.matches("textarea")) score += 60;
  if (el.matches("div[contenteditable='true']")) score += 40;
  if (el.matches("div[role='textbox']")) score += 30;
  if (el.matches("[aria-label='Chat']")) score += 70;

  const aria = ariaLabel;
  const role = roleAttr;
  if (aria.includes("chat") || aria.includes("prompt") || aria.includes("copilot") || aria.includes("codex")) score += 35;
  if (placeholder.includes("chat") || placeholder.includes("prompt") || placeholder.includes("ask")) score += 35;
  if (role === "textbox") score += 10;
  const parentHint = (el.closest("[aria-label], [class], [id]") || el);
  const parentText = [
    (parentHint.getAttribute("aria-label") || "").toLowerCase(),
    (parentHint.getAttribute("class") || "").toLowerCase(),
    (parentHint.getAttribute("id") || "").toLowerCase(),
  ].join(" ");
  if (
    parentText.includes("chat") ||
    parentText.includes("codex") ||
    parentText.includes("copilot")
  ) {
    score += 30;
  }
  const viewportWidth = window.innerWidth || 0;
  const centerX = rect.x + rect.width / 2;
  if (viewportWidth > 0) {
    if (centerX >= viewportWidth * 0.55) score += 45;
    if (centerX <= viewportWidth * 0.35) score -= 60;
  }
  if (aria.includes("terminal") || placeholder.includes("terminal")) score -= 200;
  return score;
}

function collectElementsDeep(node, out) {
  if (!node) return;
  if (node.nodeType === Node.ELEMENT_NODE) {
    out.push(node);
    if (node.shadowRoot) {
      collectElementsDeep(node.shadowRoot, out);
    }
  }

  const children = node.children || [];
  for (let i = 0; i < children.length; i += 1) {
    collectElementsDeep(children[i], out);
  }
}

const allElements = [];
collectElementsDeep(document.documentElement || document, allElements);

let best = null;
let bestScore = -9999;
for (const el of allElements) {
  const score = scoreElement(el);
  if (score > bestScore) {
    best = el;
    bestScore = score;
  }
}

if (!best || bestScore < 0) return null;

best.style.border = "2px solid red";
best.style.outline = "2px solid red";
best.style.outlineOffset = "1px";
best.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
return best;
"""

DEEP_CHAT_SELECTORS = [
    "textarea",
    "input[type='text']",
    "input:not([type])",
    "div[contenteditable='true']",
    "div[role='textbox']",
]

MESSAGE_FRAME_JS = r"""
const items = [...document.querySelectorAll('[data-content-search-unit-key]')];

return items.map((el) => {
  const key = el.getAttribute('data-content-search-unit-key') || '';
  const text = (el.innerText || el.textContent || '').replace(/\u00a0/g, ' ').trim();
  return { key, text };
}).filter((item) => item.text);
"""

THINKING_STATE_JS = r"""
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

function normalizeText(text) {
  return (text || '').replace(/\s+/g, ' ').trim();
}

const hits = [];
for (const el of document.querySelectorAll('*')) {
  if (!isVisible(el)) continue;
  const text = normalizeText(el.innerText || el.textContent || '');
  if (!text) continue;
  const lower = text.toLowerCase();
  if (lower !== 'thinking' && lower !== 'thinking...') continue;
  const attrs = {};
  for (const name of el.getAttributeNames()) attrs[name] = el.getAttribute(name);
  hits.push({
    tag: el.tagName.toLowerCase(),
    text,
    cls: (el.className || '').toString(),
    attrs,
  });
}
return { active: hits.length > 0, hits };
"""


IMAGE_DEBUG_DIR = Path(__file__).with_name("image_vscode")


def append_debug_log(log_path: Path, event: str, payload: dict | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event}"
    if payload is not None:
        line += " " + json.dumps(payload, ensure_ascii=False, default=str)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def save_debug_screenshot(driver: webdriver.Chrome, log_path: Path, label: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    IMAGE_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    png_path = IMAGE_DEBUG_DIR / f"{log_path.stem}_{label}_{stamp}.png"
    try:
        ok = driver.save_screenshot(str(png_path))
        append_debug_log(log_path, "screenshot", {"label": label, "ok": ok, "file": str(png_path)})
    except Exception as exc:
        append_debug_log(log_path, "screenshot_error", {"label": label, "error": str(exc)})


def paste_with_macos_system_events(message: str, press_enter: bool = True) -> bool:
    if platform.system().lower() != "darwin":
        return False

    try:
        subprocess.run(["pbcopy"], input=message, text=True, check=True)
        script_lines = [
            'tell application "System Events"',
            '  keystroke "v" using command down',
            "  delay 0.15",
        ]
        if press_enter:
            script_lines.append("  key code 36")
        script_lines.append("end tell")
        subprocess.run(
            [
                "osascript",
                "-e",
                "\n".join(script_lines),
            ],
            check=True,
        )
        return True
    except Exception as exc:
        print(f"Paste bang System Events that bai: {exc}")
        return False


def remove_outdated_chromedriver_from_path() -> None:
    parts = os.environ.get("PATH", "").split(":")
    filtered_parts = []
    removed_bins = []
    for part in parts:
        bin_path = shutil.which("chromedriver", path=part)
        if bin_path:
            removed_bins.append(bin_path)
            continue
        filtered_parts.append(part)

    if removed_bins:
        os.environ["PATH"] = ":".join(filtered_parts)
        print("Da bo chromedriver cu trong PATH de Selenium Manager tu lay driver dung.")
        for bin_path in removed_bins:
            print(f"  - bo qua: {bin_path}")


def parse_host_port(debugger_address: str) -> tuple[str, int]:
    host, port_str = debugger_address.split(":", 1)
    return host, int(port_str)


def get_browser_version_from_debugger(debugger_address: str) -> str | None:
    try:
        with urlopen(f"http://{debugger_address}/json/version", timeout=1.2) as resp:
            data = json.load(resp)
    except Exception:
        return None

    browser = str(data.get("Browser", ""))
    if "/" not in browser:
        return None
    return browser.split("/", 1)[1].strip()


def is_debug_port_ready(debugger_address: str, timeout: float = 0.4) -> bool:
    return is_debug_port_ready_with_retry(
        debugger_address=debugger_address,
        timeout=timeout,
        retries=1,
        retry_delay=0.0,
    )


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


def get_platform_label() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return "mac-arm64" if "arm" in machine else "mac-x64"
    if system == "linux":
        return "linux64"
    if system == "windows":
        return "win64"
    raise SystemExit(f"Nen tang khong duoc ho tro: {system}/{machine}")


def download_matching_chromedriver(browser_version: str) -> Path | None:
    major = browser_version.split(".", 1)[0]
    platform_label = get_platform_label()
    cache_dir = Path.home() / ".cache" / "codex_chromedriver" / major / platform_label
    driver_binary_name = "chromedriver.exe" if platform_label == "win64" else "chromedriver"
    cached = cache_dir / driver_binary_name
    if cached.exists():
        return cached

    meta_url = (
        "https://googlechromelabs.github.io/"
        "chrome-for-testing/latest-versions-per-milestone-with-downloads.json"
    )
    try:
        with urlopen(meta_url, timeout=10) as resp:
            metadata = json.load(resp)
    except Exception as exc:
        print(f"Khong tai duoc metadata chromedriver: {exc}")
        return None

    milestone = metadata.get("milestones", {}).get(major)
    if not milestone:
        print(f"Khong tim thay milestone chromedriver cho major={major}")
        return None

    download_url = None
    for item in milestone.get("downloads", {}).get("chromedriver", []):
        if item.get("platform") == platform_label:
            download_url = item.get("url")
            break
    if not download_url:
        print(f"Khong tim thay chromedriver cho platform={platform_label}")
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "chromedriver.zip"
    try:
        with urlopen(download_url, timeout=60) as resp:
            zip_path.write_bytes(resp.read())
    except Exception as exc:
        print(f"Tai chromedriver that bai: {exc}")
        return None

    try:
        import zipfile

        extracted_binary: Path | None = None
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                normalized = member.replace("\\", "/")
                basename = normalized.rsplit("/", 1)[-1].lower()
                if basename not in {"chromedriver", "chromedriver.exe"}:
                    continue

                zf.extract(member, cache_dir)
                src = cache_dir / normalized
                if not src.exists():
                    continue

                cached.parent.mkdir(parents=True, exist_ok=True)
                if cached.exists():
                    cached.unlink()
                src.rename(cached)
                extracted_binary = cached
                break

        if extracted_binary is None:
            print("Khong thay binary chromedriver trong file zip.")
            return None
    except Exception as exc:
        print(f"Giai nen chromedriver that bai: {exc}")
        return None
    finally:
        if zip_path.exists():
            zip_path.unlink()

    if platform_label != "win64":
        cached.chmod(0o755)
    print(f"Da tai chromedriver {major} ve: {cached}")
    return cached


def attach_driver(debugger_address: str) -> webdriver.Chrome:
    if not is_debug_port_ready_with_retry(
        debugger_address=debugger_address,
        timeout=0.8,
        retries=2,
        retry_delay=0.5,
    ):
        raise SystemExit(
            f"Khong ket noi duoc DevTools target tai {debugger_address}.\n"
            "Debug port chua san sang. Hay mo VS Code/Electron voi "
            "--remote-debugging-port hoac them --auto-launch-chrome."
        )

    remove_outdated_chromedriver_from_path()
    options = Options()
    options.add_experimental_option("debuggerAddress", debugger_address)
    service = None
    browser_version = get_browser_version_from_debugger(debugger_address)
    if browser_version:
        custom_driver = download_matching_chromedriver(browser_version)
        if custom_driver:
            service = Service(executable_path=str(custom_driver))

    try:
        if service:
            return webdriver.Chrome(service=service, options=options)
        return webdriver.Chrome(options=options)
    except WebDriverException as exc:
        detail = str(exc).splitlines()[0] if str(exc) else repr(exc)
        raise SystemExit(
            "Khong ket noi duoc DevTools target.\n"
            "Hay mo VS Code/Electron voi --remote-debugging-port "
            f"va dung --debugger-address {debugger_address}.\n"
            f"Chi tiet loi: {detail}"
        ) from exc


def _switch_to_frame_path(driver: webdriver.Chrome, frame_path: list[int]) -> bool:
    driver.switch_to.default_content()
    for frame_index in frame_path:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if frame_index >= len(frames):
            return False
        driver.switch_to.frame(frames[frame_index])
    return True


def parse_role(key: str) -> str:
    parts = key.rsplit(':', 1)
    if len(parts) != 2:
        return 'unknown'
    return parts[1].strip().lower() or 'unknown'


def normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: list[str] = []
    blank_streak = 0
    for line in lines:
        if line.strip():
            cleaned.append(line.strip())
            blank_streak = 0
            continue
        blank_streak += 1
        if blank_streak == 1:
            cleaned.append('')
    return '\n'.join(cleaned).strip()


def read_message_units(driver: webdriver.Chrome, frame_path: list[int]) -> list[dict]:
    if not _switch_to_frame_path(driver, frame_path):
        return []

    raw_items = driver.execute_script(MESSAGE_FRAME_JS) or []
    results: list[dict] = []
    for item in raw_items:
        key = str(item.get('key', '')).strip()
        text = normalize_text(str(item.get('text', '')))
        if not key or not text:
            continue
        results.append(
            {
                'key': key,
                'role': parse_role(key),
                'text': text,
            }
        )
    return results


def latest_assistant_message(messages: list[dict]) -> dict | None:
    for item in reversed(messages):
        if item.get('role') == 'assistant':
            return item
    return None


def assistant_signature(message: dict | None) -> tuple[str, str]:
    if not message:
        return '', ''
    return str(message.get('key', '')), str(message.get('text', ''))


def _scan_frame_tree_for_message_path(
    driver: webdriver.Chrome,
    depth: int,
    max_depth: int,
    frame_path: list[int],
) -> list[int] | None:
    try:
        items = driver.execute_script(MESSAGE_FRAME_JS) or []
    except Exception:
        items = []
    if items:
        return frame_path

    if depth >= max_depth:
        return None

    child_frames = driver.find_elements(By.CSS_SELECTOR, 'iframe, frame')
    for frame_index, frame in enumerate(child_frames):
        try:
            driver.switch_to.frame(frame)
            found = _scan_frame_tree_for_message_path(
                driver=driver,
                depth=depth + 1,
                max_depth=max_depth,
                frame_path=frame_path + [frame_index],
            )
            driver.switch_to.parent_frame()
            if found is not None:
                return found
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                _switch_to_frame_path(driver, frame_path)
    return None


def find_message_frame_path(driver: webdriver.Chrome, max_depth: int = 8) -> list[int] | None:
    driver.switch_to.default_content()
    found_path = _scan_frame_tree_for_message_path(driver, 0, max_depth, [])
    driver.switch_to.default_content()
    return found_path


def get_thinking_state(driver: webdriver.Chrome, frame_path: list[int]) -> dict:
    if not _switch_to_frame_path(driver, frame_path):
        return {'active': False, 'hits': []}
    try:
        state = driver.execute_script(THINKING_STATE_JS) or {}
    except Exception:
        return {'active': False, 'hits': []}
    return {
        'active': bool(state.get('active')),
        'hits': state.get('hits', []),
    }


def wait_for_reply_completion(
    driver: webdriver.Chrome,
    frame_path: list[int],
    previous_signature: tuple[str, str],
    timeout: float,
    poll_interval: float,
    stable_for: float,
    post_delay: float,
) -> tuple[dict | None, list[dict], dict]:
    start = time.time()
    seen_thinking = False
    stable_since: float | None = None
    candidate_signature = ('', '')
    last_messages: list[dict] = []
    last_state = {'active': False, 'hits': []}

    while True:
        state = get_thinking_state(driver, frame_path)
        last_state = state
        if state['active']:
            seen_thinking = True

        messages = read_message_units(driver, frame_path)
        last_messages = messages
        latest = latest_assistant_message(messages)
        signature = assistant_signature(latest)

        if seen_thinking:
            if not state['active'] and signature != previous_signature:
                if post_delay > 0:
                    time.sleep(post_delay)
                messages = read_message_units(driver, frame_path)
                latest = latest_assistant_message(messages)
                return latest, messages, {
                    'seen_thinking': True,
                    'thinking_done': True,
                    'post_delay': post_delay,
                    'state': last_state,
                }
        elif signature != previous_signature:
            if signature == candidate_signature:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= stable_for:
                    if post_delay > 0:
                        time.sleep(post_delay)
                    messages = read_message_units(driver, frame_path)
                    latest = latest_assistant_message(messages)
                    return latest, messages, {
                        'seen_thinking': False,
                        'thinking_done': False,
                        'post_delay': post_delay,
                        'state': last_state,
                    }
            else:
                candidate_signature = signature
                stable_since = time.time()

        if time.time() - start >= timeout:
            return latest, last_messages, {
                'seen_thinking': seen_thinking,
                'thinking_done': not last_state.get('active', False),
                'post_delay': 0.0,
                'state': last_state,
            }

        time.sleep(poll_interval)


def _scan_frame_tree_for_path(
    driver: webdriver.Chrome,
    depth: int,
    max_depth: int,
    frame_path: list[int],
) -> list[int] | None:
    try:
        found = driver.execute_script(DEEP_SCAN_JS, DEEP_CHAT_SELECTORS)
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
            result = _scan_frame_tree_for_path(
                driver=driver,
                depth=depth + 1,
                max_depth=max_depth,
                frame_path=frame_path + [frame_index],
            )
            driver.switch_to.parent_frame()
            if result is not None:
                return result
        except Exception as exc:
            print(f"Bo qua frame {frame_path + [frame_index]}, loi: {exc}")
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                _switch_to_frame_path(driver, frame_path)
    return None


def find_chat_input_deep_scan(
    driver: webdriver.Chrome, max_depth: int = 8
) -> tuple[object | None, list[int] | None]:
    driver.switch_to.default_content()
    found_path = _scan_frame_tree_for_path(
        driver=driver,
        depth=0,
        max_depth=max_depth,
        frame_path=[],
    )
    if found_path is None:
        return None, None

    if not _switch_to_frame_path(driver, found_path):
        return None, None

    try:
        return driver.execute_script(DEEP_SCAN_JS, DEEP_CHAT_SELECTORS), found_path
    except Exception:
        return None, found_path


def send_message(
    driver: webdriver.Chrome,
    message: str,
    fallback_wait: float,
    log_path: Path,
    press_enter: bool = True,
    image_path: Path | None = None,
) -> bool:
    append_debug_log(
        log_path,
        "send_message_start",
        {
            "message_len": len(message),
            "fallback_wait": fallback_wait,
            "press_enter": press_enter,
            "has_image": image_path is not None,
            "image_path": str(image_path) if image_path else "",
            "url": driver.current_url,
            "title": driver.title,
        },
    )
    driver.switch_to.default_content()
    chat_input, frame_path = find_chat_input_deep_scan(driver)
    append_debug_log(
        log_path,
        "deep_scan_result",
        {"found": chat_input is not None, "frame_path": frame_path},
    )
    if chat_input is not None:
        try:
            info = driver.execute_script(
                """
                const el = arguments[0];
                const r = el.getBoundingClientRect();
                return {
                  tag: (el.tagName || '').toLowerCase(),
                  role: el.getAttribute('role') || '',
                  aria: el.getAttribute('aria-label') || '',
                  cls: (el.className || '').toString().slice(0, 120),
                  html: (el.outerHTML || '').slice(0, 500),
                  x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)
                };
                """,
                chat_input,
            )
            print(f"Deep-scan target: {info}")
            append_debug_log(log_path, "deep_scan_target", {"frame_path": frame_path, "info": info})
            save_debug_screenshot(driver, log_path, "target_found")

            driver.execute_script("arguments[0].focus();", chat_input)
            try:
                chat_input.clear()
            except Exception:
                pass
            if image_path is not None:
                image_ok, image_meta = send_image_to_chat(
                    driver=driver,
                    image_path=image_path,
                    chat_input=chat_input,
                    frame_path=frame_path,
                    press_enter=False,
                )
                append_debug_log(log_path, "send_image_deep_scan", {"ok": image_ok, "meta": image_meta})
                if not image_ok:
                    raise RuntimeError(f"khong gui duoc image: {image_meta}")
            if message:
                chat_input.send_keys(message)
                typed_ok = driver.execute_script(
                    """
                    const el = arguments[0], msg = arguments[1];
                    const value = (el.value || '').toString();
                    const text = (el.textContent || '').toString();
                    return value.includes(msg) || text.includes(msg);
                    """,
                    chat_input,
                    message,
                )
                append_debug_log(log_path, "typed_check", {"typed_ok": bool(typed_ok)})
                if not typed_ok:
                    raise RuntimeError("khong xac nhan duoc text da vao o chat")
            if press_enter:
                chat_input.send_keys(Keys.ENTER)
            append_debug_log(log_path, "deep_scan_send_success", {"frame_path": frame_path})
            print("Da gui prompt qua deep scan (shadow dom + nested iframes).")
            return True
        except Exception as exc:
            print(f"Tim thay chat input nhung gui that bai: {exc}")
            append_debug_log(
                log_path,
                "deep_scan_send_error",
                {"error": str(exc), "traceback": traceback.format_exc()},
            )
            save_debug_screenshot(driver, log_path, "deep_scan_failed")

    print("Khong tim thay o chat tu dong.")
    print(f"Ban hay click thu cong vao o chat trong {fallback_wait}s...")
    append_debug_log(log_path, "fallback_manual_wait", {"seconds": fallback_wait})
    time.sleep(fallback_wait)

    try:
        driver.switch_to.default_content()
        active = driver.switch_to.active_element
        tag = (active.tag_name or "").lower()
        editable = str(active.get_attribute("contenteditable")).lower() == "true"
        if tag not in {"input", "textarea"} and not editable:
            raise RuntimeError(f"active_element khong phai o nhap (tag={tag})")
        if image_path is not None:
            image_ok, image_meta = send_image_to_chat(
                driver=driver,
                image_path=image_path,
                chat_input=active,
                frame_path=[],
                press_enter=False,
            )
            append_debug_log(log_path, "send_image_active_element", {"ok": image_ok, "meta": image_meta})
            if not image_ok:
                raise RuntimeError(f"khong gui duoc image: {image_meta}")

        if message:
            active.send_keys(message)
        if press_enter:
            active.send_keys(Keys.ENTER)
        append_debug_log(log_path, "fallback_active_element_success", {"tag": tag, "editable": editable})
        save_debug_screenshot(driver, log_path, "active_element_sent")
        print("Da gui prompt qua active element.")
        return True
    except Exception as exc:
        print(f"Active element gui that bai, chuyen qua paste he thong: {exc}")
        append_debug_log(log_path, "fallback_active_element_error", {"error": str(exc)})
        if image_path is not None:
            append_debug_log(
                log_path,
                "fallback_image_required_but_failed",
                {"error": str(exc), "image_path": str(image_path)},
            )
            print("Co --image nhung gui anh that bai, dung de tranh gui nham chi co text.")
            return False
        if paste_with_macos_system_events(message, press_enter=press_enter):
            append_debug_log(log_path, "fallback_system_paste_success")
            save_debug_screenshot(driver, log_path, "system_paste_sent")
            print("Da paste prompt bang ban phim he thong.")
            return True
        append_debug_log(log_path, "fallback_system_paste_error")
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Thu gui prompt vao VS Code webview bang Selenium."
    )
    parser.add_argument(
        "--debugger-address",
        default="127.0.0.1:9222",
        help="Dia chi DevTools target da bat remote debugging.",
    )
    parser.add_argument(
        "--message",
        default="Viet cho toi 1 ham Java check root Android don gian",
        help="Noi dung prompt can gui.",
    )
    parser.add_argument(
        "--image",
        default="",
        help="Duong dan file anh can gui kem (chi gui anh khi co tham so nay).",
    )
    parser.add_argument(
        "--fallback-wait",
        type=float,
        default=5.0,
        help="So giay cho ban click tay vao o chat neu auto tim that bai.",
    )
    parser.add_argument(
        "--keep-open",
        type=float,
        default=2.0,
        help="So giay giu script truoc khi ket thuc.",
    )
    parser.add_argument(
        "--auto-launch-chrome",
        action="store_true",
        help="Neu port debugger chua mo thi tu dong mo VS Code/Electron voi remote debugging.",
    )
    parser.add_argument(
        "--startup-wait",
        type=float,
        default=2.0,
        help="So giay cho app debug khoi dong truoc khi attach.",
    )
    parser.add_argument(
        "--no-enter",
        action="store_true",
        help="Chi nhap prompt, khong bam Enter (de kiem tra o chat).",
    )
    parser.add_argument(
        "--debug-log",
        default=str(Path(__file__).with_name("deep_scan_debug.txt")),
        help="File txt luu log debug moi lan chay.",
    )
    parser.add_argument(
        "--no-wait-reply",
        action="store_true",
        help="Chi gui prompt, khong doi cau tra loi moi.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="So giay toi da de doi cau tra loi moi.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Chu ky poll cau tra loi.",
    )
    parser.add_argument(
        "--stable-for",
        type=float,
        default=2.0,
        help="Fallback neu khong bat duoc Thinking: doi reply on dinh bao lau.",
    )
    parser.add_argument(
        "--post-delay",
        type=float,
        default=1.0,
        help="Delay them bao lau sau khi Thinking ket thuc truoc khi doc reply.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).with_name("latest_codex_reply.txt")),
        help="File txt de luu cau tra loi moi nhat.",
    )
    parser.add_argument(
        "--json-output",
        default=str(Path(__file__).with_name("latest_codex_reply.json")),
        help="File json de luu metadata va lich su message vua doc duoc.",
    )
    parser.add_argument(
        "--print-all",
        action="store_true",
        help="In toan bo message units doc duoc de debug.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    log_path = Path(args.debug_log).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    json_output_path = Path(args.json_output).expanduser().resolve()
    image_path: Path | None = None
    if args.image:
        image_path = Path(args.image).expanduser().resolve()
        if not image_path.exists() or not image_path.is_file():
            print(f"Khong tim thay file image: {image_path}")
            return 1

    append_debug_log(
        log_path,
        "run_start",
        {
            "debugger_address": args.debugger_address,
            "fallback_wait": args.fallback_wait,
            "keep_open": args.keep_open,
            "auto_launch": args.auto_launch_chrome,
            "startup_wait": args.startup_wait,
            "no_enter": args.no_enter,
            "no_wait_reply": args.no_wait_reply,
            "message_len": len(args.message),
            "has_image": image_path is not None,
            "image_path": str(image_path) if image_path else "",
            "timeout": args.timeout,
        },
    )

    debug_target_ready = is_debug_port_ready_with_retry(
        debugger_address=args.debugger_address,
        timeout=0.8,
        retries=4,
        retry_delay=1.0,
    )
    if not debug_target_ready:
        print(f"Chua thay debug target tai {args.debugger_address}.")
        if args.auto_launch_chrome:
            debug_target_ready = launch_chrome_debugger(
                args.debugger_address, args.startup_wait
            )
            if not debug_target_ready:
                print("Tu mo VS Code that bai hoac port van chua san sang.")
        else:
            print(
                "Hay tu mo target voi --remote-debugging-port hoac them --auto-launch-chrome."
            )
    if not debug_target_ready:
        append_debug_log(
            log_path,
            "run_abort_no_debug_target",
            {"debugger_address": args.debugger_address},
        )
        return 1

    print(f"Dang attach debugger tai {args.debugger_address} ...")
    driver = attach_driver(args.debugger_address)
    reply_frame_path: list[int] | None = None
    previous_signature = ('', '')

    try:
        if not args.no_wait_reply and not args.no_enter:
            reply_frame_path = find_message_frame_path(driver)
            if reply_frame_path is not None:
                before_messages = read_message_units(driver, reply_frame_path)
                previous_signature = assistant_signature(
                    latest_assistant_message(before_messages)
                )
            else:
                append_debug_log(
                    log_path,
                    "reply_frame_not_found_before_send",
                    {"debugger_address": args.debugger_address},
                )

        ok = send_message(
            driver=driver,
            message=args.message,
            fallback_wait=args.fallback_wait,
            log_path=log_path,
            press_enter=not args.no_enter,
            image_path=image_path,
        )
        if not ok:
            append_debug_log(log_path, "run_done", {"ok": False})
            print("Khong gui duoc prompt.")
            return 1

        if args.no_enter:
            append_debug_log(log_path, "run_done", {"ok": True, "no_enter": True})
            print("Da nhap prompt, bo qua doi reply vi --no-enter.")
            return 0

        if args.no_wait_reply:
            append_debug_log(log_path, "run_done", {"ok": True, "wait_reply": False})
            print("Da gui prompt. Bo qua doi reply vi --no-wait-reply.")
            return 0

        if reply_frame_path is None:
            reply_frame_path = find_message_frame_path(driver)
        if reply_frame_path is None:
            append_debug_log(
                log_path,
                "run_done",
                {"ok": False, "reason": "reply_frame_not_found"},
            )
            print("Da gui prompt nhung khong tim thay frame chat de doi reply.")
            return 1

        latest, messages, meta = wait_for_reply_completion(
            driver=driver,
            frame_path=reply_frame_path,
            previous_signature=previous_signature,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            stable_for=args.stable_for,
            post_delay=args.post_delay,
        )
        if not latest or assistant_signature(latest) == previous_signature:
            append_debug_log(
                log_path,
                "run_done",
                {"ok": False, "reason": "no_new_reply", "meta": meta},
            )
            print("Khong thay cau tra loi moi trong thoi gian cho.")
            return 1

        output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(latest["text"] + "\n", encoding="utf-8")
        json_output_path.write_text(
            json.dumps(
                {
                    "debugger_address": args.debugger_address,
                    "prompt": args.message,
                    "image_path": str(image_path) if image_path else "",
                    "frame_path": reply_frame_path,
                    "latest": latest,
                    "messages": messages,
                    "meta": meta,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        print(latest["text"])
        print(f"\nDa luu txt: {output_path}")
        print(f"Da luu json: {json_output_path}")
        if args.print_all:
            print("\nTat ca message units:")
            for index, item in enumerate(messages, start=1):
                print(f"[{index}] {item['role']} {item['key']}")
                print(item["text"])
                print("-" * 60)

        append_debug_log(
            log_path,
            "run_done",
            {
                "ok": True,
                "reply_len": len(latest["text"]),
                "seen_thinking": meta.get("seen_thinking"),
            },
        )
        return 0
    finally:
        if args.keep_open > 0:
            time.sleep(args.keep_open)
            print("Ket thuc script, dong trinh duyet debug.")
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
