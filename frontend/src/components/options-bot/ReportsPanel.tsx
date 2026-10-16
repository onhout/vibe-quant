import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  useGetBotReportApiOptionsBotReportsReportDateGet,
  useListBotReportsApiOptionsBotReportsGet,
} from "@/api/generated/options-bot/options-bot";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { cn } from "@/lib/utils";

export function ReportsPanel() {
  const listQuery = useListBotReportsApiOptionsBotReportsGet();
  const dates = listQuery.data?.status === 200 ? listQuery.data.data : [];
  const [picked, setPicked] = useState<string | null>(null);
  const selected = picked ?? dates[0] ?? null;
  const reportQuery = useGetBotReportApiOptionsBotReportsReportDateGet(selected ?? "", {
    query: { enabled: selected !== null },
  });
  const markdown = reportQuery.data?.status === 200 ? reportQuery.data.data.markdown : "";

  if (listQuery.isLoading) return <LoadingSpinner />;
  if (dates.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No daily reports yet: the bot writes one at 16:10 ET each trading day.
      </p>
    );
  }
  return (
    <div className="grid gap-6 md:grid-cols-[10rem_1fr]">
      <ul className="space-y-1 text-sm">
        {dates.map((d) => (
          <li key={d}>
            <button
              type="button"
              onClick={() => setPicked(d)}
              className={cn(
                "w-full rounded px-2 py-1 text-left font-mono",
                d === selected
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {d}
            </button>
          </li>
        ))}
      </ul>
      <article className="prose prose-invert prose-sm max-w-none prose-headings:text-foreground prose-p:text-muted-foreground prose-li:text-muted-foreground prose-strong:text-foreground prose-a:text-primary prose-th:text-foreground prose-td:text-muted-foreground prose-code:text-primary/90 prose-hr:border-border">
        {reportQuery.isLoading ? (
          <LoadingSpinner />
        ) : (
          <Markdown remarkPlugins={[remarkGfm]}>{markdown}</Markdown>
        )}
      </article>
    </div>
  );
}
