"use client";

import { cx } from "@/lib/format";

/**
 * Step progress for a plan.
 *
 * Deliberately a count plus a bar rather than a percentage of "thinking":
 * anything else would be invented precision.
 */
export function ProgressIndicator({
  current,
  total,
  label,
  tone = "accent",
}: {
  current: number;
  total: number;
  label?: string;
  tone?: "accent" | "ok" | "danger";
}) {
  const safeTotal = Math.max(total, 0);
  const clamped = safeTotal === 0 ? 0 : Math.min(current, safeTotal);
  const percent = safeTotal === 0 ? 0 : Math.round((clamped / safeTotal) * 100);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-2xs text-ink-faint">
        <span>{label ?? "Progress"}</span>
        <span className="font-mono">
          {clamped}/{safeTotal || "—"}
        </span>
      </div>
      <div
        className="h-1 w-full overflow-hidden rounded-full bg-white/[0.06]"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cx(
            "h-full rounded-full transition-all duration-500",
            tone === "accent" && "bg-accent",
            tone === "ok" && "bg-ok",
            tone === "danger" && "bg-danger",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
