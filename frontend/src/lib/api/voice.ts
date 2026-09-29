/**
 * Speech, through the backend.
 *
 * The browser never holds a transcription key and never talks to a provider:
 * it posts the recording to this service, which holds `STT_API_KEY` and makes
 * the call. Every `NEXT_PUBLIC_*` value is compiled into the bundle and is
 * therefore public, so there is no version of this where the key lives here.
 */

import type { Clip } from "@/lib/voice/recorder";

import { request } from "./client";

export interface TranscriptResponse {
  text: string;
  language: string;
  duration_seconds: number | null;
}

export const voiceApi = {
  /** Transcribe one recording. The container is declared, not guessed. */
  transcribe(clip: Clip, signal?: AbortSignal): Promise<TranscriptResponse> {
    return request<TranscriptResponse>("/voice/transcribe", {
      method: "POST",
      rawBody: clip.blob,
      contentType: clip.mimeType,
      // Lets the server refuse an over-long recording before it pays a
      // provider for it. The server does not trust this for its real limit —
      // the byte count is what it enforces.
      headers: {
        "X-Audio-Duration-Seconds": clip.durationSeconds.toFixed(2),
      },
      signal,
      timeoutMs: 60_000,
    });
  },
};
