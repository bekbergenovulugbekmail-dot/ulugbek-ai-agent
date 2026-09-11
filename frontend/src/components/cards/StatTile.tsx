import Link from "next/link";

import { cx } from "@/lib/format";
import { TONE_DOT, type Tone } from "@/lib/status";

/**
 * A dashboard counter.
 *
 * The number is the hero; the label sits under it. A tone is only applied when
 * the value means something is waiting on the operator.
 */
export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
  href,
  emphasise = false,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: Tone;
  href?: string;
  emphasise?: boolean;
}) {
  const body = (
    <>
      <div className="flex items-center gap-2">
        <span className="label-caps">{label}</span>
        {emphasise && (
          <span
            aria-hidden
            className={cx(
              "h-1.5 w-1.5 animate-pulse-soft rounded-full",
              TONE_DOT[tone],
            )}
          />
        )}
      </div>
      <p
        className={cx(
          "mt-2 font-semibold tabular-nums tracking-tight",
          emphasise ? "text-3xl" : "text-2xl",
          tone === "warn" && emphasise && "text-warn",
          tone === "danger" && emphasise && "text-danger",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 text-xs text-ink-faint">{hint}</p>}
    </>
  );

  const className = cx(
    "panel px-4 py-3.5",
    href && "panel-hover block",
  );

  return href ? (
    <Link href={href} className={className} data-testid="stat-tile">
      {body}
    </Link>
  ) : (
    <div className={className} data-testid="stat-tile">
      {body}
    </div>
  );
}
