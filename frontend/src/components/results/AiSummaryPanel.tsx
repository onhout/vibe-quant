import { useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { isApiError } from "@/api/client";
import {
  getGetAiSummaryApiResultsRunsRunIdAiSummaryGetQueryKey,
  useCreateAiSummaryApiResultsRunsRunIdAiSummaryPost,
  useGetAiSummaryApiResultsRunsRunIdAiSummaryGet,
} from "@/api/generated/results/results";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatRelative } from "@/lib/time";

interface Props {
  runId: number;
}

/** Plain-language review of the run by the research model (stored per run, regenerated on demand). */
export function AiSummaryPanel({ runId }: Props) {
  const queryClient = useQueryClient();
  const summaryQuery = useGetAiSummaryApiResultsRunsRunIdAiSummaryGet(runId, {
    query: { retry: false },
  });
  const create = useCreateAiSummaryApiResultsRunsRunIdAiSummaryPost();
  const summary = summaryQuery.data?.status === 200 ? summaryQuery.data.data : null;

  const handleWrite = () => {
    create.mutate(
      { runId },
      {
        onSuccess: (resp) => {
          if (resp.status === 200) {
            queryClient.setQueryData(
              getGetAiSummaryApiResultsRunsRunIdAiSummaryGetQueryKey(runId),
              resp,
            );
          }
        },
        onError: (err) => {
          const detail = isApiError(err) ? (err.body as { detail?: unknown })?.detail : undefined;
          toast.error(typeof detail === "string" ? detail : "Summary failed");
        },
      },
    );
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Sparkles className="h-4 w-4 text-primary" />
          AI summary
        </CardTitle>
        <div className="flex items-center gap-3">
          {summary && (
            <span className="text-[10px] text-muted-foreground">
              {summary.model} · {formatRelative(summary.created_at)}
            </span>
          )}
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            disabled={create.isPending}
            onClick={handleWrite}
          >
            {create.isPending ? "Writing…" : summary ? "Regenerate" : "Write summary"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {summary ? (
          <article className="prose prose-invert prose-sm max-w-none prose-headings:text-foreground prose-p:text-muted-foreground prose-li:text-muted-foreground prose-strong:text-foreground">
            <Markdown remarkPlugins={[remarkGfm]}>{summary.summary}</Markdown>
          </article>
        ) : (
          <p className="text-xs text-muted-foreground">
            {create.isPending
              ? "The research model is reading this run…"
              : "No summary yet. The research model reads the rules, metrics and trades and writes a verdict, red flags and a next step."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
