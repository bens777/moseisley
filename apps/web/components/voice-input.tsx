"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, getToken } from "@/lib/api";
import { useApi } from "@/lib/hooks";

/* Voice input for the chat composers.

   Two transcription paths, chosen by what this user actually has:
     · backend  — POST the recording to /api/audio/transcribe (Whisper via the
       user's own OpenAI/custom provider). Preferred: better accuracy, and the
       audio is discarded server-side.
     · browser  — the Web Speech API, live, when the backend path is not
       available to this user but the browser can do it locally.
   If neither is available the button says so and never records: capturing audio
   nobody can transcribe would just waste the user's breath.

   The transcript always lands in the input for the user to edit. Nothing here
   ever sends a message. */

const MAX_SECONDS = 120;

type Availability = { available: boolean; reason: string | null; detail: string };

type Mode = "backend" | "browser" | "none";
type State = "idle" | "recording" | "transcribing";

/* The Web Speech API is still vendor-prefixed in the browsers that have it. */
type SpeechRecognitionLike = {
  lang: string; continuous: boolean; interimResults: boolean;
  start: () => void; stop: () => void; abort: () => void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  onend: (() => void) | null;
};

function speechRecognition(): SpeechRecognitionLike | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

function canRecord(): boolean {
  return typeof window !== "undefined"
    && typeof navigator !== "undefined"
    && !!navigator.mediaDevices?.getUserMedia
    && typeof window.MediaRecorder !== "undefined";
}

const NO_PATH_TOOLTIP =
  "Voice input needs an OpenAI key (Expert mode) or a supported browser.";

