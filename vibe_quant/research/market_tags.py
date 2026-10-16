"""Market tags for research items: the asset classes and tickers a post is about.

Every item gets tags, whether or not extraction finds tradable rules:

1. A deterministic pre-pass (cashtags, symbols from Alpaca's US equity and crypto
   lists, crypto names, keyword cues for options / futures / forex / macro /
   indexes) runs first and is stored at once (status ``prepass``).
2. The research LLM (``openai_chat.research_completion``: VQ_EXTRACTOR=openai ->
   VQ_OPENAI_BASE_URL / VQ_OPENAI_MODEL) classifies the item with the pre-pass as
   hints. Its tickers are kept only when real (Alpaca's lists, index symbols) or
   found by the pre-pass; invented ones are dropped. Status ``tagged``.
3. If the model is unreachable the pre-pass tags stand and the extraction worker
   retries the model later (``tag_backlog``); after ``MAX_MODEL_ATTEMPTS`` the item
   stays at its pre-pass tags (status ``failed``).

The symbol lists come from Alpaca through NautilusTrader's Alpaca adapter and are
cached in ``data/research/market_universe.json`` for a day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibe_quant.research import openai_chat

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibe_quant.db.state_manager import StateManager
    from vibe_quant.research.schema import RawItem

logger = logging.getLogger(__name__)

MARKETS: tuple[str, ...] = (
    "us_stocks",
    "options",
    "crypto",
    "futures",
    "forex",
    "etf_index",
    "macro",
)
DIRECTIONS: tuple[str, ...] = ("bullish", "bearish", "neutral", "mixed")

MAX_MODEL_ATTEMPTS = 3
MODEL_TIMEOUT_SECONDS = 45
# After this many model failures in a row, stop calling the model for a while so a
# dead endpoint can't stall a scrape (the worker retries those items later).
BREAKER_FAILURES = 2
BREAKER_COOLDOWN_SECONDS = 300

UNIVERSE_PATH = Path("data/research/market_universe.json")
UNIVERSE_MAX_AGE = timedelta(days=1)

# Futures roots; some (ES, CL, GC, SI, NG...) are also stock tickers, so they count as
# futures whenever the post is about futures.
FUTURES_ROOTS: frozenset[str] = frozenset(
    {
        "ES",
        "NQ",
        "YM",
        "RTY",
        "CL",
        "GC",
        "SI",
        "ZN",
        "ZB",
        "NG",
        "HG",
        "MES",
        "MNQ",
        "MYM",
        "M2K",
        "MCL",
        "MGC",
    }
)

# Index symbols people trade (mostly through options) that aren't Alpaca equities.
INDEX_SYMBOLS: frozenset[str] = frozenset({"SPX", "NDX", "RUT", "VIX", "DJI", "DJIA", "XSP"})

# Upper-case words that are also real tickers but almost never mean the stock in a
# post title (WSB slang, acronyms, common words). A cashtag still counts.
STOPWORDS: frozenset[str] = frozenset(
    {
        "A",
        "AI",
        "AM",
        "AN",
        "AND",
        "ANY",
        "ARE",
        "AS",
        "AT",
        "ATH",
        "ATM",
        "BE",
        "BIG",
        "BRO",
        "BUY",
        "BY",
        "CAN",
        "CEO",
        "CFO",
        "CPI",
        "CTO",
        "DD",
        "DO",
        "EOD",
        "EPS",
        "ETF",
        "EU",
        "EV",
        "FBI",
        "FDA",
        "FED",
        "FOMC",
        "FOR",
        "FTC",
        "FUD",
        "FY",
        "GDP",
        "GO",
        "GOT",
        "HAS",
        "HE",
        "HER",
        "HIM",
        "HIS",
        "HOLD",
        "HOW",
        "I",
        "IF",
        "II",
        "IN",
        "IPO",
        "IQ",
        "IS",
        "IT",
        "ITM",
        "IV",
        "JAN",
        "JUL",
        "JUN",
        "KEY",
        "LOL",
        "LONG",
        "LOVE",
        "MAR",
        "ME",
        "MOM",
        "MOON",
        "MY",
        "NEW",
        "NEXT",
        "NFT",
        "NO",
        "NOT",
        "NOW",
        "NYSE",
        "OF",
        "OK",
        "OKAY",
        "ON",
        "ONE",
        "OR",
        "OTM",
        "OUR",
        "OUT",
        "PE",
        "PM",
        "PPI",
        "PR",
        "PUT",
        "Q",
        "QE",
        "RE",
        "RH",
        "ROI",
        "RSI",
        "SEC",
        "SO",
        "SAY",
        "SEE",
        "SELL",
        "SHORT",
        "TA",
        "THE",
        "THEM",
        "TO",
        "TOO",
        "TV",
        "TWO",
        "UK",
        "UP",
        "US",
        "USA",
        "USD",
        "WE",
        "WAS",
        "WHO",
        "WHY",
        "WSB",
        "YOLO",
        "YOU",
        "YTD",
        "ALL",
        "BAD",
        "BEST",
        "CALL",
        "CASH",
        "DAY",
        "DOW",
        "EARN",
        "EDIT",
        "ELON",
        "EVER",
        "FREE",
        "GAIN",
        "GOOD",
        "HIGH",
        "HUGE",
        "JUST",
        "LAST",
        "LIVE",
        "LOSS",
        "MAN",
        "MORE",
        "MOST",
        "NEAR",
        "NICE",
        "OPEN",
        "PLAN",
        "PUMP",
        "REAL",
        "RUN",
        "SAFE",
        "SEND",
        "TIME",
        "TRUE",
        "WAY",
        "WEEK",
        "WELL",
        "WILL",
        "YEAR",
        "ZERO",
        "TLDR",
        "IMO",
        "IMHO",
        "FOMO",
        "DCA",
        "LMAO",
        "WTF",
        "OMG",
        "LFG",
        "HODL",
        "APE",
        "APES",
    }
)

_CASHTAG = re.compile(r"(?<![\w$])\$([A-Za-z]{1,6}(?:\.[A-Za-z])?)(?![\w])")
_UPPER_TOKEN = re.compile(r"(?<![\w$/.])([A-Z]{2,5})(?![\w/])")
_CUES: dict[str, re.Pattern[str]] = {
    "options": re.compile(
        r"\b\d+(?:\.\d+)?\s?[cp]\b|\b(?:calls|puts|call options?|put options?|leaps|0\s?dte|\d+\s?dte"
        r"|strikes?|expir\w*|iron condors?|credit spreads?|debit spreads?|covered calls?"
        r"|cash[- ]secured|csps?|the wheel|theta|implied vol\w*|options?)\b",
        re.IGNORECASE,
    ),
    "futures": re.compile(
        r"(?<![\w])/(?:ES|NQ|MES|MNQ|YM|RTY|CL|GC|SI|ZN|ZB|NG)\b|\bfutures?\b"
        r"|\b(?:ES|NQ|MES|MNQ)\s(?:futures|contracts?|long|short|chart)\b",
        re.IGNORECASE,
    ),
    "forex": re.compile(
        r"\b(?:EUR|GBP|USD|JPY|AUD|CAD|CHF|NZD|XAU|XAG)/?(?:USD|JPY|EUR|GBP|CHF|CAD|AUD|NZD)\b"
        r"|\bforex\b|\bfx\b|\bpips?\b",
        re.IGNORECASE,
    ),
    "crypto": re.compile(
        r"\b(?:crypto(?:currency|currencies)?|bitcoin|btc|ethereum|altcoins?|memecoins?|defi"
        r"|stablecoins?|blockchain|solana|dogecoin|xrp)\b",
        re.IGNORECASE,
    ),
    "macro": re.compile(
        r"\b(?:fed|fomc|cpi|ppi|inflation|interest rates?|rate (?:hike|cut)s?|yields?|treasur(?:y|ies)"
        r"|bonds?|recession|gdp|jobs report|nfp|payrolls|unemployment|tariffs?|powell|central bank"
        r"|macro)\b",
        re.IGNORECASE,
    ),
    "etf_index": re.compile(
        r"\b(?:s&p(?: ?500)?|nasdaq|dow jones|russell 2000|index funds?|etfs?|spx|ndx|vix)\b",
        re.IGNORECASE,
    ),
    "us_stocks": re.compile(r"\b(?:stocks?|shares|equit(?:y|ies)|earnings|ipo)\b", re.IGNORECASE),
}
_ETF_NAME = re.compile(
    r"\bETF\b|\bETN\b|Trust, Series|Index Fund|\bFund\b|\bETF Trust\b", re.IGNORECASE
)


# -- universe ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Universe:
    """Real symbols: Alpaca's US equities (stocks + ETFs) and crypto coins."""

    equities: dict[str, str] = field(default_factory=dict)  # symbol -> name
    etfs: frozenset[str] = frozenset()
    crypto: dict[str, str] = field(default_factory=dict)  # coin symbol -> lower-case name

    def is_equity(self, symbol: str) -> bool:
        return symbol in self.equities

    def is_real(self, symbol: str) -> bool:
        return symbol in self.equities or symbol in self.crypto or symbol in INDEX_SYMBOLS

    def market_in_context(self, symbol: str, markets: set[str]) -> str | None:
        """The symbol's market given the post's markets: a futures root in a futures post
        is the contract, a coin in a crypto post is the coin (not the same-named stock/ETF)."""
        if symbol in FUTURES_ROOTS and "futures" in markets:
            return "futures"
        if symbol in self.crypto and "crypto" in markets:
            return "crypto"
        return self.market_of(symbol)

    def market_of(self, symbol: str) -> str | None:
        if symbol in INDEX_SYMBOLS or symbol in self.etfs:
            return "etf_index"
        if symbol in self.crypto and symbol not in self.equities:
            return "crypto"
        if symbol in self.equities:
            return "us_stocks"
        return None


