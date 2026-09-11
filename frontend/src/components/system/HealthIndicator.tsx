"use client";

import type { ComponentHealth } from "@/lib/api";
import { cx } from "@/lib/format";
import { TONE_DOT, healthTone } from "@/lib/status";

export function HealthIndicator({
  component,
  compact = false,
}: {
  component: ComponentHealth;
  compact?: boolean;
}) {
  const tone = healthTone(component.status);
  return (
    <div
      className={cx(
        "flex w-full items-center gap-2",
        compact ? "text-xs" : "text-sm",
      )}
      title={component.detail ?? component.status}
    >
      <span
        aria-hidden
        className={cx(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          TONE_DOT[tone],
          component.status === "ok" && "shadow-[0_0_6px_currentColor]",
        )}
      />
      {/* min-w-0 is what lets `truncate` actually shrink inside a flex row —
          without it a long detail string forces the whole page wider. */}
      <span className="shrink-0 text-ink-muted">{component.name}</span>
      {!compact && component.detail && (
        <span className="ml-auto min-w-0 truncate text-xs text-ink-faint">
          {component.detail}
        </span>
      )}
      <span className="sr-only">{component.status}</span>
    </div>
  );
}

export function HealthList({
  components,
  compact = false,
}: {
  components: ComponentHealth[];
  compact?: boolean;
}) {
  return (
    <div className={cx("space-y-1.5")} aria-label="System status">
      {components.map((component) => (
        <HealthIndicator
          key={component.name}
          component={component}
          compact={compact}
        />
      ))}
    </div>
  );
}
