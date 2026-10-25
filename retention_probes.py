"""Run all synthetic product/retention probes without contacting live services.

Usage: python retention_probes.py --output retention_probe_results.json
The JSON describes code behavior and synthetic fixtures, never real retention.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from probes_bot import run as bot_probes
from probes_storage import run_probes as storage_probes
from probes_tracking import run_probes as tracking_probes


async def run() -> dict:
    # Sequential execution keeps synthetic memory/timing measurements isolated.
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Synthetic in-memory code probes. No live Discord, PostgreSQL, credentials, or real-user data.",
        "measures_real_user_retention": False,
        "tracking": tracking_probes(),
        "discord": await bot_probes(),
        "storage": await storage_probes(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run())
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
        print(f"All synthetic probe assertions passed. Evidence: {args.output}")
        print("Real-user retention was not measured; see retention_review.md for recommendations.")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
