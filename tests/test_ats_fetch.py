"""Prove the ATS fetcher offline on recorded response shapes: parsers, Workday paging, robots.txt, rate limits,
title and place filters. No network: every HTTP call in the self-test is replaced.

    python tests/test_ats_fetch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ats_fetch  # noqa: E402


def test_selftest():
    """pytest entry point; the file also runs on its own."""
    assert ats_fetch.selftest() == 0


if __name__ == "__main__":
    sys.exit(ats_fetch.selftest())
