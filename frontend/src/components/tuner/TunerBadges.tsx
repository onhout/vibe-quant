import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const TONES: Record<string, string> = {
  win: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  settled: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  pass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  lose: "bg-red-500/15 text-red-300 border-red-500/25",
  fail: "bg-red-500/15 text-red-300 border-red-500/25",
  exhausted: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  error: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  invalid: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  blocked: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  flat: "bg-muted text-muted-foreground border-border",
  testing: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  tuning: "bg-violet-500/15 text-violet-300 border-violet-500/25",
  running: "bg-blue-500/15 text-blue-300 border-blue-500/25 animate-pulse",
  bull: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
  bear: "bg-red-500/10 text-red-300 border-red-500/20",
  wide: "bg-sky-500/10 text-sky-300 border-sky-500/20",
};

export function TunerBadge({ value, className }: { value: string; className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        TONES[value] ?? "bg-muted text-muted-foreground border-border",
        "font-mono text-[10px]",
        className,
      )}
    >
      {value}
    </Badge>
  );
}
