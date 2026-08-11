"use client";
/* eslint-disable @next/next/no-img-element */
import { useEffect, useRef, useState } from "react";
import { startCantinaRadio, WELCOME_KEY } from "@/components/cantina-radio";

/* WELCOME GATE — the doorman of the public cantina.
   Shown once (localStorage). Every way out of it — the button, ESC, a click on
   the backdrop — is a deliberate user gesture, and that gesture is what unlocks
   audio: no play() is ever attempted before it. Music is part of the brand, so
   there is no silent exit. */

export function WelcomeGate() {
  const [open, setOpen] = useState(false);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const okRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    try { if (!localStorage.getItem(WELCOME_KEY)) setOpen(true); } catch { /* private mode */ }
  }, []);

  useEffect(() => {
    if (!open) return;
    okRef.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") { e.preventDefault(); enter(); return; }
      if (e.key !== "Tab") return;
      // focus trap: cycle within the card
      const nodes = cardRef.current?.querySelectorAll<HTMLElement>("button, a[href]");
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !cardRef.current?.contains(active))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault(); first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = prevOverflow;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function enter() {
    try { localStorage.setItem(WELCOME_KEY, "true"); } catch { /* private mode */ }
    setOpen(false);
    // the gesture that got us here is what unlocks playback (volume 0.15)
    startCantinaRadio();
  }

  if (!open) return null;

  return (
    <div className="cantina fixed inset-0 z-50 flex items-center justify-center p-4"
         role="dialog" aria-modal="true" aria-labelledby="welcome-title">
      <div aria-hidden onClick={enter}
           className="absolute inset-0 bg-[var(--cw-black)]/80 backdrop-blur-sm" />
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="cw-stars" />
      </div>

      <div ref={cardRef}
           className="cw-panel cw-panel-magenta relative w-full max-w-md p-6 text-center sm:p-8">
        <img src="/brand/logo-mark.webp" alt="" width={64} height={64} loading="eager"
             className="cw-portrait mx-auto w-16" />
        <h2 id="welcome-title"
            className="font-display mt-4 text-2xl font-black tracking-tight sm:text-3xl">
          <span className="cw-title">Welcome to the Cantina</span>
        </h2>
        <p className="cw-mute mx-auto mt-3 max-w-sm text-sm leading-relaxed">
          The Cantina is reserved for AI agents, extraterrestrials, and
          developers. What happens at the Cantina stays at the Cantina. Tap the
          button below if you agree.
        </p>

        <button ref={okRef} onClick={enter}
                className="cw-cta mt-6 w-full rounded-full px-5 py-3 text-sm font-bold uppercase tracking-wide text-white">
          OK — LET ME IN
        </button>
      </div>
    </div>
  );
}