def _fetch_universe() -> dict[str, Any]:
    """Alpaca's active US equities and crypto pairs, through the NautilusTrader adapter."""
    from nautilus_trader.adapters.alpaca.factories import get_cached_alpaca_http_client
    from nautilus_trader.adapters.alpaca.http.market import AlpacaMarketHttpAPI
    from nautilus_trader.common.component import LiveClock

    async def fetch() -> tuple[list[Any], list[Any]]:
        api = AlpacaMarketHttpAPI(get_cached_alpaca_http_client(clock=LiveClock()))
        return await api.query_assets(), await api.query_assets(asset_class="crypto")

    equities, coins = asyncio.run(fetch())
    crypto: dict[str, str] = {}
    for a in coins:
        base = a.symbol.split("/")[0].upper()
        name = (a.name or base).split("/")[0].strip().lower()
        crypto.setdefault(base, name)
    return {
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "equities": {a.symbol.upper(): a.name or "" for a in equities if a.tradable},
        "etfs": sorted(a.symbol.upper() for a in equities if a.name and _ETF_NAME.search(a.name)),
        "crypto": crypto,
    }


_universe_lock = threading.Lock()
_universe: Universe | None = None
_universe_loaded_at = 0.0


def load_universe(path: Path = UNIVERSE_PATH) -> Universe:
    """The cached symbol lists, refreshed from Alpaca once a day (stale cache if Alpaca fails)."""
    global _universe, _universe_loaded_at
    with _universe_lock:
        if _universe is not None and time.monotonic() - _universe_loaded_at < 3600:
            return _universe
        raw: dict[str, Any] | None = None
        if path.exists():
            raw = json.loads(path.read_text())
        fresh = (
            raw is not None
            and datetime.now(tz=UTC) - datetime.fromisoformat(str(raw.get("fetched_at")))
            < UNIVERSE_MAX_AGE
        )
        if not fresh:
            try:
                raw = _fetch_universe()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(raw))
            except Exception as e:  # noqa: BLE001 — keys missing, Alpaca down: use what we have
                logger.warning(
                    "market universe refresh failed (%s); using %s",
                    e,
                    "cache" if raw else "no list",
                )
        raw = raw or {}
        _universe = Universe(
            equities=dict(raw.get("equities") or {}),
            etfs=frozenset(raw.get("etfs") or []),
            crypto=dict(raw.get("crypto") or {}),
        )
        _universe_loaded_at = time.monotonic()
        return _universe


