const LABELS: Record<string, string> = {
  hook: "Hook", clarity: "Clarity", emotion: "Emotion", novelty: "Novelty", information: "Information",
  completeness: "Completeness", visual: "Visual", audio: "Audio", relevance: "Relevance", platform: "Platform fit",
};

export function ScoreBars({ scores }: { scores: Record<string, number> }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
      {Object.entries(LABELS).map(([key, label]) => {
        const value = scores[key];
        if (value === undefined) return null;
        return (
          <div key={key} className="flex items-center gap-2 text-[12px]">
            <span className="w-24 shrink-0 text-muted">{label}</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
              <div className="h-full rounded-full bg-primary" style={{ width: `${Math.round(value * 100)}%` }} />
            </div>
            <span className="w-8 text-right tabular-nums text-faint">{Math.round(value * 100)}</span>
          </div>
        );
      })}
    </div>
  );
}
