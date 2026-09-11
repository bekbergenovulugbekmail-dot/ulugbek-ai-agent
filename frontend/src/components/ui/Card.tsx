import { cx } from "@/lib/format";

export function Card({
  children,
  className,
  interactive = false,
  as: Tag = "div",
}: {
  children: React.ReactNode;
  className?: string;
  interactive?: boolean;
  as?: "div" | "article" | "section" | "li";
}) {
  return (
    <Tag className={cx("panel", interactive && "panel-hover", className)}>
      {children}
    </Tag>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
  className,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex items-start justify-between gap-4 border-b border-line px-5 py-4",
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
        {subtitle && (
          <p className="mt-0.5 truncate text-xs text-ink-faint">{subtitle}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
