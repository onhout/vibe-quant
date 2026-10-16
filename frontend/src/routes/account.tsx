import { useState } from "react";
import {
  useGetAccountApiAlpacaAccountGet,
  useGetFillsApiAlpacaFillsGet,
  useGetOrdersApiAlpacaOrdersGet,
  useGetPositionsApiAlpacaPositionsGet,
} from "@/api/generated/alpaca/alpaca";
import type { GetAccountApiAlpacaAccountGetAccount } from "@/api/generated/models";
import {
  AlpacaFillsTable,
  AlpacaOrdersTable,
  AlpacaPositionsTable,
} from "@/components/alpaca/AccountTables";
import { formatApiErrorDetail } from "@/components/discovery/apiErrorDetail";
import { usd } from "@/components/options-bot/format";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { MetricCard } from "@/components/ui/MetricCard";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const ACCOUNTS: { value: GetAccountApiAlpacaAccountGetAccount; label: string }[] = [
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
];

export function AccountPage() {
  const [account, setAccount] = useState<GetAccountApiAlpacaAccountGetAccount>("paper");
  const params = { account };
  const accountQuery = useGetAccountApiAlpacaAccountGet(params, {
    query: { refetchInterval: 30_000, retry: 1 },
  });
  const ok = accountQuery.data?.status === 200;
  const positionsQuery = useGetPositionsApiAlpacaPositionsGet(params, {
    query: { enabled: ok, refetchInterval: 15_000 },
  });
  const ordersQuery = useGetOrdersApiAlpacaOrdersGet(params, {
    query: { enabled: ok, refetchInterval: 15_000 },
  });
  const fillsQuery = useGetFillsApiAlpacaFillsGet(params, {
    query: { enabled: ok, refetchInterval: 30_000 },
  });

  const summary = accountQuery.data?.status === 200 ? accountQuery.data.data : null;
  const positions = positionsQuery.data?.status === 200 ? positionsQuery.data.data : [];
  const orders = ordersQuery.data?.status === 200 ? ordersQuery.data.data : [];
  const fills = fillsQuery.data?.status === 200 ? fillsQuery.data.data : [];
  const openOrders = orders.filter(
    (o) =>
      !["filled", "canceled", "expired", "rejected", "replaced", "done_for_day"].includes(o.status),
  );

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-md border border-border/60 p-0.5">
          {ACCOUNTS.map((a) => (
            <button
              key={a.value}
              type="button"
              onClick={() => setAccount(a.value)}
              className={cn(
                "rounded px-3 py-1 text-xs font-semibold transition-colors",
                a.value === account
                  ? a.value === "live"
                    ? "bg-red-600 text-white"
                    : "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {a.label}
            </button>
          ))}
        </div>
        {summary && (
          <p className="text-xs text-muted-foreground">
            Alpaca {summary.account} account {summary.account_number} · {summary.status}
            {summary.options_trading_level != null &&
              ` · options level ${summary.options_trading_level}`}
          </p>
        )}
      </div>

      {accountQuery.isLoading ? (
        <LoadingSpinner />
      ) : !summary ? (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-300">
          {formatApiErrorDetail(accountQuery.error) || "Alpaca account unavailable"}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard
              label="Equity"
              value={usd(summary.equity)}
              subtitle={`portfolio ${usd(summary.portfolio_value)}`}
            />
            <MetricCard label="Cash" value={usd(summary.cash)} />
            <MetricCard label="Buying power" value={usd(summary.buying_power)} />
            <MetricCard label="Options buying power" value={usd(summary.options_buying_power)} />
          </div>
          <Tabs defaultValue="positions">
            <TabsList>
              <TabsTrigger value="positions">Positions ({positions.length})</TabsTrigger>
              <TabsTrigger value="open">Open orders ({openOrders.length})</TabsTrigger>
              <TabsTrigger value="orders">Orders, 14 days</TabsTrigger>
              <TabsTrigger value="fills">Fills, 14 days</TabsTrigger>
            </TabsList>
            <TabsContent value="positions" className="mt-4">
              <AlpacaPositionsTable positions={positions} />
            </TabsContent>
            <TabsContent value="open" className="mt-4">
              <AlpacaOrdersTable orders={openOrders} />
            </TabsContent>
            <TabsContent value="orders" className="mt-4">
              <AlpacaOrdersTable orders={orders} />
            </TabsContent>
            <TabsContent value="fills" className="mt-4">
              <AlpacaFillsTable fills={fills} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
