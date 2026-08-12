"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

/* Public homepage mobile navigation (< md). A hamburger that opens a small
   cantina-styled menu. Support/Discord deliberately lives in the dashboard
   only and never appears here. */

const ITEMS = [
  // the conversion CTA leads, styled as the primary action
  { href: "/register", label: "Start free trial", primary: true },
  { href: "/pricing", label: "Pricing", primary: false },
  { href: "/login", label: "Log in", primary: false },
];

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
    }
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative md:hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close navigation" : "Open navigation"}
        aria-expanded={open}
        aria-controls="cantina-mobile-menu"
        className="flex h-11 w-11 items-center justify-center rounded-full border border-[var(--cw-line)] bg-[var(--cw-panel)] text-[var(--cw-ink)] hover:border-[var(--cw-purple)]"
      >
        {open ? (
          <span aria-hidden className="text-lg leading-none">✕</span>
        ) : (
          <span aria-hidden className="flex flex-col gap-[5px]">
            <span className="block h-px w-5 bg-[var(--cw-ink)]" />
            <span className="block h-px w-5 bg-[var(--cw-ink)]" />
            <span className="block h-px w-5 bg-[var(--cw-ink)]" />
          </span>
        )}
      </button>

      {open && (
        <div
          id="cantina-mobile-menu"
          className="cw-panel absolute right-0 top-full z-50 mt-2 w-44 p-2"
        >
          {ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => setOpen(false)}
              className={item.primary
                ? "cw-cta mb-1 block rounded-xl px-3 py-2.5 text-center text-sm font-bold uppercase tracking-wide text-white"
                : "cw-mute block rounded-xl px-3 py-2.5 text-sm hover:bg-[var(--cw-raised)] hover:text-[var(--cw-ink)]"}
            >
              {item.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
