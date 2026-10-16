import { useGetProposalApiOptionsBotProposalGet } from "@/api/generated/options-bot/options-bot";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import {
  asArray,
  asRecord,
  contractLabel,
  fixed,
  type LooseRecord,
  num,
  shortTime,
  show,
  str,
  usd,
} from "./format";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 truncate font-mono text-lg font-bold tracking-tight">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted-foreground/70">{sub}</p>}
    </div>
  );
}

/** Key/value grid for loosely shaped records (trade, shadow, AI output, measurements). */
function KeyValues({ data, skip = [] }: { data: LooseRecord | null; skip?: string[] }) {
  const entries = data
    ? Object.entries(data).filter(([k, v]) => !skip.includes(k) && v != null)
    : [];
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">—</p>;
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

function LinkList({ items }: { items: LooseRecord[] }) {
  return (
    <ul className="space-y-1 text-xs">
      {items.map((item, i) => {
        const link = str(item.link);
        const title = str(item.title) ?? "(untitled)";
        const meta = str(item.subreddit) ? `r/${str(item.subreddit)}` : str(item.source);
        return (
          <li key={link ?? i} className="truncate">
            {meta && <span className="text-muted-foreground">{meta} · </span>}
            {link ? (
              <a
                href={link}
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:underline"
              >
                {title}
              </a>
            ) : (
              title
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ProposalDetailDialog({
  proposalId,
  onClose,
}: {
  proposalId: string | null;
  onClose: () => void;
}) {
  const query = useGetProposalApiOptionsBotProposalGet(
    { proposal_id: proposalId ?? "" },
    { query: { enabled: proposalId !== null } },
  );
  const detail = query.data?.status === 200 ? query.data.data : null;
  const p = detail ? asRecord(detail.proposal) : null;
  const ev = detail ? asRecord(detail.evidence) : null;
  const ai = asRecord(ev?.ai);
  const cited = asRecord(ai?.cited);
  const posts = asArray(ev?.posts)
    .map(asRecord)
    .filter((x): x is LooseRecord => x !== null);
  const headlines = asArray(ev?.headlines)
    .map(asRecord)
    .filter((x): x is LooseRecord => x !== null);
  const flags = [...asArray(p?.fatal_flags).map(String)];
  const missing = [...asArray(p?.missing_fields).map(String)];

  return (
    <Dialog open={proposalId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            {p ? (
              <>
                <span className="font-mono">{str(p.ticker)}</span>
                <span className="text-muted-foreground">
                  {contractLabel({
                    strike: num(p.strike),
                    option_type: str(p.option_type),
                    expiration: str(p.expiration),
                    contract: str(p.contract),
                  })}
                </span>
                <span className="text-sm text-muted-foreground">
                  {str(p.decision) ?? str(p.status)}
                </span>
              </>
            ) : (
              "Proposal"
            )}
          </DialogTitle>
        </DialogHeader>
        {query.isLoading || !p ? (
          <LoadingSpinner />
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat label="Setup" value={str(p.selected_setup) ?? "—"} />
              <Stat
                label="Entry score"
                value={fixed(num(p.entry_score), 1)}
                sub={`social ${fixed(num(p.social_score), 1)} · setup ${fixed(num(p.combined_setup_score), 1)}`}
              />
              <Stat
                label="Limit × qty"
                value={`${usd(num(p.proposed_limit))} × ${show(p.proposed_quantity)}`}
                sub={`max debit ${usd(num(p.maximum_debit))}`}
              />
              <Stat
                label="Underlying"
                value={usd(num(p.underlying_at_proposal))}
                sub={`delta ${fixed(num(p.option_delta_at_proposal))} · ${show(p.dte)} DTE`}
              />
            </div>
            {(str(p.thesis) || str(p.ai_summary)) && (
              <Section title="Thesis">
                {str(p.thesis) && <p className="text-sm">{str(p.thesis)}</p>}
                {str(p.ai_summary) && (
                  <p className="text-sm text-muted-foreground">{str(p.ai_summary)}</p>
                )}
              </Section>
            )}
            {flags.length + missing.length > 0 && (
              <Section title="Why it didn't qualify">
                <div className="flex flex-wrap gap-1.5 text-[11px]">
                  {flags.map((f) => (
                    <span
                      key={f}
                      className="rounded border border-red-500/25 bg-red-500/15 px-1.5 py-0.5 font-mono text-red-300"
                    >
                      {f}
                    </span>
                  ))}
                  {missing.map((f) => (
                    <span
                      key={f}
                      className="rounded border border-amber-500/25 bg-amber-500/15 px-1.5 py-0.5 font-mono text-amber-300"
                    >
                      missing {f}
                    </span>
                  ))}
                </div>
              </Section>
            )}
            <div className="grid gap-6 md:grid-cols-2">
              <Section title="Trade">
                <KeyValues data={asRecord(p.trade)} />
              </Section>
              <Section title="Shadow (what it would have done)">
                <KeyValues data={asRecord(p.shadow)} />
              </Section>
            </div>
            {ai && (
              <Section title="AI read of Reddit & news">
                <KeyValues data={ai} skip={["cited"]} />
                {cited && Object.keys(cited).length > 0 && (
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
            {asRecord(ev?.reddit_measurements) && (
              <Section title="Reddit counts">
                <KeyValues data={asRecord(ev?.reddit_measurements)} />
              </Section>
            )}
            {posts.length > 0 && (
              <Section title={`Reddit posts (${posts.length})`}>
                <LinkList items={posts} />
              </Section>
            )}
            {headlines.length > 0 && (
              <Section title="Headlines">
                <LinkList items={headlines} />
              </Section>
            )}
            {detail && detail.events.length > 0 && (
              <Section title="Events">
                <ul className="space-y-1 text-xs">
                  {detail.events.map((e) => (
                    <li key={e.id} className="flex gap-3">
                      <span className="shrink-0 font-mono text-muted-foreground">
                        {shortTime(e.at)}
                      </span>
                      <span className="shrink-0 font-medium">{e.type}</span>
                      <span className="truncate text-muted-foreground">{show(e.payload)}</span>
                    </li>
                  ))}
                </ul>
              </Section>
            )}
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
