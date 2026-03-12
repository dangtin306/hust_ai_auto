#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from get_result import find_message_frame_path, latest_assistant_message, read_message_units
from wait_for_completion import assistant_signature, wait_for_completion
from main_service import (
    append_debug_log,
    attach_driver,
    is_debug_port_ready_with_retry,
    launch_chrome_debugger,
    send_message,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Gui prompt vao Codex roi nhat cau tra loi moi nhat trong mot lenh.'
    )
    parser.add_argument(
        '--debugger-address',
        default='127.0.0.1:9222',
        help='Dia chi DevTools target dang mo remote debugging.',
    )
    parser.add_argument(
        '--message',
        required=True,
        help='Noi dung prompt can gui.',
    )
    parser.add_argument(
        '--fallback-wait',
        type=float,
        default=5.0,
        help='So giay cho ban click tay vao o chat neu auto tim that bai.',
    )
    parser.add_argument(
        '--auto-launch-chrome',
        action='store_true',
        help='Neu port debugger chua mo thi tu dong mo VS Code/Electron voi remote debugging.',
    )
    parser.add_argument(
        '--startup-wait',
        type=float,
        default=2.0,
        help='So giay cho app debug khoi dong truoc khi attach.',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=60.0,
        help='So giay toi da de doi cau tra loi moi.',
    )
    parser.add_argument(
        '--poll-interval',
        type=float,
        default=1.0,
        help='Chu ky poll cau tra loi.',
    )
    parser.add_argument(
        '--stable-for',
        type=float,
        default=2.0,
        help='Can reply giu nguyen trong bao lau moi xem la xong.',
    )
    parser.add_argument(
        '--keep-open',
        type=float,
        default=0.0,
        help='So giay giu script truoc khi ket thuc.',
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
        '--debug-log',
        default=str(Path(__file__).with_name('deep_scan_debug.txt')),
        help='File txt luu log debug moi lan chay.',
    )
    parser.add_argument(
        '--no-enter',
        action='store_true',
        help='Chi nhap prompt, khong bam Enter.',
    )
    parser.add_argument(
        '--post-delay',
        type=float,
        default=1.0,
        help='Delay them bao lau sau khi Codex het Thinking truoc khi doc ket qua.',
    )
    parser.add_argument(
        '--print-all',
        action='store_true',
        help='In toan bo message units doc duoc de debug.',
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    log_path = Path(args.debug_log).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    json_output_path = Path(args.json_output).expanduser().resolve()

    append_debug_log(
        log_path,
        'send_and_get_result_start',
        {
            'debugger_address': args.debugger_address,
            'message_len': len(args.message),
            'timeout': args.timeout,
            'no_enter': args.no_enter,
            'auto_launch': args.auto_launch_chrome,
        },
    )

    debug_target_ready = is_debug_port_ready_with_retry(
        debugger_address=args.debugger_address,
        timeout=0.8,
        retries=4,
        retry_delay=1.0,
    )
    if not debug_target_ready:
        print(f'Chua thay debug target tai {args.debugger_address}.')
        if args.auto_launch_chrome:
            debug_target_ready = launch_chrome_debugger(
                args.debugger_address, args.startup_wait
            )
            if not debug_target_ready:
                print('Tu mo VS Code that bai hoac port van chua san sang.')
        else:
            print('Hay tu mo target voi --remote-debugging-port hoac them --auto-launch-chrome.')
    if not debug_target_ready:
        append_debug_log(
            log_path,
            'send_and_get_result_abort_no_debug_target',
            {'debugger_address': args.debugger_address},
        )
        return 1

    driver = attach_driver(args.debugger_address)
    try:
        frame_path = find_message_frame_path(driver)
        if frame_path is None:
            print('Khong tim thay frame chat co message.')
            return 1

        before_messages = read_message_units(driver, frame_path)
        previous = latest_assistant_message(before_messages)
        previous_signature = assistant_signature(previous)

        ok = send_message(
            driver=driver,
            message=args.message,
            fallback_wait=args.fallback_wait,
            log_path=log_path,
            press_enter=not args.no_enter,
        )
        if not ok:
            print('Khong gui duoc prompt.')
            return 1

        if args.no_enter:
            print('Da nhap prompt, bo qua buoc nhat ket qua vi dang --no-enter.')
            return 0

        latest, messages, meta = wait_for_completion(
            driver=driver,
            frame_path=frame_path,
            previous_signature=previous_signature,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            stable_for=args.stable_for,
            post_delay=args.post_delay,
        )
        if not latest or assistant_signature(latest) == previous_signature:
            print('Khong thay cau tra loi moi trong thoi gian cho.')
            return 1

        output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(latest['text'] + '\n', encoding='utf-8')
        json_output_path.write_text(
            json.dumps(
                {
                    'debugger_address': args.debugger_address,
                    'prompt': args.message,
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
        if args.print_all:
            print('\nTat ca message units:')
            for index, item in enumerate(messages, start=1):
                print(f"[{index}] {item['role']} {item['key']}")
                print(item['text'])
                print('-' * 60)

        append_debug_log(log_path, 'send_and_get_result_done', {'ok': True, 'reply_len': len(latest['text']), 'seen_thinking': meta.get('seen_thinking')})
        if args.keep_open > 0:
            time.sleep(args.keep_open)
        return 0
    finally:
        driver.quit()


if __name__ == '__main__':
    raise SystemExit(main())
