#!/usr/bin/env python3
from __future__ import annotations

"""
Backward-compatible entrypoint.
Primary implementation now lives in send_and_get_result.py.
"""

import send_and_get_result as _impl

# Backward compatibility for any external imports from main_service.py
append_debug_log = _impl.append_debug_log
attach_driver = _impl.attach_driver
is_debug_port_ready_with_retry = _impl.is_debug_port_ready_with_retry
launch_chrome_debugger = _impl.launch_chrome_debugger
send_message = _impl.send_message
build_parser = _impl.build_parser
main = _impl.main


if __name__ == "__main__":
    raise SystemExit(main())
