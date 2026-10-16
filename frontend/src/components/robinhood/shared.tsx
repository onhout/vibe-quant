import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import {
  type Proposal,
  type TradeRecord,
  num,
  pct,
  usd,
  useProposalDetail,
  when,
} from "@/hooks/useRobinhood";
import { cn } from "@/lib/utils";

export function Panel({
  title,
  action,
  children,
  className,
}: {
  title?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn("rounded-xl border border-border/60 bg-card/40 p-5 backdrop-blur-sm", className)}
    >
      {(title || action) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          {title && (
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
              {title}
            </h2>
          )}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "profit" | "loss" | undefined;
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <p
        className={cn(
          "mt-1 truncate font-mono text-xl font-bold tracking-tight",
          tone === "profit" && "text-profit",
          tone === "loss" && "text-loss",
        )}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-muted-foreground/70">{sub}</p>}
    </div>
  );
}

export function tone(value: number | null | undefined): "profit" | "loss" | undefined {
  if (value === null || value === undefined || value === 0) return undefined;
  return value > 0 ? "profit" : "loss";
}

const PILL: Record<string, string> = {
  green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  red: "bg-red-500/15 text-red-300 border-red-500/25",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  blue: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  violet: "bg-violet-500/15 text-violet-300 border-violet-500/25",
  gray: "bg-muted text-muted-foreground border-border",
};

export function Pill({
  color = "gray",
  children,
  className,
}: {
  color?: keyof typeof PILL;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Badge variant="outline" className={cn(PILL[color], "font-mono text-[10px]", className)}>
      {children}
    </Badge>
  );
}

export function tradeColor(status: string | undefined): keyof typeof PILL {
  switch (status) {
    case "OPEN":
    case "ENTRY_WORKING":
    case "ENTRY_PENDING":
    case "EXITING":
      return "blue";
    case "CLOSED":
      return "violet";
    case "SKIPPED":
    case "CANCELLED":
    case "EXPIRED":
      return "gray";
    case "REJECTED":
    case "ERROR":
      return "red";
    default:
      return "gray";
  }
}

