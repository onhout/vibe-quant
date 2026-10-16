import type {
  AlpacaFillResponse,
  AlpacaOrderResponse,
  AlpacaPositionResponse,
} from "@/api/generated/models";
import { pct, shortTime, usd } from "@/components/options-bot/format";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

function pnlClass(value: number | null | undefined): string {
  if (value == null || value === 0) return "";
  return value > 0 ? "text-profit" : "text-loss";
}

export function AlpacaPositionsTable({ positions }: { positions: AlpacaPositionResponse[] }) {
  if (positions.length === 0)
    return <p className="text-xs text-muted-foreground">No open positions.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead>Type</TableHead>
          <TableHead className="text-right">Qty</TableHead>
          <TableHead className="text-right">Avg entry</TableHead>
          <TableHead className="text-right">Price</TableHead>
          <TableHead className="text-right">Value</TableHead>
          <TableHead className="text-right">Unrealized P&amp;L</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {positions.map((p) => (
          <TableRow key={p.symbol}>
            <TableCell className="font-mono font-semibold">{p.symbol}</TableCell>
            <TableCell className="text-xs">
              {p.asset_class === "us_option" ? "option" : "stock"} · {p.side}
            </TableCell>
            <TableCell className="text-right font-mono">{p.qty}</TableCell>
            <TableCell className="text-right font-mono">{usd(p.avg_entry_price)}</TableCell>
            <TableCell className="text-right font-mono">{usd(p.current_price)}</TableCell>
            <TableCell className="text-right font-mono">{usd(p.market_value)}</TableCell>
            <TableCell className={cn("text-right font-mono", pnlClass(p.unrealized_pl))}>
              {usd(p.unrealized_pl)} <span className="text-[10px]">{pct(p.unrealized_pl_pct)}</span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function AlpacaOrdersTable({ orders }: { orders: AlpacaOrderResponse[] }) {
  if (orders.length === 0)
    return <p className="text-xs text-muted-foreground">No orders in this window.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Created</TableHead>
          <TableHead>Symbol</TableHead>
          <TableHead>Side</TableHead>
          <TableHead>Type</TableHead>
          <TableHead className="text-right">Filled / qty</TableHead>
          <TableHead className="text-right">Price</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {orders.map((o) => (
          <TableRow key={o.id}>
            <TableCell className="font-mono text-xs text-muted-foreground">
              {shortTime(o.created_at)}
            </TableCell>
            <TableCell className="font-mono">{o.symbol}</TableCell>
            <TableCell className="text-xs">{o.position_intent ?? o.side}</TableCell>
            <TableCell className="text-xs">
              {o.type} · {o.time_in_force}
            </TableCell>
            <TableCell className="text-right font-mono">
              {o.filled_qty}/{o.qty ?? "—"}
            </TableCell>
            <TableCell className="text-right font-mono">
              {o.filled_avg_price != null
                ? usd(o.filled_avg_price)
                : o.limit_price != null
                  ? `lmt ${usd(o.limit_price)}`
                  : o.stop_price != null
                    ? `stp ${usd(o.stop_price)}`
                    : "mkt"}
            </TableCell>
            <TableCell>
              <StatusBadge status={o.status} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function AlpacaFillsTable({ fills }: { fills: AlpacaFillResponse[] }) {
  if (fills.length === 0)
    return <p className="text-xs text-muted-foreground">No fills in this window.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Symbol</TableHead>
          <TableHead>Side</TableHead>
          <TableHead className="text-right">Qty</TableHead>
          <TableHead className="text-right">Price</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {fills.map((f) => (
          <TableRow key={f.id}>
            <TableCell className="font-mono text-xs text-muted-foreground">
              {shortTime(f.transaction_time)}
            </TableCell>
            <TableCell className="font-mono">{f.symbol}</TableCell>
            <TableCell className="text-xs">{f.side}</TableCell>
            <TableCell className="text-right font-mono">{f.qty}</TableCell>
            <TableCell className="text-right font-mono">{usd(f.price)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