export function VoiceInput({ onTranscript, disabled }: {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}) {
  const availability = useApi<Availability>("/audio/availability");
  const [state, setState] = useState<State>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [tip, setTip] = useState(false);

  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stream = useRef<MediaStream | null>(null);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const cancelled = useRef(false);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);

  const backendReady = availability.data?.available === true;
  const browserReady = typeof window !== "undefined" && !!speechRecognition();
  const mode: Mode = backendReady ? "backend" : browserReady ? "browser" : "none";

  const releaseMic = useCallback(() => {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    if (ticker.current) { clearInterval(ticker.current); ticker.current = null; }
    setSeconds(0);
  }, []);

  useEffect(() => () => {
    // unmounting mid-recording must not leave the mic light on
    cancelled.current = true;
    try { recorder.current?.stop(); } catch { /* already stopped */ }
    try { recognition.current?.abort(); } catch { /* not started */ }
    releaseMic();
  }, [releaseMic]);

  const startTimer = useCallback(() => {
    setSeconds(0);
    ticker.current = setInterval(() => {
      setSeconds((s) => {
        if (s + 1 >= MAX_SECONDS) stopRecording();
        return s + 1;
      });
    }, 1000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function sendForTranscription(blob: Blob) {
    setState("transcribing");
    try {
      const form = new FormData();
      form.append("file", blob, "voice.webm");
      const resp = await fetch(`${API_URL}/api/audio/transcribe`, {
        method: "POST",
        headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
        body: form,
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body?.detail;
        throw new Error(typeof detail === "object" && detail?.message
          ? detail.message
          : "Transcription failed. Try again, or type it instead.");
      }
      const { text } = await resp.json();
      if (text) onTranscript(text);
      else setError("Nothing was picked up — try again closer to the mic.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transcription failed.");
    } finally {
      setState("idle");
    }
  }

  async function startBackendRecording() {
    if (!canRecord()) {
      setError("This browser cannot record audio.");
      return;
    }
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone blocked. Allow mic access for this site in your browser settings.");
      return;
    }
    cancelled.current = false;
    chunks.current = [];
    const rec = new MediaRecorder(stream.current);
    recorder.current = rec;
    rec.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
    rec.onstop = () => {
      releaseMic();
      const blob = new Blob(chunks.current, { type: rec.mimeType || "audio/webm" });
      chunks.current = [];
      if (cancelled.current || blob.size < 1000) { setState("idle"); return; }
      sendForTranscription(blob);
    };
    rec.start();
    setState("recording");
    startTimer();
  }

  function startBrowserRecording() {
    const rec = speechRecognition();
    if (!rec) { setError(NO_PATH_TOOLTIP); return; }
    recognition.current = rec;
    cancelled.current = false;
    rec.lang = navigator.language || "en-US";
    rec.continuous = true;
    rec.interimResults = false;
    let heard = "";
    rec.onresult = (e) => {
      for (let i = 0; i < e.results.length; i++) {
        const alt = e.results[i]?.[0];
        if (alt?.transcript) heard = `${heard} ${alt.transcript}`.trim();
      }
    };
    rec.onerror = (e) => {
      setError(e?.error === "not-allowed"
        ? "Microphone blocked. Allow mic access for this site in your browser settings."
        : "Could not hear anything — try again.");
      setState("idle");
      releaseMic();
    };
    rec.onend = () => {
      releaseMic();
      setState("idle");
      if (!cancelled.current && heard) onTranscript(heard);
    };
    try {
      rec.start();
      setState("recording");
      startTimer();
    } catch {
      setError("Could not start listening.");
    }
  }

  function stopRecording() {
    if (ticker.current) { clearInterval(ticker.current); ticker.current = null; }
    try { recorder.current?.state === "recording" && recorder.current.stop(); } catch { /* noop */ }
    try { recognition.current?.stop(); } catch { /* noop */ }
  }

  function cancel() {
    cancelled.current = true;
    stopRecording();
    releaseMic();
    setState("idle");
  }

  useEffect(() => {
    if (state !== "recording") return;
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") cancel(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  function toggle() {
    setError(null);
    if (state === "recording") { stopRecording(); return; }
    if (state === "transcribing") return;
    if (mode === "backend") startBackendRecording();
    else if (mode === "browser") startBrowserRecording();
  }

  const unavailable = mode === "none";
  const tooltip = unavailable
    ? NO_PATH_TOOLTIP
    : mode === "browser"
      ? "Voice input, transcribed by your browser. Nothing is uploaded."
      : "Voice input. Audio is transcribed and then discarded — nothing is stored.";

  const label = state === "recording" ? "Stop recording"
    : state === "transcribing" ? "Transcribing"
    : unavailable ? NO_PATH_TOOLTIP : "Start voice input";

  return (
    <div className="relative flex shrink-0 items-center gap-2">
      {state === "recording" && (
        <span className="flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-crit"
              aria-live="polite">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-crit" aria-hidden />
          {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
          <span className="hidden text-ink-faint sm:inline">esc to cancel</span>
        </span>
      )}

      {(error || tip) && (
        <div role={error ? "alert" : "tooltip"}
             className={`absolute bottom-full right-0 z-30 mb-2 w-64 rounded-md border px-2.5 py-1.5 text-xs leading-snug shadow-lg shadow-black/40 ${
               error ? "border-crit/50 bg-crit/10 text-crit"
                     : "border-line-strong bg-raised text-ink-mute"}`}>
          {error || tooltip}
        </div>
      )}

      <button
        type="button"
        onClick={toggle}
        disabled={disabled || unavailable || state === "transcribing"}
        aria-label={label}
        aria-pressed={state === "recording"}
        title={label}
        onMouseEnter={() => setTip(true)}
        onMouseLeave={() => setTip(false)}
        onFocus={() => setTip(true)}
        onBlur={() => setTip(false)}
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md border transition ${
          state === "recording"
            ? "animate-pulse border-crit bg-crit/15 text-crit"
            : unavailable
              ? "cursor-not-allowed border-line text-ink-faint opacity-60"
              : "border-line-strong text-ink-mute hover:border-brand/60 hover:text-ink"
        }`}
      >
        {state === "transcribing" ? (
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-faint border-t-brand"
                aria-hidden />
        ) : state === "recording" ? (
          <span className="h-3 w-3 rounded-[2px] bg-crit" aria-hidden />
        ) : (
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round" aria-hidden>
            <rect x="9" y="3" width="6" height="11" rx="3" />
            <path d="M5 11a7 7 0 0 0 14 0" />
            <path d="M12 18v3" />
          </svg>
        )}
      </button>
    </div>
  );
}
