"use client";

import { cx } from "@/lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-white hover:bg-accent/90 disabled:bg-accent/40 border-transparent",
  secondary:
    "bg-white/5 text-ink hover:bg-white/10 border-line hover:border-line-strong",
  ghost: "bg-transparent text-ink-muted hover:text-ink hover:bg-white/5 border-transparent",
  danger:
    "bg-danger-soft text-danger hover:bg-danger/20 border-danger/30",
};

export function Button({
  children,
  variant = "secondary",
  size = "md",
  loading = false,
  className,
  type = "button",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  loading?: boolean;
}) {
  return (
    <button
      type={type}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg border font-medium transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-60",
        size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-3.5 py-2 text-sm",
        VARIANTS[variant],
        className,
      )}
      disabled={props.disabled || loading}
      {...props}
    >
      {loading && (
        <span
          aria-hidden
          className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  );
}
