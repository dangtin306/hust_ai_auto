#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from selenium.webdriver.common.by import By

from main_service import attach_driver


MESSAGE_FRAME_JS = r"""
const items = [...document.querySelectorAll('[data-content-search-unit-key]')];

return items.map((el) => {
  const key = el.getAttribute('data-content-search-unit-key') || '';
  const text = (el.innerText || el.textContent || '').replace(/\u00a0/g, ' ').trim();
  return { key, text };
}).filter((item) => item.text);
"""


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


def switch_to_frame_path(driver, frame_path: list[int]) -> bool:
    driver.switch_to.default_content()
    for frame_index in frame_path:
        frames = driver.find_elements(By.CSS_SELECTOR, 'iframe, frame')
        if frame_index >= len(frames):
            return False
        driver.switch_to.frame(frames[frame_index])
    return True


def scan_for_message_frame(driver, depth: int, max_depth: int, frame_path: list[int]) -> list[int] | None:
    try:
        items = driver.execute_script(MESSAGE_FRAME_JS)
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
            found = scan_for_message_frame(driver, depth + 1, max_depth, frame_path + [frame_index])
            driver.switch_to.parent_frame()
            if found is not None:
                return found
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                switch_to_frame_path(driver, frame_path)
    return None


def find_message_frame_path(driver, max_depth: int = 8) -> list[int] | None:
    driver.switch_to.default_content()
    found_path = scan_for_message_frame(driver, 0, max_depth, [])
    driver.switch_to.default_content()
    return found_path


def read_message_units(driver, frame_path: list[int]) -> list[dict]:
    if not switch_to_frame_path(driver, frame_path):
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


def wait_for_stable_assistant_message(
    driver,
    frame_path: list[int],
    timeout: float,
    poll_interval: float,
    stable_for: float,
) -> tuple[dict | None, list[dict]]:
    start = time.time()
    latest_key = ''
    latest_text = ''
    stable_since: float | None = None
    last_messages: list[dict] = []

    while True:
        messages = read_message_units(driver, frame_path)
        last_messages = messages
        latest = latest_assistant_message(messages)
        if latest:
            key = latest['key']
            text = latest['text']
            if key == latest_key and text == latest_text:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= stable_for:
                    return latest, messages
            else:
                latest_key = key
                latest_text = text
                stable_since = time.time()

        if time.time() - start >= timeout:
            return latest, last_messages
        time.sleep(poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Nhay vao Codex chat trong VS Code va nhat cau tra loi moi nhat.'
    )
    parser.add_argument(
        '--debugger-address',
        default='127.0.0.1:9222',
        help='Dia chi DevTools target dang mo remote debugging.',
    )
    parser.add_argument(
        '--output',
        default=str(Path(__file__).with_name('latest_codex_reply.txt')),
        help='File txt de luu cau tra loi moi nhat.',
    )
    parser.add_argument(
        '--json-output',
        default=str(Path(__file__).with_name('latest_codex_reply.json')),
        help='File json de luu metadata va lich su message vua doc duoc.',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=30.0,
        help='So giay toi da de doi reply on dinh.',
    )
    parser.add_argument(
        '--poll-interval',
        type=float,
        default=1.0,
        help='Chu ky poll chat khi dang doi reply.',
    )
    parser.add_argument(
        '--stable-for',
        type=float,
        default=2.0,
        help='Can reply giu nguyen trong bao lau moi xem la xong.',
    )
    parser.add_argument(
        '--print-all',
        action='store_true',
        help='In toan bo message units doc duoc de debug.',
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_path = Path(args.output).expanduser().resolve()
    json_output_path = Path(args.json_output).expanduser().resolve()

    driver = attach_driver(args.debugger_address)
    try:
        frame_path = find_message_frame_path(driver)
        if frame_path is None:
            print('Khong tim thay frame chat co message.')
            return 1

        latest, messages = wait_for_stable_assistant_message(
            driver=driver,
            frame_path=frame_path,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            stable_for=args.stable_for,
        )
        if not latest:
            print('Khong doc duoc cau tra loi assistant nao.')
            return 1

        output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(latest['text'] + '\n', encoding='utf-8')
        json_output_path.write_text(
            json.dumps(
                {
                    'debugger_address': args.debugger_address,
                    'frame_path': frame_path,
                    'latest': latest,
                    'messages': messages,
                },
                ensure_ascii=False,
                indent=2,
            )
            + '\n',
            encoding='utf-8',
        )

        print(latest['text'])
        print(f'\nDa luu txt: {output_path}')
        print(f'Da luu json: {json_output_path}')
        if args.print_all:
            print('\nTat ca message units:')
            for index, item in enumerate(messages, start=1):
                print(f"[{index}] {item['role']} {item['key']}")
                print(item['text'])
                print('-' * 60)
        return 0
    finally:
        driver.quit()


if __name__ == '__main__':
    raise SystemExit(main())
