"use client";

/**
 * Recording one spoken command.
 *
 * The hard part is not recording — it is recording something the backend's
 * transcription provider can actually read. `MediaRecorder` produces whatever
 * the browser prefers: Opus in a WebM container on Chrome, Firefox and Edge,
 * AAC in MP4 on Safari. Google's speech API reads the first and not the
 * second, so the container is chosen here and declared to the server rather
 * than discovered by it.
 *
 * When nothing suitable is available the microphone is reported as absent.
 * Offering a button that records perfectly and then fails on every upload is
 * worse than offering no button at all.
 */

/** In order of preference. Every entry is a container the backend accepts. */
export const PREFERRED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
] as const;

export interface Clip {
  blob: Blob;
  /** The full type recorded, parameters included, for the `Content-Type`. */
  mimeType: string;
  durationSeconds: number;
}

export interface Recording {
  /** Finish and hand back the audio. */
  stop: () => Promise<Clip>;
  /** Abandon it: no clip, and the microphone is released either way. */
  cancel: () => void;
}

type RecorderConstructor = {
  new (stream: MediaStream, options?: { mimeType?: string }): MediaRecorder;
  isTypeSupported?: (type: string) => boolean;
};

function recorderClass(): RecorderConstructor | undefined {
  return (globalThis as { MediaRecorder?: RecorderConstructor }).MediaRecorder;
}

/** The best container this browser can record that the backend can read. */
export function supportedMimeType(): string | null {
  const Recorder = recorderClass();
  if (!Recorder?.isTypeSupported) return null;
  return (
    PREFERRED_MIME_TYPES.find((type) => Recorder.isTypeSupported!(type)) ?? null
  );
}

/** Whether to offer the microphone at all. */
export function isRecordingSupported(): boolean {
  if (typeof navigator === "undefined") return false;
  if (!navigator.mediaDevices?.getUserMedia) return false;
  return supportedMimeType() !== null;
}

/** Raised when the operator (or the platform) refuses the microphone. */
export class MicrophoneDeniedError extends Error {
  constructor() {
    super("microphone denied");
    this.name = "MicrophoneDeniedError";
  }
}

/**
 * Open the microphone and start recording.
 *
 * @throws MicrophoneDeniedError when permission is refused.
 */
export async function startRecording(
  { maxSeconds }: { maxSeconds?: number } = {},
): Promise<Recording> {
  const Recorder = recorderClass();
  const mimeType = supportedMimeType();
  if (!Recorder || !mimeType) throw new Error("recording is not supported");

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (caught) {
    const name = (caught as { name?: string })?.name;
    if (name === "NotAllowedError" || name === "SecurityError") {
      throw new MicrophoneDeniedError();
    }
    throw caught;
  }

  const recorder = new Recorder(stream, { mimeType });
  const chunks: Blob[] = [];
  const startedAt = Date.now();
  let cap: ReturnType<typeof setTimeout> | undefined;

  const release = () => {
    if (cap) clearTimeout(cap);
    // Every track, explicitly: the browser keeps the recording indicator lit
    // until the last one is stopped, and a console that leaves it on is a
    // console nobody grants a microphone to twice.
    stream.getTracks().forEach((track) => track.stop());
  };

  recorder.ondataavailable = (event: BlobEvent) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };
  recorder.start();

  const stop = () =>
    new Promise<Clip>((resolve, reject) => {
      const finish = () => {
        release();
        resolve({
          blob: new Blob(chunks, { type: mimeType }),
          mimeType,
          durationSeconds: (Date.now() - startedAt) / 1000,
        });
      };
      recorder.onerror = () => {
        release();
        reject(new Error("the recording failed"));
      };
      recorder.onstop = finish;
      if (recorder.state === "inactive") finish();
      else recorder.stop();
    });

  if (maxSeconds && maxSeconds > 0) {
    // The server refuses anything longer anyway; stopping here turns a refused
    // upload into a finished recording.
    cap = setTimeout(() => {
      if (recorder.state !== "inactive") recorder.stop();
    }, maxSeconds * 1000);
  }

  return {
    stop,
    cancel: () => {
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
      release();
    },
  };
}
