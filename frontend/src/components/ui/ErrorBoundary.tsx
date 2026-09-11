"use client";

import React from "react";

import { Button } from "./Button";

interface State {
  error: Error | undefined;
}

/**
 * Stops one broken panel from blanking the whole console.
 *
 * The message shown is generic on purpose: details go to the browser console,
 * not to the screen.
 */
export class ErrorBoundary extends React.Component<
  { children: React.ReactNode; label?: string },
  State
> {
  state: State = { error: undefined };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ulugbek-ai] render error", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          role="alert"
          className="panel flex flex-col items-start gap-2 px-5 py-4"
        >
          <p className="text-sm font-medium text-ink">
            {this.props.label ?? "This panel"} could not be displayed.
          </p>
          <p className="text-xs text-ink-faint">
            The rest of the console is unaffected.
          </p>
          <Button
            size="sm"
            onClick={() => this.setState({ error: undefined })}
            className="mt-1"
          >
            Try again
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
