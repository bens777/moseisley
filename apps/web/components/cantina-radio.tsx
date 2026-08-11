"use client";
import { useCallback, useEffect, useRef, useState } from "react";

/* CANTINA RADIO — ambient music, mounted once in the ROOT LAYOUT so the
   <audio> element survives every client-side navigation (homepage → /pricing →
   /legal → /login → dashboard). Only a hard reload or a new tab can reset it,
   and that path restores track + position and waits for a gesture.

   No autoplay, ever: browsers require a user gesture, and a stored preference
   is not a gesture. play() is only ever called from a click/keypress. */

// Files present in /public/audio (a client component cannot glob the
// filesystem). Add new tracks here.
const TRACKS = ["cantina-01.mp3"];

const STORE_KEY = "cantina_radio";
const DEFAULT_VOLUME = 0.15;
const SAVE_EVERY_MS = 5000;

/* The welcome gate answers the "music?" question on first load, so the radio
   suppresses its own chip until that key exists. */
export const WELCOME_KEY = "welcome_seen";

/* Imperative handles: the welcome gate lives in another subtree (the homepage
   is a server component), so a module-level registry is the smallest thing
   that works. The click inside the gate is the gesture that unlocks audio. */
type Handles = { start: () => void; decline: () => void };
let handles: Handles | null = null;

export function startCantinaRadio() { handles?.start(); }
export function declineCantinaRadio() { handles?.decline(); }

type Pref = {
  on: boolean;       // the listener wants music
  volume: number;
  track: number;     // index into TRACKS
  time: number;      // playback position, seconds
  playing: boolean;  // was it actually playing when last saved
};

const EMPTY: Pref = { on: false, volume: DEFAULT_VOLUME, track: 0, time: 0, playing: false };

function readPref(): Pref | null {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<Pref>;
    return {
      on: !!p.on,
      volume: typeof p.volume === "number" ? p.volume : DEFAULT_VOLUME,
      track: typeof p.track === "number" && p.track >= 0 && p.track < TRACKS.length ? p.track : 0,
      time: typeof p.time === "number" && p.time > 0 ? p.time : 0,
      playing: !!p.playing,
    };
  } catch { return null; }
}

function writePref(patch: Partial<Pref>) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ ...(readPref() ?? EMPTY), ...patch }));
  } catch { /* private mode */ }
}