export function Markdownish({ text }: { text: string }) {
  return (
    <article className="prose prose-invert prose-sm max-w-none prose-headings:text-foreground prose-p:text-muted-foreground prose-li:text-muted-foreground prose-strong:text-foreground prose-a:text-primary prose-th:text-foreground prose-td:text-muted-foreground prose-code:text-primary/90 prose-hr:border-border">
      <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
    </article>
  );
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** Key/value grid for loosely shaped records (trade, shadow, AI output, measurements). */
export function KeyValues({ data, skip = [] }: { data: Record<string, unknown> | null | undefined; skip?: string[] }) {
  if (!data) return <p className="text-xs text-muted-foreground">—</p>;
  const entries = Object.entries(data).filter(([k, v]) => !skip.includes(k) && v !== null && v !== undefined);
  if (!entries.length) return <p className="text-xs text-muted-foreground">—</p>;
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex min-w-0 gap-2">
          <dt className="shrink-0 text-muted-foreground">{k.replaceAll("_", " ")}</dt>
          <dd className="min-w-0 break-words font-mono text-foreground/90">{show(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function TradeCell({ record }: { record: TradeRecord | null }) {
  if (!record?.status) return <span className="text-muted-foreground/50">—</span>;
  return (
    <div className="flex items-center gap-1.5">
      <Pill color={tradeColor(record.status)}>{record.status}</Pill>
      {record.mode === "live" && <Pill color="red">LIVE</Pill>}
      {record.return_pct !== null && record.return_pct !== undefined && (
        <span className={cn("font-mono text-xs", record.return_pct >= 0 ? "text-profit" : "text-loss")}>
          {pct(record.return_pct)}
        </span>
      )}
    </div>
  );
}

export function contractLabel(p: Proposal): string {
  if (!p.option_type) return p.contract ?? "—";
  const kind = p.option_type.toLowerCase().startsWith("c") ? "C" : "P";
  return `${p.strike ?? "?"}${kind} ${p.expiration ?? ""}`.trim();
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
      {children}
    </div>
  );
}

type Post = { subreddit?: string; author?: string; created?: string; title?: string; link?: string };
type Headline = { title?: string; link?: string; source?: string; published?: string };

export function ProposalDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const detail = useProposalDetail(id);
  const p = detail.data?.proposal as (Proposal & Record<string, unknown>) | undefined;
  const ev = (detail.data?.evidence ?? {}) as Record<string, unknown>;
  const ai = (ev.ai ?? null) as Record<string, unknown> | null;
  const cited = (ai?.cited ?? {}) as Record<string, unknown>;
  const posts = (ev.posts ?? []) as Post[];
  const headlines = (ev.headlines ?? []) as Headline[];

  return (
    <Dialog open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            {p ? (
              <>
                <span className="font-mono">{p.ticker}</span>
                <span className="text-muted-foreground">{contractLabel(p)}</span>
                <Pill color={p.trade_eligible ? "green" : "gray"}>{p.decision ?? p.status}</Pill>
                {p.lab && <Pill color="violet">Lab: {p.lab.name ?? p.lab.strategy_id}</Pill>}
              </>
            ) : (
              "Proposal"
            )}
          </DialogTitle>
        </DialogHeader>
        {detail.isLoading || !p ? (
          <LoadingSpinner />
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat label="Setup" value={p.selected_setup ?? "—"} />
              <Stat label="Entry score" value={num(p.entry_score, 1)} sub={`social ${num(p.social_score, 1)} · setup ${num(p.combined_setup_score, 1)}`} />
              <Stat label="Limit × qty" value={`${usd(p.proposed_limit)} × ${p.proposed_quantity ?? "—"}`} sub={`max debit ${usd(p.maximum_debit)}`} />
              <Stat label="Underlying" value={usd(p.underlying_at_proposal)} sub={`delta ${num(p.option_delta_at_proposal)} · ${p.dte ?? "?"} DTE`} />
            </div>
            {(p.thesis || p.ai_summary) && (
              <Section title="Thesis">
                {p.thesis && <p className="text-sm">{p.thesis}</p>}
                {p.ai_summary && <p className="text-sm text-muted-foreground">{p.ai_summary}</p>}
              </Section>
            )}
            {(p.fatal_flags?.length || p.missing_fields?.length) ? (
              <Section title="Why it didn't qualify">
                <div className="flex flex-wrap gap-1.5">
                  {(p.fatal_flags ?? []).map((f) => (
                    <Pill key={f} color="red">{f}</Pill>
                  ))}
                  {(p.missing_fields ?? []).map((f) => (
                    <Pill key={f} color="amber">missing {f}</Pill>
                  ))}
                </div>
              </Section>
            ) : null}
            <div className="grid gap-6 md:grid-cols-2">
              <Section title="Trade">
                <KeyValues data={p.trade as Record<string, unknown> | null} />
              </Section>
              <Section title="Shadow (what it would have done)">
                <KeyValues data={p.shadow as Record<string, unknown> | null} />
              </Section>
            </div>
            {p.exit_rules && (
              <Section title="Exit rules">
                <KeyValues data={p.exit_rules} />
              </Section>
            )}
            {ai && (
              <Section title="AI read of Reddit & news">
                <KeyValues data={ai} skip={["cited"]} />
                {Object.keys(cited).length > 0 && (
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {Object.entries(cited).map(([k, v]) => (
                      <li key={k}>
                        <span className="font-mono text-foreground/70">{k}</span> {show(v)}
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
            )}
            {ev.reddit_measurements ? (
              <Section title="Reddit counts">
                <KeyValues data={ev.reddit_measurements as Record<string, unknown>} />
              </Section>
            ) : null}
            {posts.length > 0 && (
              <Section title={`Reddit posts (${posts.length})`}>
                <ul className="space-y-1 text-xs">
                  {posts.map((post, i) => (
                    <li key={`${post.link ?? i}`} className="truncate">
                      <span className="text-muted-foreground">r/{post.subreddit} · </span>
                      {post.link ? (
                        <a href={post.link} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                          {post.title}
                        </a>
                      ) : (
                        post.title
                      )}
                    </li>
                  ))}
                </ul>
              </Section>
            )}
            {headlines.length > 0 && (
              <Section title="Headlines">
                <ul className="space-y-1 text-xs">
                  {headlines.map((h, i) => (
                    <li key={`${h.link ?? i}`} className="truncate">
                      {h.link ? (
                        <a href={h.link} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                          {h.title}
                        </a>
                      ) : (
                        h.title
                      )}
                      {h.source && <span className="text-muted-foreground"> · {h.source}</span>}
                    </li>
                  ))}
                </ul>
              </Section>
            )}
            {detail.data?.events.length ? (
              <Section title="Events">
                <ul className="space-y-1 text-xs">
                  {detail.data.events.map((e, i) => (
                    <li key={`${e.at}-${i}`} className="flex gap-3">
                      <span className="shrink-0 font-mono text-muted-foreground">{when(e.at)}</span>
                      <span className="shrink-0 font-medium">{e.type}</span>
                      <span className="truncate text-muted-foreground">{show(e.payload)}</span>
                    </li>
                  ))}
                </ul>
              </Section>
            ) : null}
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">Everything (raw)</summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted/40 p-3">
                {JSON.stringify({ proposal: p, evidence: ev }, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
