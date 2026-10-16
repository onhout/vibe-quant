"""Market and company news: Google News headlines, SEC 8-K filings, upcoming earnings.

Each run (every cycle of the research loop) reads

- Google News RSS search for general market queries (``VQ_NEWS_QUERIES``,
  ``|``-separated, or the default list: the market, the Fed, indexes, yields,
  oil, crypto) and for every ticker that matters right now,
- the SEC EDGAR submissions of those tickers for new 8-K filings (material
  events: results, deals, officer changes, offerings),
- Nasdaq's earnings calendar for the coming week (those tickers and mega caps).

The tickers come from the caller (the pipeline passes the tickers tagged on
recent research items, WallStreetBets' hot names and the core watch list), or
``VQ_NEWS_TICKERS``. A headline's id is its normalized title, so one story
syndicated under several publishers or found by several queries is a single
item across runs; the archive also refuses a link it already holds. Items are
market-tagged like posts; only headlines that read like trading rules go to
the extraction model (:meth:`NewsSource.wants_extraction`).
"""

from __future__ import annotations

import email.utils
import hashlib
import html
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from itertools import zip_longest
from typing import TYPE_CHECKING, Any, ClassVar, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from vibe_quant.research.schema import RawItem
from vibe_quant.research.sources import register_source

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from vibe_quant.db.state_manager import StateManager

logger = logging.getLogger(__name__)


class _HttpGetter(Protocol):
    """The subset of ``httpx.Client`` this source relies on."""

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response: ...

    def close(self) -> None: ...


GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
SEC_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_EARNINGS_PAGE = "https://www.nasdaq.com/market-activity/earnings"
# SEC asks automated clients to identify themselves; www.sec.gov answers 403 to browser UAs.
SEC_USER_AGENT = "PersonalTradingResearch/1.0 research@localhost.localdomain"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
HTTP_TIMEOUT_S = 20.0
# Request floors per host: Google News is polite at ~1 req/s, SEC allows 10 req/s.
MIN_INTERVAL_S = {
    "news.google.com": 1.0,
    "efts.sec.gov": 0.2,
    "data.sec.gov": 0.2,
    "api.nasdaq.com": 0.5,
}

ENV_TICKERS = "VQ_NEWS_TICKERS"
ENV_QUERIES = "VQ_NEWS_QUERIES"
# Worded for US markets: a bare "stock market today" pulls in Seoul, Mumbai and London.
DEFAULT_QUERIES = (
    "Wall Street stocks today",
    "Federal Reserve interest rates",
    "S&P 500 Nasdaq Dow Jones",
    "US stocks biggest movers",
    "Treasury yields inflation",
    "oil prices",
    "bitcoin price",
)
CORE_TICKERS = ("NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD")
MAX_TICKERS = 15
HEADLINES_PER_QUERY = 8
HEADLINES_PER_TICKER = 4
EARNINGS_DAYS = 7
# Upcoming earnings of companies this big are listed even when no one is talking about them.
MEGA_CAP_USD = 200e9
# 8-K filings of the last few days on a first run (afterwards: since the last run).
FILING_LOOKBACK = timedelta(days=3)

ITEM_8K = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.05": "Material Cybersecurity Incident",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure or Appointment of Directors or Officers",
    "5.03": "Amendments to Articles or Bylaws; Change in Fiscal Year",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}
EARNINGS_TIME = {"time-pre-market": "before the open", "time-after-hours": "after the close"}