/* "cantina-01.mp3" → "Cantina 01" */
function prettify(file: string): string {
  return file.replace(/\.[^.]+$/, "").split(/[-_]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

export function CantinaRadio() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const seekToRef = useRef(0);                   // position to apply once metadata loads
  const [asked, setAsked] = useState(true);      // hide the chip until we read the pref
  const [visible, setVisible] = useState(false); // player shown
  const [playing, setPlaying] = useState(false);
  const [volume, setVolume] = useState(DEFAULT_VOLUME);
  const [track, setTrack] = useState(0);

  const save = useCallback((patch: Partial<Pref>) => {
    const el = audioRef.current;
    writePref({ time: el ? el.currentTime : 0, ...patch });
  }, []);

  /* The ONLY place play() is called. Every caller is a user gesture, or a
     resume armed by the next one. */
  const play = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (seekToRef.current > 0 && el.readyState > 0) {
      el.currentTime = seekToRef.current;
      seekToRef.current = 0;
    }
    el.play()
      .then(() => { setPlaying(true); save({ on: true, playing: true }); })
      .catch(() => { setPlaying(false); save({ playing: false }); });
  }, [save]);

  /* Hard reload / new tab: audio cannot resume by itself, so wait for the
     first interaction anywhere and pick up where we left off. */
  const armResume = useCallback(() => {
    const events = ["pointerdown", "keydown", "touchstart"] as const;
    const fire = () => {
      events.forEach((e) => document.removeEventListener(e, fire));
      play();
    };
    events.forEach((e) => document.addEventListener(e, fire, { once: true, passive: true }));
  }, [play]);

  // ── expose start/decline to the welcome gate ──────────────────────
  useEffect(() => {
    handles = {
      start: () => {
        setAsked(true); setVisible(true); setVolume(DEFAULT_VOLUME);
        const el = audioRef.current;
        if (el) el.volume = DEFAULT_VOLUME;
        writePref({ on: true, volume: DEFAULT_VOLUME });
        play();
      },
      decline: () => {
        // no music, but leave the player on screen in its paused state
        setAsked(true); setVisible(true); setPlaying(false);
        writePref({ on: false, playing: false });
      },
    };
    return () => { handles = null; };
  }, [play]);

  // ── restore on mount (hard load only; client navs never remount) ──
  useEffect(() => {
    const pref = readPref();
    if (pref === null) {
      // first visit: the welcome gate asks (homepage). Elsewhere the chip is
      // the fallback, but only once the gate has been seen.
      try { if (localStorage.getItem(WELCOME_KEY)) setAsked(false); } catch { /* ignore */ }
      return;
    }
    setVolume(pref.volume);
    setTrack(pref.track);
    seekToRef.current = pref.time;
    const el = audioRef.current;
    if (el) el.volume = pref.volume;
    if (!pref.on) return;            // opted out → silent, hidden
    setVisible(true);
    if (pref.playing) armResume();   // was playing before the reload
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── persistence: throttled while playing, and on the way out ──────
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => save({}), SAVE_EVERY_MS);
    return () => clearInterval(id);
  }, [playing, save]);

  useEffect(() => {
    const flush = () => save({ playing: !audioRef.current?.paused });
    document.addEventListener("visibilitychange", flush);
    window.addEventListener("pagehide", flush);
    return () => {
      document.removeEventListener("visibilitychange", flush);
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [save]);

  if (TRACKS.length === 0) return null;

  function toggle() {
    const el = audioRef.current;
    if (!el) return;
    if (playing) { el.pause(); setPlaying(false); save({ on: false, playing: false }); return; }
    play();
  }

  function changeVolume(v: number) {
    setVolume(v);
    if (audioRef.current) audioRef.current.volume = v;
    save({ volume: v });
  }

  function dismiss() {
    audioRef.current?.pause();
    setPlaying(false);
    setVisible(false);
    save({ on: false, playing: false });
  }

  return (
    <>
      <audio
        ref={audioRef}
        src={`/audio/${TRACKS[track]}`}
        loop={TRACKS.length === 1}
        onLoadedMetadata={() => {
          const el = audioRef.current;
          if (el && seekToRef.current > 0) {
            el.currentTime = seekToRef.current;
            seekToRef.current = 0;
          }
        }}
        onEnded={() => setTrack((t) => {
          const next = (t + 1) % TRACKS.length;
          save({ track: next, time: 0 });
          return next;
        })}
        preload="none"
      />

      {!asked && (
        <div className="cantina fixed bottom-20 left-4 right-4 z-20 flex flex-wrap items-center justify-center gap-2 rounded-full border border-[var(--cw-line)] bg-[var(--cw-deep)]/95 px-4 py-2 sm:left-auto sm:bottom-5 sm:right-4">
          <span className="font-mono text-[11px] uppercase tracking-widest text-[var(--cw-mute)]">
            ♫ Turn on Cantina Radio?
          </span>
          <button onClick={() => { setAsked(true); setVisible(true); play(); }}
                  className="cw-cta rounded-full px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest text-white">
            Set the mood
          </button>
          <button onClick={() => { setAsked(true); writePref({ on: false, playing: false }); }}
                  className="rounded-full border border-[var(--cw-line)] px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--cw-faint)] hover:text-[var(--cw-mute)]">
            Not now
          </button>
        </div>
      )}

      {visible && (
        /* Bottom-right, clear of everything else that is fixed:
             · the dashboard sidebar + its bottom stack live on the LEFT;
             · the Manager launcher sits at bottom-4 right-4 (z-40), so the
               player rides above it at bottom-20 on mobile / bottom-5 on
               desktop where the launcher is out of the way;
             · z-20 keeps it beneath the mobile nav drawer (z-40), the Manager
               drawer (z-30) and the welcome gate (z-50). */
        <div className="cantina fixed bottom-20 left-4 right-4 z-20 sm:left-auto sm:bottom-5 sm:right-4">
          <div className="cw-panel flex items-center gap-3 px-4 py-2.5">
            <button onClick={toggle} aria-label={playing ? "Pause Cantina Radio" : "Play Cantina Radio"}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--cw-line)] text-sm text-[var(--cw-ink)] hover:border-[var(--cw-purple)]">
              {playing ? "❚❚" : "▶"}
            </button>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--cw-faint)]">
                <span className={`led ${playing ? "cw-led-cyan" : "cw-led-gold"}`} aria-hidden />
                Cantina Radio
              </div>
              <div className="truncate font-mono text-[11px] text-[var(--cw-mute)]">
                {prettify(TRACKS[track])}
              </div>
            </div>
            <input type="range" min={0} max={1} step={0.05} value={volume}
                   onChange={(e) => changeVolume(Number(e.target.value))}
                   aria-label="Cantina Radio volume"
                   className="h-1 w-20 shrink-0 accent-[var(--cw-magenta)] sm:w-24" />
            <button onClick={dismiss} aria-label="Hide Cantina Radio"
                    className="shrink-0 px-1 font-mono text-xs text-[var(--cw-faint)] hover:text-[var(--cw-ink)]">
              ✕
            </button>
          </div>
        </div>
      )}
    </>
  );
}
