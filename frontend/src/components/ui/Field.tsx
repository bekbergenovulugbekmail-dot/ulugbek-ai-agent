"use client";

import { cx } from "@/lib/format";

export function TextInput({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cx(
        "w-full rounded-lg border border-line bg-elevated px-3 py-2 text-sm text-ink",
        "placeholder:text-ink-faint focus:border-accent-line",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cx(
        "rounded-lg border border-line bg-elevated px-3 py-2 text-sm text-ink",
        "focus:border-accent-line",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

export function FilterTabs<T extends string>({
  options,
  value,
  onChange,
  counts,
}: {
  options: readonly T[];
  value: T;
  onChange: (next: T) => void;
  counts?: Partial<Record<T, number>>;
}) {
  return (
    <div
      role="tablist"
      className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1"
    >
      {options.map((option) => {
        const active = option === value;
        return (
          <button
            key={option}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option)}
            className={cx(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              active
                ? "bg-accent-soft text-accent"
                : "text-ink-faint hover:bg-white/5 hover:text-ink-muted",
            )}
          >
            {option.replace(/_/g, " ")}
            {counts?.[option] !== undefined && (
              <span className="ml-1.5 text-ink-faint">{counts[option]}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
