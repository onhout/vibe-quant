import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { useListStrategiesApiStrategiesGet } from "@/api/generated/strategies/strategies";
import {
  getGetTunerApiTunerGetQueryKey,
  useQueueStrategyApiTunerQueuePost,
} from "@/api/generated/tuner/tuner";
import { formatApiErrorDetail } from "@/components/discovery/apiErrorDetail";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function QueueStrategy({ timeframes }: { timeframes: string[] }) {
  const queryClient = useQueryClient();
  const strategies = useListStrategiesApiStrategiesGet();
  const [selected, setSelected] = useState<string>("");
  const queue = useQueueStrategyApiTunerQueuePost({
    mutation: {
      onSuccess: () => {
        toast.success("Queued: the tuner takes it next");
        void queryClient.invalidateQueries({ queryKey: getGetTunerApiTunerGetQueryKey() });
      },
      onError: (err) => toast.error(formatApiErrorDetail(err) || "Could not queue the strategy"),
    },
  });
  const options =
    strategies.data?.status === 200
      ? strategies.data.data.strategies.filter(
          (s) =>
            s.strategy_type !== "tuned" &&
            timeframes.includes(String(s.dsl_config.timeframe ?? "")),
        )
      : [];
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={selected} onValueChange={setSelected}>
        <SelectTrigger className="h-8 w-72 text-xs">
          <SelectValue placeholder="Pick a strategy to tune" />
        </SelectTrigger>
        <SelectContent>
          {options.map((s) => (
            <SelectItem key={s.id} value={String(s.id)} className="text-xs">
              #{s.id} {s.name} · {String(s.dsl_config.timeframe ?? "")}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        size="sm"
        disabled={!selected || queue.isPending}
        onClick={() => queue.mutate({ data: { strategy_id: Number(selected), restart: false } })}
      >
        Tune next
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={!selected || queue.isPending}
        onClick={() => queue.mutate({ data: { strategy_id: Number(selected), restart: true } })}
      >
        Tune again from scratch
      </Button>
    </div>
  );
}
