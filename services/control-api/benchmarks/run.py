from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

CONTROL_API = Path(__file__).resolve().parents[1]
if str(CONTROL_API) not in sys.path:
    sys.path.insert(0, str(CONTROL_API))

from benchmarks.comparison import compare_scenarios, render_comparison  # noqa: E402
from benchmarks.scenarios import make_scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare FailureLab investigators on shared evidence."
    )
    parser.add_argument(
        "--gemini",
        choices=("mock", "real"),
        default="mock",
        help="Use a clearly labeled mock (default) or explicitly call the real Gemini API.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if args.gemini == "real" and not api_key.strip():
        parser.error("--gemini real requires GEMINI_API_KEY in the environment")

    entries = asyncio.run(
        compare_scenarios(
            make_scenarios(),
            gemini_mode=args.gemini,
            api_key=api_key,
            model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
            fallback_to_local=os.environ.get("GEMINI_FALLBACK_TO_LOCAL", "true").strip().lower()
            in {"1", "true", "yes", "on"},
        )
    )
    print(render_comparison(entries))


if __name__ == "__main__":
    main()
