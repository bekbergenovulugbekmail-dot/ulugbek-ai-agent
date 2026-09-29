"use client";

/**
 * The microphone.
 *
 * It hands the transcript to its parent and stops there. It never sends: Uzbek
 * transcription is imperfect, and an agent that acts on a sentence nobody said
 * is worse than one that waits for a glance.
 *
 * The button is absent — not disabled, not hidden behind an error — when the
 * browser cannot record something the backend can read. Safari records MP4,
 * which the transcription provider cannot decode, and a button that records
 * perfectly and then fails on every upload teaches the operator to distrust
 * the whole console.
 *
 * Its messages are in Uzbek because they are addressed to whoever is holding
 * the microphone, and this console has one operator who speaks it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, voiceApi } from "@/lib/api";
import { cx } from "@/lib/format";
import {
  MicrophoneDeniedError,
  isRecordingSupported,
  startRecording,
  type Recording,
} from "@/lib/voice/recorder";

export type VoiceState = "idle" | "recording" | "transcribing";

/** Longer than this and the server refuses the upload anyway. */
const MAX_SECONDS = 60;

export function VoiceButton({
  onTranscript,
  disabled = false,
}: {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}) {
  const [supported, setSupported] = useState(false);
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string | undefined>();
  const recording = useRef<Recording | null>(null);

  // Read after mount: on the server there is no navigator, and rendering the
  // button and then removing it would flash a control that never worked.
  useEffect(() => {
    setSupported(isRecordingSupported());
    return () => recording.current?.cancel();
  }, []);

  const begin = useCallback(async () => {
    setError(undefined);
    try {
      recording.current = await startRecording({ maxSeconds: MAX_SECONDS });
      setState("recording");
    } catch (caught) {
      recording.current = null;
      setState("idle");
      setError(
        caught instanceof MicrophoneDeniedError
          ? "Mikrofonga ruxsat berilmadi. Brauzer sozlamalaridan ruxsat bering yoki buyruqni yozib yuboring."
          : "Mikrofonni ochib bo'lmadi. Buyruqni yozib yuborishingiz mumkin.",
      );
    }
  }, []);

  const finish = useCallback(async () => {
    const current = recording.current;
    if (!current) return;
    recording.current = null;
    setState("transcribing");
    try {
      const clip = await current.stop();
      const transcript = await voiceApi.transcribe(clip);
      const text = transcript.text.trim();
      if (!text) {
        setError("Hech narsa eshitilmadi. Yana bir marta urinib ko'ring.");
      } else {
        onTranscript(text);
      }
    } catch (caught) {
      // The API error's message is the backend's own — it names the container
      // it cannot read, or the limit that was exceeded, and saying that beats
      // a generic failure.
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Ovozni matnga o'girib bo'lmadi. Buyruqni yozib yuborishingiz mumkin.",
      );
    } finally {
      setState("idle");
    }
  }, [onTranscript]);

  if (!supported) return null;

  const busy = state === "transcribing";
  const label =
    state === "recording"
      ? "To'xtatish"
      : busy
        ? "Matnga o'girilmoqda"
        : "Ovozli buyruq";

  return (
    <>
      <button
        type="button"
        aria-label={label}
        aria-pressed={state === "recording"}
        disabled={disabled || busy}
        onClick={() => void (state === "recording" ? finish() : begin())}
        className={cx(
          "flex h-8 w-8 items-center justify-center rounded-lg border text-sm transition-colors",
          state === "recording"
            ? "border-danger/40 bg-danger-soft text-danger"
            : "border-line bg-elevated text-ink-muted hover:text-ink",
          (disabled || busy) && "opacity-60",
        )}
        title={label}
      >
        <span aria-hidden>
          {state === "recording" ? "■" : busy ? "…" : "🎤"}
        </span>
      </button>
      {state === "recording" && (
        <span className="text-2xs text-danger" role="status">
          Yozilmoqda…
        </span>
      )}
      {error && (
        <p role="alert" className="w-full text-2xs leading-relaxed text-danger">
          {error}
        </p>
      )}
    </>
  );
}
