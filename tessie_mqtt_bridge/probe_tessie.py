"""Standalone Tessie Fleet Telemetry probe — no MQTT, no HA.

Connects to wss://streaming.tessie.com/<VIN>, prints decoded messages, and
summarizes which fields the car is actually streaming. Use this to verify:

  1. The Tessie token is valid (no 401).
  2. Fleet Telemetry is enabled in Tessie console for this VIN.
  3. The expected fields (Location / Gear / VehicleSpeed) are flowing.
  4. The on-the-wire JSON shape (camelCase vs snake_case keys, value
     wrappers like locationValue/doubleValue/etc.) — useful if Tesla
     ever drifts the schema and the bridge starts mis-parsing.

Usage:

  cd tessie_mqtt_bridge
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  set -a; source .env; set +a
  python probe_tessie.py              # runs until Ctrl-C
  python probe_tessie.py --seconds 60 # runs for 60s then exits with a summary

Prints raw JSON for the first 3 messages so you can eyeball the envelope, then
switches to compact one-liners. Always prints a summary on exit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

# Auto-load .env then .env.local from this script's directory.
try:
    from dotenv import load_dotenv
    _HERE = Path(__file__).parent
    load_dotenv(_HERE / ".env")
    load_dotenv(_HERE / ".env.local", override=True)
except ImportError:
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def redact_token(tok: str) -> str:
    if not tok:
        return "<empty>"
    if len(tok) <= 8:
        return "*" * len(tok)
    return f"{tok[:4]}…{tok[-4:]}"


async def probe(vin: str, token: str, max_seconds: int | None) -> int:
    url = f"wss://streaming.tessie.com/{vin}"
    print(f"[{now_iso()}] connecting to {url}")
    print(f"[{now_iso()}] using token {redact_token(token)}")

    field_counter: Counter[str] = Counter()
    total_messages = 0
    raw_dumps_remaining = 3
    started_at = time.monotonic()
    stop_event = asyncio.Event()

    def _stop(_sig=None) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    try:
        async with websockets.connect(
            url,
            extra_headers={"Authorization": f"Bearer {token}"},
            ping_interval=30,
            ping_timeout=30,
            close_timeout=5,
            max_size=2_000_000,
        ) as ws:
            print(f"[{now_iso()}] connected. waiting for messages…")
            print(
                f"[{now_iso()}] (if the car is asleep, expect silence; "
                f"wake it via the Tesla app to test)"
            )

            async def reader() -> None:
                nonlocal total_messages, raw_dumps_remaining
                async for raw in ws:
                    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    total_messages += 1
                    try:
                        msg = json.loads(text)
                    except json.JSONDecodeError:
                        print(f"[{now_iso()}] non-JSON: {text[:200]!r}")
                        continue

                    if raw_dumps_remaining > 0:
                        print(f"\n[{now_iso()}] --- raw message #{total_messages} ---")
                        print(json.dumps(msg, indent=2)[:2000])
                        raw_dumps_remaining -= 1
                        continue

                    # Compact summary of subsequent messages.
                    summary_parts: list[str] = []
                    if "status" in msg:
                        summary_parts.append(f"status={msg['status']}")
                    if "errors" in msg:
                        summary_parts.append(f"errors={msg['errors']}")
                    data = msg.get("data") or []
                    for d in data:
                        key = d.get("key", "?")
                        field_counter[key] += 1
                        v = d.get("value", {})
                        if isinstance(v, dict):
                            # Show the value-wrapper key + a short repr of its content.
                            for vk, vv in v.items():
                                vrepr = json.dumps(vv) if not isinstance(vv, str) else vv
                                if len(vrepr) > 60:
                                    vrepr = vrepr[:57] + "…"
                                summary_parts.append(f"{key}.{vk}={vrepr}")
                                break
                        else:
                            summary_parts.append(f"{key}={v}")
                    if summary_parts:
                        print(f"[{now_iso()}] " + " | ".join(summary_parts))

            reader_task = asyncio.create_task(reader())
            stop_task = asyncio.create_task(stop_event.wait())
            timeout_task: asyncio.Task | None = None
            if max_seconds:
                timeout_task = asyncio.create_task(asyncio.sleep(max_seconds))

            wait_for = [reader_task, stop_task]
            if timeout_task:
                wait_for.append(timeout_task)
            _done, pending = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

    except InvalidStatus as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code == 401:
            print(f"[{now_iso()}] ✗ Tessie returned 401 — token is invalid or expired.")
            print(f"[{now_iso()}]   Regenerate at https://my.tessie.com/settings/api")
            return 2
        print(f"[{now_iso()}] ✗ Tessie handshake failed: {e}")
        return 3
    except ConnectionClosed as e:
        print(f"[{now_iso()}] connection closed (code={e.code} reason={e.reason!r})")
    except OSError as e:
        print(f"[{now_iso()}] ✗ network error: {e}")
        return 4

    elapsed = time.monotonic() - started_at
    print()
    print(f"=== summary after {elapsed:.1f}s ===")
    print(f"  messages received: {total_messages}")
    if field_counter:
        print("  fields seen:")
        for k, n in field_counter.most_common():
            mark = ""
            if k in ("Location", "Gear", "VehicleSpeed"):
                mark = "  ← needed by bridge"
            print(f"    {k:30s} {n:5d}{mark}")
        missing = {"Location", "Gear", "VehicleSpeed"} - set(field_counter)
        if missing:
            print()
            print(f"  ⚠ MISSING expected fields: {sorted(missing)}")
            print("     Enable them in Tessie console → vehicle → Settings → Fleet Telemetry")
    elif total_messages == 0:
        print("  no messages received. likely causes:")
        print("    - car is asleep (wake it from the Tesla app and re-run)")
        print("    - Fleet Telemetry not enabled in Tessie console for this VIN")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Probe Tessie Fleet Telemetry WebSocket.")
    p.add_argument("--seconds", type=int, default=None,
                   help="Stop after N seconds and print a summary. Default: run until Ctrl-C.")
    p.add_argument("--vin", default=os.environ.get("TESSIE_VIN", ""),
                   help="Vehicle VIN. Defaults to $TESSIE_VIN.")
    p.add_argument(
        "--token", default=os.environ.get("TESSIE_TOKEN", ""),
        help=(
            "Tessie access token. Defaults to $TESSIE_TOKEN. "
            "(Don't paste on the command line on shared machines.)"
        ),
    )
    args = p.parse_args()

    if not args.token:
        print(
            "error: TESSIE_TOKEN not set. Run `set -a; source .env; set +a` first.",
            file=sys.stderr,
        )
        sys.exit(2)
    if len(args.vin) != 17:
        print(f"error: TESSIE_VIN must be 17 chars, got {args.vin!r}", file=sys.stderr)
        sys.exit(2)

    sys.exit(asyncio.run(probe(args.vin, args.token, args.seconds)))


if __name__ == "__main__":
    main()
