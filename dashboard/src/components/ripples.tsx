"use client";

import { useEffect } from "react";

/**
 * Click ripple for every button on the site and console: a soft light
 * expands from the exact point that was pressed. One document listener, no
 * per-button wiring. Respects reduced motion.
 */
export function Ripples() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const onDown = (event: PointerEvent) => {
      const target = (event.target as HTMLElement | null)?.closest<HTMLElement>(
        "button, .btn, [role='button'], a[data-ripple]",
      );
      if (!target || target.hasAttribute("disabled")) return;
      const rect = target.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height) * 2.4;
      const style = getComputedStyle(target);
      if (style.position === "static") target.style.position = "relative";
      if (style.overflow !== "hidden") target.style.overflow = "hidden";
      const filled = /btn-accent|bg-\[var\(--accent\)\]|ripple-light/.test(target.className);
      const ripple = document.createElement("span");
      ripple.className = filled ? "ripple" : "ripple ripple-dark";
      ripple.style.width = ripple.style.height = `${size}px`;
      ripple.style.left = `${event.clientX - rect.left}px`;
      ripple.style.top = `${event.clientY - rect.top}px`;
      target.appendChild(ripple);
      window.setTimeout(() => ripple.remove(), 650);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, []);
  return null;
}
