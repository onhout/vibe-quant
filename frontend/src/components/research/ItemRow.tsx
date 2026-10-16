import { Link } from "@tanstack/react-router";
import { memo } from "react";
import type { ResearchItemResponse } from "@/api/generated/models";
import { ConfidenceBar } from "@/components/research/ConfidenceBar";
import { MarketTags } from "@/components/research/MarketTags";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { TableCell, TableRow } from "@/components/ui/table";
import { formatRelative } from "@/lib/time";
import { useResearchStore } from "@/stores/researchStore";

function getConfidence(extras: { [k: string]: unknown } | null | undefined): number | null {
  if (!extras) return null;
  const c = extras.latest_confidence;
  return typeof c === "number" ? c : null;
}

function getNumComments(extras: { [k: string]: unknown } | null | undefined): number | null {
  if (!extras) return null;
  const n = extras.num_comments;
  return typeof n === "number" ? n : null;
}

function getPublisher(extras: { [k: string]: unknown } | null | undefined): string | null {
  const p = extras?.publisher;
  return typeof p === "string" && p ? p : null;
}

interface Props {
  item: ResearchItemResponse;
}

function ItemRowImpl({ item }: Props) {
  const confidence = getConfidence(item.extras);
  const numComments = getNumComments(item.extras);
  // News rows name their publisher (list rows carry no extras; the author is the publisher).
  const publisher = getPublisher(item.extras) ?? (item.source === "news" ? item.author : null);
  const setMarket = useResearchStore((s) => s.setMarket);
  const setTicker = useResearchStore((s) => s.setTicker);
  const showConfidence =
    item.extraction_status === "extracted" || item.extraction_status === "parsed";

  return (
    <TableRow className="cursor-pointer hover:bg-white/[0.02]">
      <TableCell className="text-xs text-muted-foreground tabular-nums">
        {formatRelative(item.posted_at ?? item.fetched_at)}
      </TableCell>
      <TableCell className="max-w-md">
        <Link
          to="/research/$itemId"
          params={{ itemId: String(item.id) }}
          className="block truncate text-sm text-foreground hover:text-primary hover:underline"
          title={item.title ?? undefined}
        >
          {item.title || "(untitled)"}
        </Link>
      </TableCell>
      <TableCell className="w-72 py-1.5">
        <MarketTags
          tags={item.market_tags}
          maxTickers={4}
          onMarketClick={setMarket}
          onTickerClick={setTicker}
        />
      </TableCell>
      <TableCell>
        <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground">
          {item.source}
        </Badge>
        {publisher && (
          <div
            className="mt-0.5 max-w-[8rem] truncate text-[10px] text-muted-foreground"
            title={publisher}
          >
            {publisher}
          </div>
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums text-muted-foreground">
        {item.score ?? "—"}
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums text-muted-foreground">
        {numComments ?? "—"}
      </TableCell>
      <TableCell>
        <StatusBadge status={item.extraction_status} />
      </TableCell>
      <TableCell>
        {showConfidence ? (
          <ConfidenceBar value={confidence} />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
    </TableRow>
  );
}

export const ItemRow = memo(ItemRowImpl);
