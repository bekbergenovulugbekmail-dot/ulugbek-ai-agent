"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { healthApi } from "@/lib/api";
import { cx } from "@/lib/format";
import { useResource } from "@/lib/hooks/useResource";

import { HealthList } from "../system/HealthIndicator";

export interface NavItem {
  href: string;
  label: string;
  glyph: string;
  badgeKey?: "pending_approvals" | "active_tasks";
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", glyph: "◱" },
  { href: "/agent", label: "Agent", glyph: "◈" },
  { href: "/projects", label: "Projects", glyph: "▤" },
  { href: "/tasks", label: "Tasks", glyph: "☰", badgeKey: "active_tasks" },
  { href: "/memory", label: "Memory", glyph: "◇" },
  { href: "/approvals", label: "Approvals", glyph: "⏳", badgeKey: "pending_approvals" },
  { href: "/tools", label: "Tools", glyph: "⚙" },
  { href: "/activity", label: "Activity", glyph: "≋" },
  { href: "/settings", label: "Settings", glyph: "⚬" },
];

const UNCONFIGURED_FALLBACK = [
  { name: "API", status: "degraded" as const, detail: "unreachable" },
  { name: "Database", status: "degraded" as const, detail: null },
  { name: "Claude", status: "degraded" as const, detail: null },
  { name: "Tool Registry", status: "degraded" as const, detail: null },
];

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  // The sidebar owns the slow poll for system status; pages do not duplicate it.
  const { data } = useResource((signal) => healthApi.overview(signal), [], {
    pollMs: 15_000,
  });

  const counters = data?.counters;

  return (
    <div className="flex h-full flex-col gap-6 px-3 py-5">
      <Link
        href="/"
        onClick={onNavigate}
        className="flex items-center gap-2.5 px-2"
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-md border border-accent-line bg-accent-soft text-xs font-bold text-accent">
          U
        </span>
        <span className="text-sm font-semibold tracking-tight text-ink">
          ULUGBEK AI
        </span>
      </Link>

      <nav className="flex-1 space-y-0.5" aria-label="Main">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          const badge = item.badgeKey ? counters?.[item.badgeKey] : undefined;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cx(
                "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
                active
                  ? "bg-white/[0.06] text-ink"
                  : "text-ink-muted hover:bg-white/[0.03] hover:text-ink",
              )}
            >
              <span
                aria-hidden
                className={cx(
                  "w-4 text-center text-xs",
                  active ? "text-accent" : "text-ink-faint",
                )}
              >
                {item.glyph}
              </span>
              <span className="flex-1">{item.label}</span>
              {badge ? (
                <span
                  className={cx(
                    "rounded-md px-1.5 py-0.5 text-2xs font-semibold",
                    item.badgeKey === "pending_approvals"
                      ? "bg-warn-soft text-warn"
                      : "bg-white/5 text-ink-muted",
                  )}
                >
                  {badge}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>

      <div className="rounded-lg border border-line bg-surface p-3">
        <p className="label-caps mb-2">System status</p>
        <HealthList
          components={data?.components ?? UNCONFIGURED_FALLBACK}
          compact
        />
        {data && (
          <p className="mt-2.5 border-t border-line pt-2 font-mono text-2xs text-ink-faint">
            v{data.version} · {data.environment}
          </p>
        )}
      </div>
    </div>
  );
}
