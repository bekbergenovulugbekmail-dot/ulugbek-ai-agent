"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { cx } from "@/lib/format";

import { Sidebar } from "./Sidebar";

/**
 * The console frame: a fixed rail on desktop, a drawer on mobile.
 *
 * Desktop is the primary target, so the rail is always visible there and never
 * collapses; below `lg` it becomes an overlay that closes on navigation.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => setDrawerOpen(false), [pathname]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="app-backdrop min-h-screen">
      {/* Desktop rail */}
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r border-line bg-surface/60 backdrop-blur lg:block">
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      <div
        className={cx(
          "fixed inset-0 z-40 lg:hidden",
          drawerOpen ? "pointer-events-auto" : "pointer-events-none",
        )}
      >
        <div
          className={cx(
            "absolute inset-0 bg-black/60 transition-opacity duration-200",
            drawerOpen ? "opacity-100" : "opacity-0",
          )}
          onClick={() => setDrawerOpen(false)}
          aria-hidden
        />
        <aside
          className={cx(
            "absolute inset-y-0 left-0 w-64 border-r border-line bg-surface transition-transform duration-200",
            drawerOpen ? "translate-x-0" : "-translate-x-full",
          )}
          aria-hidden={!drawerOpen}
        >
          <Sidebar onNavigate={() => setDrawerOpen(false)} />
        </aside>
      </div>

      <div className="lg:pl-60">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-canvas/80 px-4 backdrop-blur lg:hidden">
          <button
            onClick={() => setDrawerOpen(true)}
            aria-label="Open navigation"
            aria-expanded={drawerOpen}
            className="rounded-lg border border-line px-2.5 py-1.5 text-sm text-ink-muted"
          >
            ☰
          </button>
          <span className="text-sm font-semibold">ULUGBEK AI</span>
        </header>

        <main className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
