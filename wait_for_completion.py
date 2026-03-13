#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from selenium.webdriver.common.by import By

from get_result import find_message_frame_path, latest_assistant_message, read_message_units
from send_and_get_result import attach_driver


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


def assistant_signature(message: dict | None) -> tuple[str, str]:
    if not message:
        return '', ''
    return str(message.get('key', '')), str(message.get('text', ''))


def switch_to_frame_path(driver, frame_path: list[int]) -> bool:
    driver.switch_to.default_content()
    for frame_index in frame_path:
        frames = driver.find_elements(By.CSS_SELECTOR, 'iframe, frame')
        if frame_index >= len(frames):
            return False
        driver.switch_to.frame(frames[frame_index])
    return True


def get_thinking_state(driver, frame_path: list[int]) -> dict:
    if not switch_to_frame_path(driver, frame_path):
        return {'active': False, 'hits': []}
    try:
        state = driver.execute_script(THINKING_STATE_JS) or {}
    except Exception:
        return {'active': False, 'hits': []}
    return {
        'active': bool(state.get('active')),
        'hits': state.get('hits', []),
    }


def wait_for_completion(
    driver,
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
                last_messages = messages
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
                    last_messages = messages
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Doi Codex het Thinking, delay them 1s, roi nhat cau tra loi moi nhat.'
    )
    parser.add_argument(
        '--debugger-address',
        default='127.0.0.1:9222',
        help='Dia chi DevTools target dang mo remote debugging.',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=60.0,
        help='So giay toi da de doi Codex nghi xong.',
    )
    parser.add_argument(
        '--poll-interval',
        type=float,
        default=0.5,
        help='Chu ky poll trang thai Thinking.',
    )
    parser.add_argument(
        '--stable-for',
        type=float,
        default=2.0,
        help='Fallback neu khong bat duoc Thinking: doi reply on dinh bao lau.',
    )
    parser.add_argument(
        '--post-delay',
        type=float,
        default=1.0,
        help='Delay them bao lau sau khi het Thinking truoc khi doc ket qua.',
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

        before_messages = read_message_units(driver, frame_path)
        previous = latest_assistant_message(before_messages)
        initial_state = get_thinking_state(driver, frame_path)

        if not initial_state['active']:
            if args.post_delay > 0:
                time.sleep(args.post_delay)
            messages = read_message_units(driver, frame_path)
            latest = latest_assistant_message(messages)
            meta = {
                'seen_thinking': False,
                'thinking_done': True,
                'post_delay': args.post_delay,
                'state': initial_state,
                'immediate': True,
            }
        else:
            previous_signature = assistant_signature(previous)
            latest, messages, meta = wait_for_completion(
                driver=driver,
                frame_path=frame_path,
                previous_signature=previous_signature,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
                stable_for=args.stable_for,
                post_delay=args.post_delay,
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
                    'meta': meta,
                },
                ensure_ascii=False,
                indent=2,
            ) + '\n',
            encoding='utf-8',
        )

        print(latest['text'])
        print(f'\nDa luu txt: {output_path}')
        print(f'Da luu json: {json_output_path}')
        print(f"Thinking active da thay: {meta['seen_thinking']}")
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