# -- pre-pass ------------------------------------------------------------------------------------


@dataclass
class MarketTags:
    markets: list[str]
    tickers: list[str]
    primary_ticker: str | None = None
    direction: str | None = None


def _item_fields(item: RawItem | dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if isinstance(item, dict):
        return (
            str(item.get("title") or ""),
            str(item.get("body") or ""),
            dict(item.get("extras") or {}),
        )
    return item.title or "", item.body or "", dict(item.extras or {})


def prepass(item: RawItem | dict[str, Any], universe: Universe) -> MarketTags:
    """Deterministic tags from the text: no model involved."""
    title, body, extras = _item_fields(item)
    text = f"{title}\n{body[:6000]}"
    tickers: list[str] = []

    def add(symbol: str) -> None:
        symbol = symbol.upper().removesuffix(".X")
        if symbol not in tickers:
            tickers.append(symbol)

    # The source's own subject tickers first (news query ticker, SEC filer, earnings company).
    for hint in extras.get("tickers") or []:
        if universe.is_real(str(hint).upper()) or not universe.equities:
            add(str(hint))
    for tag in _CASHTAG.findall(text):
        if universe.is_real(tag.upper()) or not universe.equities:
            add(tag)
    for scope, min_len in ((title, 2), (body[:3000], 3)):
        for token in _UPPER_TOKEN.findall(scope):
            if token in STOPWORDS or len(token) < min_len:
                continue
            if universe.is_real(token):
                add(token)
    lowered = text.lower()
    for symbol, name in universe.crypto.items():
        if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", lowered):
            add(symbol)

    markets = {m for m, cue in _CUES.items() if cue.search(text)}
    if "futures" in markets:
        for token in _UPPER_TOKEN.findall(title):
            if token in FUTURES_ROOTS and token not in tickers:
                add(token)
    for symbol in tickers:
        market = universe.market_in_context(symbol, markets)
        if market:
            markets.add(market)
    if extras.get("subreddit") in {"options", "thetagang"}:
        markets.add("options")
    if extras.get("subreddit") in {"cryptocurrency", "bitcoin", "cryptomarkets"}:
        markets.add("crypto")
    ordered = [m for m in MARKETS if m in markets]
    return MarketTags(
        markets=ordered, tickers=tickers[:12], primary_ticker=tickers[0] if tickers else None
    )


# -- model ---------------------------------------------------------------------------------------

_PROMPT = """You tag one trading post or market news item with the markets it involves. Reply with ONLY a JSON object:
{{"markets": [...], "tickers": [...], "primary_ticker": "XYZ" or null, "direction": "bullish" | "bearish" | "neutral" | "mixed" | null}}

markets: every asset class the post trades or discusses concretely, chosen from
  us_stocks (individual US-listed stocks; a stock listed only abroad, e.g. in Tokyo, London or
  Milan, is not), options (any options trade or options talk), crypto,
  futures (ES/NQ/CL/... contracts), forex (currency pairs, spot gold XAUUSD),
  etf_index (SPY/QQQ/IWM and other ETFs, the S&P 500 / Nasdaq / VIX indexes), macro (Fed, rates,
  yields, inflation, jobs, tariffs, the economy).
  Examples: "NVDA 220c YOLO" -> ["us_stocks","options"]; "SPY 0DTE puts" -> ["etf_index","options"];
  "CPI tomorrow, bonds dumping" -> ["macro"]; "BTC to 150k" -> ["crypto"]. A general question
  about trading with no market -> [].
tickers: US stock/ETF symbols, index symbols (SPX, NDX, VIX), futures roots (ES, NQ, CL) and
  crypto coin symbols (BTC, ETH) the post is about, upper case, no "$". Include companies named
  in words when you are sure of the symbol (Chevron -> CVX, Nvidia -> NVDA, Micron -> MU).
  Leave out tickers merely mentioned in passing.
primary_ticker: the main ticker, else null.
direction: the author's stance on the primary ticker or market: bullish / bearish / neutral /
  mixed, only when the author states or clearly implies an expected move or holds a position
  (long/short, calls/puts). Methodology, backtesting, tooling, questions and education posts -> null.

Keyword scan hints (may be incomplete or wrong): markets={hint_markets} tickers={hint_tickers}
Source: {where}   Flair: {flair}{image_note}
Title: {title}
Post:
{body}
Top comments:
{comments}
"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _parse_reply(text: str) -> dict[str, Any]:
    match = _JSON_OBJECT.search(text)
    if not match:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


def classify(item: RawItem | dict[str, Any], hints: MarketTags, universe: Universe) -> MarketTags:
    """Ask the research LLM; validate its answer against the symbol lists and the pre-pass."""
    title, body, extras = _item_fields(item)
    comments = "\n".join(
        f"- {str(c.get('body') or '')[:300]}"
        for c in (extras.get("comments") or [])[:3]
        if isinstance(c, dict)
    )
    images = [str(p) for p in (extras.get("image_paths") or [])[:1] if Path(str(p)).is_file()]
    prompt = _PROMPT.format(
        image_note=(
            "\nThe post's image is attached (often a screenshot of the position or chart): use it."
            if images
            else ""
        ),
        hint_markets=hints.markets,
        hint_tickers=hints.tickers,
        where=(
            f"r/{extras['subreddit']}"
            if extras.get("subreddit")
            else f"news ({extras['publisher']})"
            if extras.get("publisher")
            else "-"
        ),
        flair=extras.get("flair") or "-",
        title=title[:300],
        body=body[:2500] or "(no text)",
        comments=comments or "(none)",
    )
    # Prompt-only JSON (no response_format: not every endpoint supports it), validated
    # below; one immediate retry when the reply isn't a JSON object.
    for attempt in range(2):
        reply = openai_chat.research_completion(
            prompt, timeout_seconds=MODEL_TIMEOUT_SECONDS, temperature=0, image_paths=images
        )
        try:
            data = _parse_reply(reply)
            break
        except ValueError:
            if attempt == 1:
                raise
            prompt += "\n\nYour last reply was not a JSON object. Reply with ONLY the JSON object."

    markets = {
        str(m).strip().lower()
        for m in data.get("markets") or []
        if str(m).strip().lower() in MARKETS
    }
    known = set(hints.tickers)
    tickers: list[str] = []
    for raw in data.get("tickers") or []:
        symbol = str(raw).strip().upper().lstrip("$").split("/")[0].removesuffix("-USD")
        futures_root = symbol in FUTURES_ROOTS and "futures" in markets
        if (
            symbol
            and (universe.is_real(symbol) or symbol in known or futures_root)
            and symbol not in tickers
        ):
            tickers.append(symbol)
    # A filing's or earnings date's company is certain: it stays even if the model left it out.
    if extras.get("kind") in ("sec_filing", "earnings"):
        for symbol in extras.get("tickers") or []:
            symbol = str(symbol).upper()
            if symbol in known and symbol not in tickers:
                tickers.insert(0, symbol)
    # Explicit cashtags stay even if the model left them out.
    title_body = f"{title}\n{body[:6000]}"
    for tag in _CASHTAG.findall(title_body):
        symbol = tag.upper()
        if symbol in known and symbol not in tickers:
            tickers.append(symbol)
    model_markets = set(markets)
    for symbol in tickers:
        market = universe.market_in_context(symbol, model_markets)
        if market:
            markets.add(market)
    primary = str(data.get("primary_ticker") or "").strip().upper().lstrip("$") or None
    if primary not in tickers:
        primary = tickers[0] if tickers else None
    direction = str(data.get("direction") or "").strip().lower() or None
    if direction not in DIRECTIONS:
        direction = None
    return MarketTags(
        markets=[m for m in MARKETS if m in markets],
        tickers=tickers[:12],
        primary_ticker=primary,
        direction=direction,
    )


# -- tagging -------------------------------------------------------------------------------------


class MarketTagger:
    """Tags items and stores them; one per process (keeps the circuit breaker state)."""

    def __init__(self, sm: StateManager, universe: Universe | None = None) -> None:
        self.sm = sm
        self._universe = universe
        self._consecutive_failures = 0
        self._open_until = 0.0

    @property
    def universe(self) -> Universe:
        if self._universe is None:
            self._universe = load_universe()
        return self._universe

    def model_available(self) -> bool:
        return time.monotonic() >= self._open_until

    def tag(
        self, item_id: int, item: RawItem | dict[str, Any], *, use_model: bool = True
    ) -> dict[str, Any]:
        """Pre-pass, then the model (unless disabled or the breaker is open); always stores tags."""
        existing = self.sm.get_research_item_tags(item_id)
        attempts = int(existing["attempts"]) if existing else 0
        hints = prepass(item, self.universe)
        if not use_model or not self.model_available():
            if existing and existing["status"] in ("tagged", "failed"):
                return existing  # never downgrade model tags to a pre-pass
            self._store(item_id, hints, status="prepass", model=None, attempts=attempts)
            return self.sm.get_research_item_tags(item_id) or {}
        label = openai_chat.research_model_label()
        try:
            tags = classify(item, hints, self.universe)
        except Exception as e:  # noqa: BLE001 — model down / bad reply: keep the pre-pass, retry later
            attempts += 1
            self._consecutive_failures += 1
            if self._consecutive_failures >= BREAKER_FAILURES:
                self._open_until = time.monotonic() + BREAKER_COOLDOWN_SECONDS
                logger.warning(
                    "market tags: model failing (%s); pausing model calls for %ds",
                    e,
                    BREAKER_COOLDOWN_SECONDS,
                )
            status = "failed" if attempts >= MAX_MODEL_ATTEMPTS else "prepass"
            self._store(
                item_id,
                hints,
                status=status,
                model=label,
                attempts=attempts,
                error=f"{type(e).__name__}: {e}"[:500],
            )
        else:
            self._consecutive_failures = 0
            self._store(item_id, tags, status="tagged", model=label, attempts=attempts + 1)
        return self.sm.get_research_item_tags(item_id) or {}

    def _store(
        self,
        item_id: int,
        tags: MarketTags,
        *,
        status: str,
        model: str | None,
        attempts: int,
        error: str | None = None,
    ) -> None:
        self.sm.upsert_research_item_tags(
            item_id,
            status=status,
            markets=tags.markets,
            tickers=tags.tickers,
            primary_ticker=tags.primary_ticker,
            direction=tags.direction,
            model=model,
            attempts=attempts,
            error=error,
        )

    def tag_backlog(
        self,
        *,
        limit: int = 10,
        use_model: bool = True,
        should_stop: Callable[[], bool] | None = None,
    ) -> int:
        """Tag items that have no tags yet or (with the model) only pre-pass tags.

        Returns how many items were processed. ``use_model=False`` gives never-tagged
        items their pre-pass tags only; ``should_stop`` is checked between items.
        """
        rows = self.sm.list_items_needing_tags(
            limit=limit, max_attempts=MAX_MODEL_ATTEMPTS, include_prepass=use_model
        )
        done = 0
        for row in rows:
            if should_stop is not None and should_stop():
                break
            if not use_model:
                self.tag(int(row["id"]), row, use_model=False)
                done += 1
                continue
            if not self.model_available():
                # Model paused: still give never-tagged items their pre-pass tags.
                if self.sm.get_research_item_tags(int(row["id"])) is None:
                    self.tag(int(row["id"]), row, use_model=False)
                    done += 1
                continue
            self.tag(int(row["id"]), row)
            done += 1
        return done
