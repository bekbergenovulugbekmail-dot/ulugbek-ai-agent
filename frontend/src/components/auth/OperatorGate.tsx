"use client";

/**
 * The door.
 *
 * Nothing in this console is useful without the operator token, and every
 * request fails the same way without it, so the whole app sits behind this
 * rather than letting nine pages each discover the problem separately.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  installOperatorToken,
  onTokenChange,
  operatorToken,
  setOperatorToken,
} from "@/lib/auth";

export function OperatorGate({ children }: { children: React.ReactNode }) {
  // `undefined` until the browser has been read: rendering the form before
  // then flashes the door in front of an operator who is already signed in.
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [entered, setEntered] = useState("");
  const [rejected, setRejected] = useState(false);

  useEffect(() => {
    installOperatorToken();
    setToken(operatorToken());
    return onTokenChange((next) => {
      setToken(next);
      // Cleared by a 401 rather than by the operator signing out.
      if (next === null) setRejected(true);
    });
  }, []);

  const submit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      const value = entered.trim();
      if (!value) return;
      setRejected(false);
      setOperatorToken(value);
      setEntered("");
    },
    [entered],
  );

  if (token === undefined) return null;
  if (token) return <>{children}</>;

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form
        onSubmit={submit}
        className="panel w-full max-w-sm space-y-4 px-6 py-7"
        aria-labelledby="gate-title"
      >
        <div className="space-y-1.5">
          <h1 id="gate-title" className="text-base font-semibold text-ink">
            ULUGBEK AI
          </h1>
          <p className="text-xs leading-relaxed text-ink-faint">
            This console is for one operator. Paste the token the backend was
            configured with.
          </p>
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor="operator-token"
            className="block text-2xs font-semibold uppercase tracking-wider text-ink-faint"
          >
            Operator token
          </label>
          <input
            id="operator-token"
            // `password`, so it is not shoulder-read and not offered to a
            // password manager as a username.
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={entered}
            onChange={(event) => setEntered(event.target.value)}
            className="w-full rounded-lg border border-line bg-elevated px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent"
          />
        </div>

        {rejected && (
          <p role="alert" className="text-xs text-danger">
            That token was refused. Check AUTH_TOKEN on the backend — it may
            have been rotated.
          </p>
        )}

        <Button type="submit" variant="primary" className="w-full">
          Continue
        </Button>
      </form>
    </main>
  );
}
