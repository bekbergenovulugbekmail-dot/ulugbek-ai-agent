"use client";

import { useEffect, useRef, useState } from "react";

import type { Project } from "@/lib/api";
import { cx } from "@/lib/format";

import { Button } from "../ui/Button";

/**
 * The universal command box.
 *
 * Enter sends, Shift+Enter adds a line, and the textarea grows with the text —
 * the operator should be able to write a paragraph without leaving the field.
 */
export function CommandInput({
  onSubmit,
  busy = false,
  projects = [],
  projectId,
  onProjectChange,
  placeholder = "Ask Ulugbek AI anything…",
  autoFocus = false,
  value: controlledValue,
  onValueChange,
  accessory,
}: {
  onSubmit: (message: string) => void;
  busy?: boolean;
  projects?: Project[];
  projectId?: string | null;
  onProjectChange?: (id: string | null) => void;
  placeholder?: string;
  autoFocus?: boolean;
  /** Supply this (with `onValueChange`) to drive the box from outside. */
  value?: string;
  onValueChange?: (value: string) => void;
  /** Rendered in the toolbar row — the microphone lives here. */
  accessory?: React.ReactNode;
}) {
  // Uncontrolled by default, so the dashboard's copy is unchanged. Controlled
  // when a parent needs to write into it, which is what a transcript does.
  const [ownValue, setOwnValue] = useState("");
  const controlled = controlledValue !== undefined;
  const value = controlled ? controlledValue : ownValue;
  const setValue = (next: string) => {
    if (!controlled) setOwnValue(next);
    onValueChange?.(next);
  };
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 200)}px`;
  }, [value]);

  const submit = () => {
    const message = value.trim();
    if (!message || busy) return;
    onSubmit(message);
    setValue("");
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className={cx(
        "rounded-xl border bg-surface transition-colors",
        busy ? "border-accent-line" : "border-line focus-within:border-line-strong",
      )}
    >
      <label htmlFor="command-input" className="sr-only">
        Command for the agent
      </label>
      <textarea
        id="command-input"
        ref={textareaRef}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        disabled={busy}
        placeholder={placeholder}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        className="w-full resize-none bg-transparent px-4 pt-3.5 text-sm text-ink placeholder:text-ink-faint focus:outline-none disabled:opacity-60"
      />

      <div className="flex flex-wrap items-center gap-2 px-3 pb-3 pt-1.5">
        {accessory}
        {projects.length > 0 && onProjectChange && (
          <>
            <label htmlFor="command-project" className="sr-only">
              Project
            </label>
            <select
              id="command-project"
              value={projectId ?? ""}
              onChange={(event) =>
                onProjectChange(event.target.value || null)
              }
              disabled={busy}
              className="rounded-lg border border-line bg-elevated px-2 py-1 text-xs text-ink-muted"
            >
              <option value="">Auto-route project</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </>
        )}

        <span className="hidden text-2xs text-ink-faint sm:inline">
          Enter to send · Shift+Enter for a new line
        </span>

        <Button
          type="submit"
          variant="primary"
          size="sm"
          className="ml-auto"
          loading={busy}
          disabled={!value.trim() || busy}
        >
          {busy ? "Working" : "Send"}
        </Button>
      </div>
    </form>
  );
}
