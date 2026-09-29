"use client";

/**
 * The operator's credential, in the browser.
 *
 * This deployment has one operator and one shared token, so there is no login
 * form in the usual sense: the operator pastes the same secret the server was
 * configured with, and the console holds it.
 *
 * **Where it is kept, and why.** `localStorage`, deliberately, over the two
 * alternatives:
 *
 * * `sessionStorage` would force the token to be re-entered in every new tab,
 *   for a console meant to stay open — and it is just as readable by injected
 *   script, so the ergonomic cost buys very little.
 * * Memory only would mean re-entering it on every reload.
 *
 * What actually contains the risk is elsewhere: this app renders no raw HTML
 * (there is no `dangerouslySetInnerHTML` anywhere in it) and loads no
 * third-party script, so there is no obvious way for another party's code to
 * read it; and the token is one environment variable, so rotating it
 * invalidates every stored copy at once.
 */

import { setAuthTokenProvider, setUnauthorizedHandler } from "@/lib/api";

const STORAGE_KEY = "ulugbek-ai.operator-token";

type Listener = (token: string | null) => void;

let cached: string | null = null;
let loaded = false;
const listeners = new Set<Listener>();

function read(): string | null {
  if (loaded) return cached;
  loaded = true;
  try {
    cached = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private windows and blocked site data both throw. The console still
    // works; the token simply lasts as long as the page does.
    cached = null;
  }
  return cached;
}

/** The token every request is sent with, or `null` when there is none. */
export function operatorToken(): string | null {
  if (typeof window === "undefined") return null;
  return read();
}

export function setOperatorToken(token: string | null): void {
  cached = token;
  loaded = true;
  try {
    if (token) window.localStorage.setItem(STORAGE_KEY, token);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* held in memory for this page instead */
  }
  listeners.forEach((listener) => listener(token));
}

/** Called when the server rejects the token, so the UI can ask again. */
export function onTokenChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Point the API client at this store. Idempotent, and deliberately without a
 * "already wired" short-circuit: registering the same two functions again is a
 * no-op, while skipping the call leaves whatever was registered last in place.
 * A guard here means the second caller silently gets someone else's provider.
 */
export function installOperatorToken(): void {
  setAuthTokenProvider(operatorToken);
  // A token the server no longer accepts is worse than none: every panel
  // fails separately and none of them names the cause. Drop it and ask again.
  setUnauthorizedHandler(() => setOperatorToken(null));
}
