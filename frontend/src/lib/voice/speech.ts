"use client";

/**
 * Reading an answer aloud, in the browser.
 *
 * `SpeechSynthesis` is free, needs no key, sends no audio anywhere and works
 * offline — which is why the backend's `TTS_PROVIDER` defaults to `browser`
 * and `/api/voice/speak` produces nothing. The trade is voice quality and
 * coverage: many machines have no Uzbek voice at all, and will read Uzbek Latin
 * text with whatever voice they do have. That is a real limitation, not a bug,
 * and the button simply does not appear where synthesis is unavailable.
 *
 * Swapping in a server voice later means changing `speak()` to call
 * `/api/voice/speak` and play the bytes. Nothing else in the console knows how
 * the sound is made.
 */

type Synthesis = {
  speak: (utterance: SpeechSynthesisUtterance) => void;
  cancel: () => void;
};

function synthesis(): Synthesis | undefined {
  return (globalThis as { speechSynthesis?: Synthesis }).speechSynthesis;
}

function utteranceClass():
  | (new (text: string) => SpeechSynthesisUtterance)
  | undefined {
  return (
    globalThis as {
      SpeechSynthesisUtterance?: new (text: string) => SpeechSynthesisUtterance;
    }
  ).SpeechSynthesisUtterance;
}

/** Whether this browser can speak at all. */
export function isSpeechSupported(): boolean {
  return Boolean(synthesis() && utteranceClass());
}

/**
 * Read `text` aloud. Resolves when it finishes — or immediately, if it cannot.
 *
 * Never rejects. The written answer is already on screen by the time this is
 * called, so a browser that cannot speak is a missing convenience, not an
 * error worth showing anyone.
 */
export function speak(
  text: string,
  { lang = "uz-UZ" }: { lang?: string } = {},
): Promise<void> {
  const engine = synthesis();
  const Utterance = utteranceClass();
  if (!engine || !Utterance || !text.trim()) return Promise.resolve();

  return new Promise<void>((resolve) => {
    try {
      // Anything still being read belongs to a previous answer.
      engine.cancel();
      const utterance = new Utterance(text);
      utterance.lang = lang;
      utterance.onend = () => resolve();
      utterance.onerror = () => resolve();
      engine.speak(utterance);
    } catch {
      resolve();
    }
  });
}

/** Stop whatever is being read. Safe when nothing is. */
export function cancelSpeech(): void {
  try {
    synthesis()?.cancel();
  } catch {
    /* a browser that cannot speak cannot be interrupted either */
  }
}
