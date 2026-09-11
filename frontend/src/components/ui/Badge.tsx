import { cx } from "@/lib/format";
import { TONE_CLASSES, TONE_DOT, type Tone } from "@/lib/status";

export function Badge({
  children,
  tone = "neutral",
  dot = false,
  pulse = false,
  className,
}: {
  children: React.ReactNode;
  tone?: Tone;
  dot?: boolean;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-2xs font-medium uppercase tracking-wider",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {dot && (
        <span
          className={cx(
            "h-1.5 w-1.5 rounded-full",
            TONE_DOT[tone],
            pulse && "animate-pulse-soft",
          )}
        />
      )}
      {children}
    </span>
  );
}
