import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { isApiError } from "@/api/client";
import { useSubmitIdeaApiResearchIdeasPost } from "@/api/generated/research/research";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

interface Props {
  open: boolean;
  onClose: () => void;
}

/**
 * "Describe a strategy in plain English": the idea becomes a research item, the extraction
 * worker writes its DSL (and auto-screens it), and the item page offers Promote → Backtest.
 */
export function StrategyIdeaDialog({ open, onClose }: Props) {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const submit = useSubmitIdeaApiResearchIdeasPost();

  const canSubmit =
    title.trim().length >= 3 && description.trim().length >= 20 && !submit.isPending;

  const handleSubmit = () => {
    submit.mutate(
      { data: { title: title.trim(), description: description.trim() } },
      {
        onSuccess: (resp) => {
          if (resp.status !== 201) {
            toast.error("Could not submit the idea");
            return;
          }
          toast.success("Idea queued — the model is writing the strategy");
          setTitle("");
          setDescription("");
          onClose();
          navigate({ to: "/research/$itemId", params: { itemId: String(resp.data.item_id) } });
        },
        onError: (err) => {
          const detail = isApiError(err) ? (err.body as { detail?: unknown })?.detail : undefined;
          toast.error(typeof detail === "string" ? detail : "Could not submit the idea");
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Describe a strategy</DialogTitle>
          <DialogDescription>
            Write the rules in plain English — entries, exits, stops, timeframe. The research model
            turns them into a strategy, screens it, and you can promote it to a backtest.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Name, e.g. RSI(2) dip buy on SPY"
            aria-label="Strategy name"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={8}
            placeholder="e.g. Daily bars. Buy when RSI(2) closes below 10 and price is above the 200-day SMA. Sell when RSI(2) closes above 70 or after 5 days. 3% stop."
            aria-label="Strategy description"
            className="w-full rounded-md border border-border/60 bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-primary/60"
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" disabled={!canSubmit} onClick={handleSubmit}>
            {submit.isPending ? "Submitting…" : "Write strategy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