# Headlines are news, not strategies: only text that reads like rules is worth an extraction call.
RULE_CUES = re.compile(
    r"\b(?:strateg(?:y|ies)|backtest(?:ed|ing)?|trading rules?|moving average|rsi|macd|"
    r"bollinger|crossover|breakout|stop[- ]loss|entry (?:signal|rule)s?|mean reversion)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
_TAG = re.compile(r"<[^>]+>")
_ANCHOR = re.compile(r"<a [^>]*>(.*?)</a>(?:\s|&nbsp;)*(?:<font[^>]*>(.*?)</font>)?", re.DOTALL)


def queries_from_env() -> list[str]:
    raw = os.getenv(ENV_QUERIES, "").strip()
    if not raw:
        return list(DEFAULT_QUERIES)
    return [q.strip() for q in raw.split("|") if q.strip()]


def tickers_from_env() -> list[str] | None:
    raw = os.getenv(ENV_TICKERS, "").strip()
    if not raw:
        return None
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def watch_tickers(sm: StateManager, limit: int = MAX_TICKERS) -> list[str]:
    """The tickers that matter now: tagged on research items of the last 3 days, then
    WallStreetBets' hot names of the last week, then the core list (stocks only)."""
    from vibe_quant.research.market_tags import STOPWORDS, load_universe
    from vibe_quant.research.sources.wsb_archive import mention_counts

    configured = tickers_from_env()
    if configured:
        return configured[:limit]
    universe = load_universe()

    def is_stock(symbol: str) -> bool:
        return (
            universe.is_equity(symbol) and symbol not in universe.etfs and symbol not in STOPWORDS
        )

    tagged = [
        t
        for t, _ in sm.recent_tagged_tickers(days=3, markets=["us_stocks", "options"])
        if is_stock(t)
    ]
    try:
        counts = mention_counts(days=7)
    except Exception as e:  # noqa: BLE001 — archive missing or locked: go without it
        logger.warning("news: WallStreetBets mention counts unavailable (%s)", e)
        counts = {}
    hot = [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if is_stock(t)]
    return list(dict.fromkeys([*tagged[:7], *hot[:5], *CORE_TICKERS]))[:limit]


def normalized_title(title: str) -> str:
    return _NON_WORD.sub(" ", html.unescape(title).lower()).strip()


def canonical_url(url: str) -> str:
    """The link without query string or fragment (tracking parameters), lower-case host."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def _strip_publisher(title: str, publisher: str) -> str:
    """Google News titles end in ' - Publisher'."""
    if publisher and title.endswith(f" - {publisher}"):
        return title[: -len(publisher) - 3].strip()
    return title.strip()


def _published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _related(description: str, title: str) -> str:
    """Other coverage of the same story, from the description's link list."""
    lines = []
    for headline, publisher in _ANCHOR.findall(description or ""):
        text = html.unescape(_TAG.sub("", headline)).strip()
        source = html.unescape(_TAG.sub("", publisher or "")).strip()
        if text and normalized_title(text) != normalized_title(title):
            lines.append(f"- {text}" + (f" ({source})" if source else ""))
    return "Related coverage:\n" + "\n".join(lines) if lines else ""


def _window(since: datetime | None) -> str:
    """Google News ``when:`` window covering everything since the last run (1 to 7 days)."""
    if since is None:
        return "1d"
    days = (datetime.now(tz=UTC) - since).total_seconds() / 86400
    return f"{min(7, max(1, int(days) + 1))}d"


@register_source("news")
class NewsSource:
    """Market and company headlines, 8-K filings and upcoming earnings."""

    name: ClassVar[str] = "news"

    def __init__(
        self,
        tickers: list[str] | None = None,
        queries: list[str] | None = None,
        client: _HttpGetter | None = None,
        earnings_days: int = EARNINGS_DAYS,
    ) -> None:
        tickers = tickers if tickers is not None else (tickers_from_env() or list(CORE_TICKERS))
        self._tickers = list(dict.fromkeys(t.upper() for t in tickers))[:MAX_TICKERS]
        self._queries = queries if queries is not None else queries_from_env()
        self._earnings_days = earnings_days
        self._client: _HttpGetter = client or httpx.Client(
            timeout=HTTP_TIMEOUT_S, follow_redirects=True
        )
        self._last_request: dict[str, float] = {}
        self._ciks: dict[str, int | None] = {}

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def wants_extraction(item: RawItem) -> bool:
        """Only headlines that read like trading rules go to the extraction model."""
        if (item.extras or {}).get("kind") != "headline":
            return False
        return RULE_CUES.search(f"{item.title}\n{item.body}") is not None

    # -- http ----------------------------------------------------------------------------------

    def _get(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        user_agent: str = BROWSER_USER_AGENT,
    ) -> httpx.Response:
        host = urlsplit(url).netloc
        wait = MIN_INTERVAL_S.get(host, 0.5) - (
            time.monotonic() - self._last_request.get(host, 0.0)
        )
        if wait > 0:
            time.sleep(wait)
        try:
            response = self._client.get(
                url,
                params=params,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/json, application/xml, */*",
                },
            )
        finally:
            self._last_request[host] = time.monotonic()
        response.raise_for_status()
        return response

    # -- fetch ---------------------------------------------------------------------------------

    def fetch(self, since: datetime | None, limit: int) -> Iterable[RawItem]:
        """Upcoming earnings, then 8-K filings, then headlines (queries interleaved, newest first)."""
        window = _window(since)
        sections: list[tuple[str, list[RawItem]]] = []
        errors: list[str] = []

        def collect(label: str, produce: Any) -> list[RawItem]:
            try:
                return list(produce())
            except (httpx.HTTPError, ET.ParseError, ValueError, KeyError) as e:
                logger.warning("news: %s failed: %s", label, e)
                errors.append(f"{label}: {e}")
                return []

        sections.append(("earnings", collect("earnings calendar", self._earnings)))
        filings: list[RawItem] = []
        filing_since = since or datetime.now(tz=UTC) - FILING_LOOKBACK
        for ticker in self._tickers:
            filings += collect(f"8-K {ticker}", lambda t=ticker: self._filings(t, filing_since))
        sections.append(("filings", filings))
        per_query = [
            collect(f"headlines {q!r}", lambda q=q: self._headlines(q, window, HEADLINES_PER_QUERY))
            for q in self._queries
        ]
        per_query += [
            collect(
                f"headlines {t}",
                lambda t=t: self._headlines(f'"{t}" stock', window, HEADLINES_PER_TICKER, ticker=t),
            )
            for t in self._tickers
        ]
        # Round robin across queries so ``limit`` never starves the later ones.
        headlines = [i for row in zip_longest(*per_query) for i in row if i is not None]
        sections.append(("headlines", headlines))
        attempted = 1 + len(self._tickers) + len(per_query)
        if len(errors) == attempted:
            raise RuntimeError(f"every news request failed: {errors[-1]}")

        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        yielded = 0
        for label, items in sections:
            count = 0
            for item in items:
                if yielded >= limit:
                    break
                if item.external_id in seen_ids or item.url in seen_urls:
                    continue
                seen_ids.add(item.external_id)
                seen_urls.add(item.url)
                yielded += 1
                count += 1
                yield item
            logger.info("news: %d %s", count, label)

    # -- Google News ---------------------------------------------------------------------------

    def _headlines(
        self, query: str, window: str, keep: int, *, ticker: str | None = None
    ) -> list[RawItem]:
        response = self._get(
            GOOGLE_NEWS_URL,
            {"q": f"{query} when:{window}", "hl": "en-US", "gl": "US", "ceid": "US:en"},
        )
        rows: list[RawItem] = []
        for node in ET.fromstring(response.content).iter("item"):
            source_node = node.find("source")
            publisher = html.unescape(node.findtext("source") or "").strip()
            title = _strip_publisher(html.unescape(node.findtext("title") or ""), publisher)
            link = canonical_url(node.findtext("link") or "")
            key = normalized_title(title)
            if not key or not link:
                continue
            rows.append(
                RawItem(
                    source=self.name,
                    external_id="gn:" + hashlib.sha1(key.encode()).hexdigest()[:16],  # noqa: S324 (an id, not security)
                    url=link,
                    title=title,
                    body=_related(node.findtext("description") or "", title),
                    author=publisher or None,
                    posted_at=_published(node.findtext("pubDate")),
                    score=None,
                    extras={
                        "kind": "headline",
                        "publisher": publisher or None,
                        "publisher_url": source_node.get("url")
                        if source_node is not None
                        else None,
                        "query": query,
                        "tickers": [ticker] if ticker else [],
                    },
                )
            )
        rows.sort(key=lambda i: i.posted_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return rows[:keep]

    # -- SEC EDGAR -----------------------------------------------------------------------------

    def _cik(self, ticker: str) -> int | None:
        if ticker not in self._ciks:
            response = self._get(SEC_SEARCH_URL, {"keysTyped": ticker}, user_agent=SEC_USER_AGENT)
            cik = None
            for hit in (response.json().get("hits") or {}).get("hits") or []:
                tickers = str((hit.get("_source") or {}).get("tickers") or "")
                if ticker in [t.strip().upper() for t in tickers.split(",")]:
                    cik = int(hit["_id"])
                    break
            self._ciks[ticker] = cik
        return self._ciks[ticker]

    def _filings(self, ticker: str, since: datetime) -> list[RawItem]:
        cik = self._cik(ticker)
        if cik is None:
            return []
        data = self._get(SEC_SUBMISSIONS_URL.format(cik=cik), user_agent=SEC_USER_AGENT).json()
        company = str(data.get("name") or ticker)
        recent = (data.get("filings") or {}).get("recent") or {}
        rows = zip(
            recent.get("form", []),
            recent.get("accessionNumber", []),
            recent.get("acceptanceDateTime", []),
            recent.get("filingDate", []),
            recent.get("items", []),
            recent.get("primaryDocument", []),
            recent.get("primaryDocDescription", []),
            strict=False,
        )
        out: list[RawItem] = []
        for form, accession, accepted, filed, items, document, doc_description in rows:
            if form not in ("8-K", "8-K/A"):
                continue
            when = _sec_time(accepted, filed)
            if when is None or when < since:
                continue
            codes = [c.strip() for c in str(items or "").split(",") if c.strip()]
            described = [f"{ITEM_8K.get(c, 'Item')} ({c})" for c in codes]
            headline = "; ".join(ITEM_8K[c] for c in codes if c in ITEM_8K and c != "9.01")
            title = f"{ticker} {form}: {headline or 'current report'}"
            body = (
                f"{company} ({ticker}) filed a Form {form} with the SEC on {filed}.\n"
                + (f"Items: {', '.join(described)}.\n" if described else "")
                + (
                    f"Document: {doc_description}."
                    if doc_description and str(doc_description).upper() != form
                    else ""
                )
            )
            out.append(
                RawItem(
                    source=self.name,
                    external_id=f"sec:{accession}",
                    url=SEC_ARCHIVE_URL.format(
                        cik=cik, accession=str(accession).replace("-", ""), document=document
                    ),
                    title=title,
                    body=body.strip(),
                    author=company,
                    posted_at=when,
                    score=None,
                    extras={
                        "kind": "sec_filing",
                        "publisher": "SEC EDGAR",
                        "form": form,
                        "items": codes,
                        "tickers": [ticker],
                    },
                )
            )
        return out

    # -- Nasdaq earnings calendar --------------------------------------------------------------

    def _earnings(self) -> list[RawItem]:
        watch = set(self._tickers)
        now = datetime.now(tz=UTC)
        day = now.date()
        out: list[RawItem] = []
        for _ in range(self._earnings_days + 1):
            if day.weekday() < 5:
                payload = self._get(NASDAQ_EARNINGS_URL, {"date": day.isoformat()}).json()
                for row in ((payload.get("data") or {}).get("rows")) or []:
                    item = self._earnings_item(row, day, now, watch)
                    if item is not None:
                        out.append(item)
            day += timedelta(days=1)
        return out

    def _earnings_item(
        self, row: dict[str, Any], day: date, now: datetime, watch: set[str]
    ) -> RawItem | None:
        symbol = str(row.get("symbol") or "").strip().upper()
        cap = _money(row.get("marketCap"))
        if not symbol or (symbol not in watch and (cap or 0) < MEGA_CAP_USD):
            return None
        name = str(row.get("name") or symbol).strip()
        timing = EARNINGS_TIME.get(str(row.get("time") or ""))
        eps = str(row.get("epsForecast") or "").strip()
        estimates = str(row.get("noOfEsts") or "").strip()
        title = f"{symbol} reports earnings {day:%a %b} {day.day}" + (
            f" {timing}" if timing else ""
        )
        if eps:
            title += f" (EPS forecast {eps})"
        body = (
            f"{name} ({symbol}) is scheduled to report results for the quarter ending "
            f"{row.get('fiscalQuarterEnding') or '?'} on {day.isoformat()}"
            + (f", {timing}" if timing else "")
            + ".\n"
            + (f"Consensus EPS forecast {eps} from {estimates or '?'} estimates. " if eps else "")
            + (
                f"Last year: EPS {row.get('lastYearEPS')} reported {row.get('lastYearRptDt')}. "
                if row.get("lastYearEPS")
                else ""
            )
            + (f"Market cap {row.get('marketCap')}." if row.get("marketCap") else "")
        )
        return RawItem(
            source=self.name,
            external_id=f"earnings:{symbol}:{day.isoformat()}",
            url=f"{NASDAQ_EARNINGS_PAGE}?date={day.isoformat()}#{symbol}",
            title=title,
            body=body.strip(),
            author="Nasdaq earnings calendar",
            posted_at=now,
            score=None,
            extras={
                "kind": "earnings",
                "publisher": "Nasdaq",
                "earnings_date": day.isoformat(),
                "timing": timing,
                "eps_forecast": eps or None,
                "market_cap": cap,
                "tickers": [symbol],
            },
        )


def _sec_time(accepted: str | None, filed: str | None) -> datetime | None:
    for raw in (accepted, filed):
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(tzinfo=UTC)
    return None


def _money(raw: object) -> float | None:
    digits = re.sub(r"[^0-9.]", "", str(raw or ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None
