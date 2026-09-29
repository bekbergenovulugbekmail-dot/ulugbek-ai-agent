/**
 * Speaking to the agent.
 *
 * The properties worth pinning are the ones that would let voice damage the
 * console it is bolted onto: a transcript that sends itself, a missing
 * microphone that takes the keyboard with it, and a failed voice that swallows
 * an answer the operator already has in writing.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const routerMock = { push: vi.fn(), replace: vi.fn() };
const searchParams = { current: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => searchParams.current,
  usePathname: () => "/agent",
}));

vi.stubGlobal("EventSource", undefined);

import { AgentConsole } from "@/app/agent/AgentConsole";
import { VoiceButton } from "@/components/agent/VoiceButton";
import { agentApi, projectApi, toolApi, voiceApi } from "@/lib/api";
import { isRecordingSupported, supportedMimeType } from "@/lib/voice/recorder";
import { isSpeechSupported, speak } from "@/lib/voice/speech";

import { makeState } from "./setup-mocks";

const RUN_ID = "55555555-5555-5555-5555-555555555555";

// --------------------------------------------------------------------------
// A microphone, for a browser that has none
// --------------------------------------------------------------------------
class FakeMediaRecorder {
  static supported = new Set(["audio/webm;codecs=opus", "audio/webm"]);
  static isTypeSupported = (type: string) => FakeMediaRecorder.supported.has(type);

  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  state: "inactive" | "recording" = "inactive";

  constructor(
    public stream: unknown,
    public options?: { mimeType?: string },
  ) {}

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["audio"], { type: "audio/webm" }) });
    this.onstop?.();
  }
}

function stubMicrophone(
  { grant = true }: { grant?: boolean } = {},
): { stop: ReturnType<typeof vi.fn> } {
  const stop = vi.fn();
  const getUserMedia = grant
    ? vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] })
    : vi.fn().mockRejectedValue(
        Object.assign(new Error("denied"), { name: "NotAllowedError" }),
      );
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  return { stop };
}

function stubBaseline() {
  vi.spyOn(projectApi, "list").mockResolvedValue([]);
  vi.spyOn(toolApi, "executions").mockResolvedValue([]);
  vi.spyOn(agentApi, "runEvents").mockResolvedValue({
    events: [],
    cursor: null,
    has_more: false,
  });
  vi.spyOn(agentApi, "state").mockResolvedValue(makeState());
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.stubGlobal("EventSource", undefined);
  routerMock.replace.mockReset();
  searchParams.current = new URLSearchParams();
  stubBaseline();
});

// --------------------------------------------------------------------------
// Picking a container the backend can actually read
// --------------------------------------------------------------------------
describe("the recorder", () => {
  it("prefers the container the transcription provider accepts", () => {
    stubMicrophone();

    expect(supportedMimeType()).toBe("audio/webm;codecs=opus");
    expect(isRecordingSupported()).toBe(true);
  });

  it("takes Safari's container rather than refusing it", () => {
    // The speech service converts with ffmpeg before the model sees anything,
    // so audio/mp4 is no longer the dead end it was under a cloud API.
    stubMicrophone();
    FakeMediaRecorder.supported = new Set(["audio/mp4"]);

    expect(supportedMimeType()).toBe("audio/mp4");
    expect(isRecordingSupported()).toBe(true);

    FakeMediaRecorder.supported = new Set(["audio/webm;codecs=opus", "audio/webm"]);
  });

  it("reports no microphone rather than recording something unusable", () => {
    // A container nothing in the chain can decode: no button, rather than one
    // that records perfectly and fails on every upload.
    stubMicrophone();
    FakeMediaRecorder.supported = new Set(["audio/amr-wb", "video/x-matroska"]);

    expect(supportedMimeType()).toBeNull();
    expect(isRecordingSupported()).toBe(false);

    FakeMediaRecorder.supported = new Set(["audio/webm;codecs=opus", "audio/webm"]);
  });

  it("reports no microphone when the browser has no MediaRecorder at all", () => {
    vi.stubGlobal("MediaRecorder", undefined);

    expect(isRecordingSupported()).toBe(false);
  });
});

// --------------------------------------------------------------------------
// The button
// --------------------------------------------------------------------------
describe("the voice button", () => {
  it("is absent when the browser cannot record", () => {
    vi.stubGlobal("MediaRecorder", undefined);
    render(<VoiceButton onTranscript={vi.fn()} />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("records, transcribes, and hands the text back", async () => {
    stubMicrophone();
    const transcribe = vi
      .spyOn(voiceApi, "transcribe")
      .mockResolvedValue({ text: "loyihalarimni ko'rsat", language: "uz", duration_seconds: 2 });
    const onTranscript = vi.fn();
    const user = userEvent.setup();

    render(<VoiceButton onTranscript={onTranscript} />);

    await user.click(screen.getByRole("button", { name: /ovozli buyruq/i }));
    expect(
      await screen.findByRole("button", { name: /to'xtatish/i }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /to'xtatish/i }));

    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith("loyihalarimni ko'rsat"));
    expect(transcribe).toHaveBeenCalledTimes(1);
  });

  it("releases the microphone when the recording stops", async () => {
    const { stop } = stubMicrophone();
    vi.spyOn(voiceApi, "transcribe").mockResolvedValue({
      text: "salom",
      language: "uz",
      duration_seconds: 1,
    });
    const user = userEvent.setup();

    render(<VoiceButton onTranscript={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /ovozli buyruq/i }));
    await user.click(screen.getByRole("button", { name: /to'xtatish/i }));

    // A tab that keeps the recording indicator lit after one command is a tab
    // the operator stops trusting with a microphone.
    await waitFor(() => expect(stop).toHaveBeenCalled());
  });

  it("says plainly, in Uzbek, when the microphone is refused", async () => {
    stubMicrophone({ grant: false });
    const onTranscript = vi.fn();
    const user = userEvent.setup();

    render(<VoiceButton onTranscript={onTranscript} />);
    await user.click(screen.getByRole("button", { name: /ovozli buyruq/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/mikrofon/i);
    expect(alert).toHaveTextContent(/ruxsat/i);
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("keeps the console usable when transcription fails", async () => {
    stubMicrophone();
    vi.spyOn(voiceApi, "transcribe").mockRejectedValue(new Error("no"));
    const onTranscript = vi.fn();
    const user = userEvent.setup();

    render(<VoiceButton onTranscript={onTranscript} />);
    await user.click(screen.getByRole("button", { name: /ovozli buyruq/i }));
    await user.click(screen.getByRole("button", { name: /to'xtatish/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(onTranscript).not.toHaveBeenCalled();
    // Back to idle: one failure must not cost the operator the microphone.
    expect(
      await screen.findByRole("button", { name: /ovozli buyruq/i }),
    ).toBeEnabled();
  });
});

// --------------------------------------------------------------------------
// The transcript goes to the operator, not to the agent
// --------------------------------------------------------------------------
describe("the transcript", () => {
  it("lands in the command box and is not sent", async () => {
    stubMicrophone();
    vi.spyOn(voiceApi, "transcribe").mockResolvedValue({
      text: "loyihalarimni ko'rsat",
      language: "uz",
      duration_seconds: 2,
    });
    const start = vi.spyOn(agentApi, "start");
    const user = userEvent.setup();

    render(<AgentConsole />);
    await user.click(screen.getByRole("button", { name: /ovozli buyruq/i }));
    await user.click(screen.getByRole("button", { name: /to'xtatish/i }));

    const box = await screen.findByLabelText("Command for the agent");
    await waitFor(() =>
      expect(box).toHaveValue("loyihalarimni ko'rsat"),
    );
    // Uzbek transcription is imperfect, and an agent that acts on a sentence
    // nobody said is worse than one that waits. The operator presses Send.
    expect(start).not.toHaveBeenCalled();
  });

  it("leaves the keyboard alone when the browser cannot record", async () => {
    vi.stubGlobal("MediaRecorder", undefined);
    const user = userEvent.setup();

    render(<AgentConsole />);
    const box = await screen.findByLabelText("Command for the agent");
    await user.type(box, "qo'lda yozilgan buyruq");

    expect(box).toHaveValue("qo'lda yozilgan buyruq");
    expect(
      screen.queryByRole("button", { name: /ovozli buyruq/i }),
    ).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Speaking the answer
// --------------------------------------------------------------------------
describe("the spoken answer", () => {
  function stubSynthesis(
    { throws = false }: { throws?: boolean } = {},
  ): { speak: ReturnType<typeof vi.fn> } {
    const speakFn = vi.fn((utterance: { onend?: () => void }) => {
      if (throws) throw new Error("no voice");
      utterance.onend?.();
    });
    vi.stubGlobal("speechSynthesis", {
      speak: speakFn,
      cancel: vi.fn(),
      getVoices: () => [],
    });
    vi.stubGlobal(
      "SpeechSynthesisUtterance",
      class {
        onend: (() => void) | null = null;
        onerror: (() => void) | null = null;
        lang = "";
        constructor(public text: string) {}
      },
    );
    return { speak: speakFn };
  }

  it("is available when the browser can speak", () => {
    stubSynthesis();
    expect(isSpeechSupported()).toBe(true);
  });

  it("is simply unavailable when it cannot", () => {
    vi.stubGlobal("speechSynthesis", undefined);
    expect(isSpeechSupported()).toBe(false);
  });

  it("resolves rather than throwing when synthesis fails", async () => {
    stubSynthesis({ throws: true });

    // A voice that throws must not become an unhandled rejection in a console
    // whose written answer is already on screen.
    await expect(speak("salom")).resolves.toBeUndefined();
  });

  it("offers to read a finished answer aloud", async () => {
    stubMicrophone();
    stubSynthesis();
    searchParams.current = new URLSearchParams(`run=${RUN_ID}`);
    vi.spyOn(agentApi, "getRun").mockResolvedValue({
      id: RUN_ID,
      input: "salom",
      output: "Sizda 3 ta loyiha bor.",
      error: null,
      status: "COMPLETED",
      created_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      steps: [],
    } as never);

    render(<AgentConsole />);

    expect(
      await screen.findByRole("button", { name: /o'qib berish/i }),
    ).toBeInTheDocument();
  });

  it("does not offer to read a run that is waiting for approval", async () => {
    stubMicrophone();
    stubSynthesis();
    searchParams.current = new URLSearchParams(`run=${RUN_ID}`);
    vi.spyOn(agentApi, "state").mockResolvedValue(
      makeState({ run_status: "WAITING_APPROVAL", busy: true }),
    );
    vi.spyOn(agentApi, "getRun").mockResolvedValue({
      id: RUN_ID,
      input: "deploy qil",
      output: null,
      error: null,
      status: "WAITING_APPROVAL",
      created_at: new Date().toISOString(),
      finished_at: null,
      steps: [],
    } as never);

    render(<AgentConsole />);
    await screen.findByText("deploy qil");

    // Nothing has been answered yet: reading out a half-finished run would
    // announce a decision the operator has not made.
    expect(
      screen.queryByRole("button", { name: /o'qib berish/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps the written answer when the voice fails", async () => {
    stubMicrophone();
    stubSynthesis({ throws: true });
    searchParams.current = new URLSearchParams(`run=${RUN_ID}`);
    vi.spyOn(agentApi, "getRun").mockResolvedValue({
      id: RUN_ID,
      input: "salom",
      output: "Sizda 3 ta loyiha bor.",
      error: null,
      status: "COMPLETED",
      created_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      steps: [],
    } as never);
    const user = userEvent.setup();

    render(<AgentConsole />);
    await user.click(await screen.findByRole("button", { name: /o'qib berish/i }));

    expect(screen.getByText("Sizda 3 ta loyiha bor.")).toBeInTheDocument();
  });
});
