"""
Robinhood trading system (Peter's ~/trading project) inside the vibe-quant dashboard.

The bot (Reddit options strategy on NautilusTrader, paper or live on the Agentic account), its proposal
ledger and the Research Lab live in the trading project; this package exposes them to the dashboard.
``TRADING_ROOT`` points at that project (default ~/trading) and is put on ``sys.path`` so its packages
(``research``, ``lab``, ``nautilus_robinhood``) import directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


TRADING_ROOT = Path(os.environ.get("TRADING_ROOT", Path.home() / "trading")).resolve()
STATE = TRADING_ROOT / "state"

if str(TRADING_ROOT) not in sys.path:
    sys.path.insert(0, str(TRADING_ROOT))
