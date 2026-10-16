import type { MarketTagsResponse } from "@/api/generated/models";
import { cn } from "@/lib/utils";

/** Display label + colour per market (see vibe_quant/research/market_tags.py MARKETS). */
export const MARKET_META: Record<string, { label: string; cls: string }> = {
  us_stocks: { label: "Stocks", cls: "border-sky-500/30 bg-sky-500/10 text-sky-300" },
  options: { label: "Options", cls: "border-violet-500/30 bg-violet-500/10 text-violet-300" },
  crypto: { label: "Crypto", cls: "border-amber-500/30 bg-amber-500/10 text-amber-300" },
  futures: { label: "Futures", cls: "border-orange-500/30 bg-orange-500/10 text-orange-300" },
  forex: { label: "Forex", cls: "border-teal-500/30 bg-teal-500/10 text-teal-300" },
  etf_index: { label: "ETF/Index", cls: "border-indigo-500/30 bg-indigo-500/10 text-indigo-300" },
  macro: { label: "Macro", cls: "border-rose-500/30 bg-rose-500/10 text-rose-300" },
};

export const MARKET_ORDER = Object.keys(MARKET_META);

export function marketLabel(market: string): string {
  return MARKET_META[market]?.label ?? market;
}

const DIRECTION_META: Record<string, { symbol: string; cls: string; label: string }> = {
  bullish: { symbol: "▲", cls: "text-emerald-400", label: "Bullish" },
  bearish: { symbol: "▼", cls: "text-red-400", label: "Bearish" },
  neutral: { symbol: "◆", cls: "text-muted-foreground", label: "Neutral" },
  mixed: { symbol: "◆", cls: "text-amber-300", label: "Mixed" },
};

interface Props {
  tags: MarketTagsResponse | null | undefined;
  /** Max ticker chips before "+N" (all shown when omitted). */
  maxTickers?: number;
  onMarketClick?: (market: string) => void;
  onTickerClick?: (ticker: string) => void;
  className?: string;
}

/** Market + ticker chips for a research item, readable at a glance. */
export function MarketTags({ tags, maxTickers, onMarketClick, onTickerClick, className }: Props) {
  if (!tags) {
    return <span className="text-[10px] text-muted-foreground/60">untagged</span>;
  }
  const tickers = maxTickers === undefined ? tags.tickers : tags.tickers.slice(0, maxTickers);
  const hidden = tags.tickers.length - tickers.length;
  const direction = tags.direction ? DIRECTION_META[tags.direction] : undefined;
  const pending = tags.status === "prepass";

  if (tags.markets.length === 0 && tags.tickers.length === 0) {
    return (
      <span className={cn("text-[10px] text-muted-foreground/60", className)}>
        {pending ? "tagging…" : "no market"}
      </span>
    );
  }

  return (
    <div
      className={cn("flex flex-wrap items-center gap-1", className)}
      title={pending ? "Keyword scan only — model tagging pending" : undefined}
    >
      {tags.markets.map((m) => (
        <button
          key={m}
          type="button"
          disabled={!onMarketClick}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onMarketClick?.(m);
          }}
          className={cn(
            "rounded border px-1.5 py-px text-[10px] font-medium leading-4",
            MARKET_META[m]?.cls ?? "border-border text-muted-foreground",
            onMarketClick ? "cursor-pointer hover:brightness-125" : "cursor-default",
            pending && "opacity-70",
          )}
        >
          {marketLabel(m)}
        </button>
      ))}
      {tickers.map((t) => (
        <button
          key={t}
          type="button"
          disabled={!onTickerClick}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onTickerClick?.(t);
          }}
          className={cn(
            "rounded border border-border/70 bg-muted/40 px-1.5 py-px font-mono text-[10px] leading-4 text-foreground/90",
            t === tags.primary_ticker && "border-primary/50 font-semibold text-primary",
            onTickerClick ? "cursor-pointer hover:border-primary/60" : "cursor-default",
          )}
        >
          {t}
        </button>
      ))}
      {hidden > 0 && <span className="text-[10px] text-muted-foreground">+{hidden}</span>}
      {direction && (
        <span className={cn("text-[10px] font-medium", direction.cls)} title={direction.label}>
          {direction.symbol} {direction.label}
        </span>
      )}
    </div>
  );
}
