import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import {
  useGetSourcesApiResearchSourcesGet,
  useGetTagFacetsApiResearchTagsFacetsGet,
} from "@/api/generated/research/research";
import { ItemList } from "@/components/research/ItemList";
import { MARKET_ORDER, marketLabel } from "@/components/research/MarketTags";
import { ScrapeButton } from "@/components/research/ScrapeButton";
import { ScrapeRunBanner } from "@/components/research/ScrapeRunBanner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type ResearchSort,
  type ResearchStatusFilter,
  useResearchStore,
} from "@/stores/researchStore";

const VALID_SORTS: ResearchSort[] = [
  "newest_scraped",
  "newest_posted",
  "highest_score",
  "highest_confidence",
  "credibility",
  "screen_sharpe",
];

const STATUS_OPTIONS: { value: ResearchStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "extracted", label: "Extracted" },
  { value: "failed", label: "Failed" },
  { value: "promoted", label: "Promoted" },
  { value: "rejected", label: "Rejected" },
  { value: "skipped", label: "Skipped" },
];

const SORT_OPTIONS: { value: ResearchSort; label: string }[] = [
  { value: "newest_scraped", label: "Newest scraped" },
  { value: "newest_posted", label: "Newest posted" },
  { value: "highest_score", label: "Highest score" },
  { value: "highest_confidence", label: "Highest confidence" },
  { value: "credibility", label: "Highest credibility" },
  { value: "screen_sharpe", label: "Highest screen Sharpe" },
];

export function ResearchPage() {
  const source = useResearchStore((s) => s.source);
  const status = useResearchStore((s) => s.status);
  const sort = useResearchStore((s) => s.sort);
  const hideLowTrade = useResearchStore((s) => s.hideLowTrade);
  const q = useResearchStore((s) => s.q);
  const setSource = useResearchStore((s) => s.setSource);
  const setStatus = useResearchStore((s) => s.setStatus);
  const setSort = useResearchStore((s) => s.setSort);
  const setHideLowTrade = useResearchStore((s) => s.setHideLowTrade);
  const setQ = useResearchStore((s) => s.setQ);
  const market = useResearchStore((s) => s.market);
  const ticker = useResearchStore((s) => s.ticker);
  const setMarket = useResearchStore((s) => s.setMarket);
  const setTicker = useResearchStore((s) => s.setTicker);

  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as {
    source?: string;
    sort?: string;
    hide_low_trade?: boolean;
    q?: string;
    market?: string;
    ticker?: string;
  };

  // On mount, hydrate store from URL (URL wins for shareable links).
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current) return;
    hydratedRef.current = true;
    if (typeof search.source === "string" && search.source.length > 0) {
      setSource(search.source);
    }
    if (search.sort && (VALID_SORTS as string[]).includes(search.sort)) {
      setSort(search.sort as ResearchSort);
    }
    if (search.hide_low_trade === true) {
      setHideLowTrade(true);
    }
    if (typeof search.q === "string" && search.q.length > 0) {
      setQ(search.q);
    }
    // A link with a market or ticker filter sets both (no stale remembered filter on top).
    const urlMarket =
      typeof search.market === "string" && MARKET_ORDER.includes(search.market)
        ? search.market
        : "";
    const urlTicker = typeof search.ticker === "string" ? search.ticker : "";
    if (urlMarket || urlTicker) {
      setMarket(urlMarket);
      setTicker(urlTicker);
    }
  }, [
    search.source,
    search.sort,
    search.hide_low_trade,
    search.q,
    search.market,
    search.ticker,
    setSource,
    setSort,
    setHideLowTrade,
    setQ,
    setMarket,
    setTicker,
  ]);

  // After hydration, mirror store back to URL.
  useEffect(() => {
    if (!hydratedRef.current) return;
    navigate({
      to: "/research",
      search: (prev) => ({
        ...prev,
        source: source === "reddit" ? undefined : source,
        sort: sort === "newest_scraped" ? undefined : sort,
        hide_low_trade: hideLowTrade ? true : undefined,
        q: q ? q : undefined,
        market: market ? market : undefined,
        ticker: ticker ? ticker : undefined,
      }),
      replace: true,
    });
  }, [source, sort, hideLowTrade, q, market, ticker, navigate]);

  const { data: facetsData } = useGetTagFacetsApiResearchTagsFacetsGet({ source });
  const facets = facetsData && facetsData.status === 200 ? facetsData.data : null;
  const marketCounts = new Map((facets?.markets ?? []).map((f) => [f.value, f.count]));

  const { data: sourcesData } = useGetSourcesApiResearchSourcesGet();
  const sources = sourcesData && sourcesData.status === 200 ? sourcesData.data.sources : ["reddit"];

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex flex-wrap items-center gap-2 pb-2">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Source</span>
          <Select value={source} onValueChange={setSource}>
            <SelectTrigger size="sm" className="h-8 text-xs w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sources.map((s) => (
                <SelectItem key={s} value={s} className="text-xs">
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Status</span>
          <Select value={status} onValueChange={(v) => setStatus(v as ResearchStatusFilter)}>
            <SelectTrigger size="sm" className="h-8 text-xs w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value} className="text-xs">
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Market</span>
          <Select value={market || "all"} onValueChange={(v) => setMarket(v === "all" ? "" : v)}>
            <SelectTrigger size="sm" className="h-8 text-xs w-36" aria-label="Market filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all" className="text-xs">
                All markets
              </SelectItem>
              {MARKET_ORDER.map((m) => (
                <SelectItem key={m} value={m} className="text-xs">
                  {marketLabel(m)}
                  {marketCounts.has(m) ? ` (${marketCounts.get(m)})` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Ticker</span>
          <input
            type="search"
            list="research-ticker-options"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="e.g. NVDA"
            aria-label="Ticker filter"
            className="h-8 w-28 rounded-md border border-border/60 bg-background px-2 font-mono text-xs uppercase text-foreground placeholder:normal-case placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-primary/60"
          />
          <datalist id="research-ticker-options">
            {(facets?.tickers ?? []).map((f) => (
              <option key={f.value} value={f.value}>
                {`${f.count} item${f.count === 1 ? "" : "s"}`}
              </option>
            ))}
          </datalist>
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Sort</span>
          <Select value={sort} onValueChange={(v) => setSort(v as ResearchSort)}>
            <SelectTrigger size="sm" className="h-8 text-xs w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value} className="text-xs">
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <label className="flex items-center gap-1.5 text-xs text-muted-foreground select-none cursor-pointer">
          <input
            type="checkbox"
            checked={hideLowTrade}
            onChange={(e) => setHideLowTrade(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-border/60 accent-primary cursor-pointer"
          />
          Hide &lt;50 trades
        </label>

        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Search</span>
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="title contains…"
            className="h-8 w-56 rounded-md border border-border/60 bg-background px-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-primary/60"
          />
        </div>

        <div className="ml-auto">
          <ScrapeButton source={source} />
        </div>
      </div>

      <div className="rounded-md border border-border/40 bg-card/40 px-3 py-2">
        <ScrapeRunBanner source={source} />
      </div>

      <div className="flex-1 overflow-auto">
        <ItemList />
      </div>
    </div>
  );
}
