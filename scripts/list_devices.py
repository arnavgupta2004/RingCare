"""List Ring devices visible to RING_ACCESS_TOKEN via GET /v1/devices.

Usage:
    python scripts/list_devices.py            # plain device list
    python scripts/list_devices.py --include  # also include status + capabilities
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import ConfigError, get_settings  # noqa: E402
from backend.ring.accounts import TokenUnavailable, get_accounts  # noqa: E402
from backend.ring.client import RingAPIError, RingTokenExpiredError  # noqa: E402


async def main(include: bool) -> int:
    try:
        get_settings()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    # Linked-account token if one is linked (refreshed as needed), else RING_ACCESS_TOKEN (sandbox).
    accounts = get_accounts()
    try:
        result = await accounts.call(lambda ring: ring.list_devices(include=["status", "capabilities"] if include else None))
    except (RingTokenExpiredError, TokenUnavailable) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except RingAPIError as exc:
        print(f"\n{exc}\n{json.dumps(exc.body, indent=2) if exc.body else ''}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    devices = (result or {}).get("data", [])
    print(f"\n{len(devices)} device(s):")
    for d in devices:
        print(f"  - {d.get('attributes', {}).get('name', '?')}  id={d.get('id')}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--include", action="store_true", help="include status and capabilities")
    sys.exit(asyncio.run(main(parser.parse_args().include)))
