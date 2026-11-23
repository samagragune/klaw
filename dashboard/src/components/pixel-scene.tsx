"use client";

/**
 * Pixel-art scenes for the Klaw site, drawn in code (no image assets).
 *
 * The world: an agent workshop at golden hour. Tall factory windows glow
 * coral and amber, light shafts cross the floor, server racks blink, and
 * small robot agents move crates of work along a conveyor - while the Klaw
 * claw rides a ceiling gantry, ready to lift out the one that shouldn't ship.
 *
 * Scenes are painted once at low resolution and scaled up with
 * nearest-neighbour sampling. A seeded generator keeps every load identical.
 */

import { useEffect, useRef } from "react";

type RGB = [number, number, number];
const hex = (h: string): RGB => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);
const dither = (x: number, y: number) => BAYER[(y & 3) * 4 + (x & 3)];

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

class Canvas {
  data: Uint8ClampedArray;
  constructor(public w: number, public h: number) {
    this.data = new Uint8ClampedArray(w * h * 4);
  }
  set(x: number, y: number, c: RGB, a = 255) {
    x = Math.round(x);
    y = Math.round(y);
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    const i = (y * this.w + x) * 4;
    this.data[i] = c[0];
    this.data[i + 1] = c[1];
    this.data[i + 2] = c[2];
    this.data[i + 3] = a;
  }
  get(x: number, y: number): RGB {
    const i = (Math.round(y) * this.w + Math.round(x)) * 4;
    return [this.data[i], this.data[i + 1], this.data[i + 2]];
  }
  /** Tint toward a colour; t is quantised with dithering to keep it pixel-pure. */
  tint(x: number, y: number, c: RGB, t: number) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    const q = Math.floor(t * 3 + dither(x, y)) / 3;
    if (q <= 0) return;
    this.set(x, y, mix(this.get(x, y), c, Math.min(1, q)));
  }
  rect(x: number, y: number, w: number, h: number, c: RGB) {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) this.set(x + i, y + j, c);
  }
}

const C = {
  wall: hex("#262130"),
  wallShade: hex("#1f1b28"),
  wallDeep: hex("#17141e"),
  beam: hex("#3b3243"),
  floor: hex("#2c2532"),
  floorDark: hex("#221c28"),
  ink: hex("#0e0d12"),
  inkSoft: hex("#1c1a23"),
  steel: hex("#4c4858"),
  steelLight: hex("#7d7890"),
  coral: hex("#ef4f3c"),
  coralDark: hex("#b8352a"),
  amber: hex("#f6b445"),
  cream: hex("#f6efe4"),
  creamShade: hex("#d9cdbd"),
  screen: hex("#1f2a2e"),
  eye: hex("#5fe3a8"),
  sun: hex("#ffc779"),
  crate: hex("#9a6b43"),
  crateDark: hex("#6e4a2c"),
  green: hex("#5fe3a8"),
};

/* ------------------------------------------------------------- painters */

function gradient(c: Canvas, x0: number, y0: number, w: number, h: number, stops: string[], bands = 6) {
  const cols = stops.map(hex);
  for (let y = y0; y < y0 + h; y++) {
    const t = (y - y0) / Math.max(1, h - 1);
    const pos = t * (cols.length - 1);
    const k = Math.min(cols.length - 2, Math.floor(pos));
    for (let x = x0; x < x0 + w; x++) {
      const q = Math.floor((pos - k) * bands + dither(x, y)) / bands;
      c.set(x, y, mix(cols[k], cols[k + 1], Math.min(1, q)));
    }
  }
}

/** Arched factory window with mullions and a sunset beyond. */
function windowPane(c: Canvas, x: number, y: number, w: number, h: number) {
  gradient(c, x, y, w, h, ["#171a42", "#33296a", "#7d4379", "#e2684f", "#f3a24a"], 5);
  // distant skyline of server towers, a few windows still lit
  const r = rng(x * 7 + y);
  for (let i = x; i < x + w; i += 3 + Math.floor(r() * 3)) {
    const th = 4 + Math.floor(r() * h * 0.38);
    for (let j = 0; j < th; j++) for (let k = 0; k < 3; k++) {
      c.set(i + k, y + h - 1 - j, j % 3 === 1 && k === 1 && r() < 0.35 ? hex("#ffc779") : hex("#141533"));
    }
  }
  for (let i = x; i < x + w; i++) {
    c.set(i, y, C.beam);
    c.set(i, y + h, C.beam);
    if ((i - x) % Math.floor(w / 3) === 0) for (let j = y; j < y + h; j++) c.set(i, j, C.beam);
  }
  for (let j = y; j <= y + h; j++) c.set(x + w, j, C.beam);
  for (const fy of [0.33, 0.66]) for (let i = x; i < x + w; i++) c.set(i, y + Math.round(h * fy), C.beam);
}

/** Warm light shafts falling from the windows. */
function lightShafts(c: Canvas, windows: [number, number, number, number][]) {
  // the dusk outside spills a little coral onto the floor
  for (const [x, y, w, h] of windows) {
    for (let j = y + h; j < c.h; j++) {
      const drift = (j - (y + h)) * 0.55;
      const fade = 1 - (j - (y + h)) / (c.h - y - h);
      for (let i = 0; i < w; i++) {
        const px = Math.round(x + i + drift);
        if (px >= 0 && px < c.w) c.set(px, j, mix(c.get(px, j), hex("#e2684f"), 0.07 * fade));
      }
    }
  }
}

/** Pendant lamps hanging from the ceiling, each throwing a warm cone. */
function lamps(c: Canvas, xs: number[], top: number) {
  for (const x of xs) {
    c.rect(x, 0, 1, top, C.steel);
    c.rect(x - 4, top, 9, 3, C.inkSoft);
    c.rect(x - 2, top + 3, 5, 1, C.sun);
    for (let j = top + 4; j < c.h; j++) {
      const spread = (j - top) * 0.42;
      const fade = 1 - (j - top) / (c.h - top);
      for (let i = Math.ceil(-spread); i <= spread; i++) {
        const px = x + i;
        if (px < 0 || px >= c.w) continue;
        const edge = 1 - Math.abs(i) / (spread + 1);
        c.set(px, j, mix(c.get(px, j), C.sun, 0.13 * fade * Math.min(1, edge * 1.6)));
      }
    }
  }
}

function rack(c: Canvas, r: () => number, x: number, y: number, w: number, h: number) {
  c.rect(x, y, w, h, C.ink);
  c.rect(x + 1, y + 1, w - 2, h - 2, C.inkSoft);
  for (let j = y + 3; j < y + h - 3; j += 4) {
    c.rect(x + 2, j, w - 4, 2, C.ink);
    for (let i = x + 3; i < x + w - 3; i += 3) {
      const roll = r();
      if (roll < 0.35) c.set(i, j, roll < 0.08 ? C.coral : roll < 0.16 ? C.amber : C.green);
    }
  }
}

/** A small robot agent: cream body, dark screen face, green eyes, antenna. */
function robot(c: Canvas, x: number, y: number, opts: { carrying?: "crate" | "bad"; flip?: boolean; eye?: RGB } = {}) {
  const eye = opts.eye ?? C.eye;
  // antenna
  c.set(x + 5, y, C.coral);
  c.rect(x + 5, y + 1, 1, 2, C.steel);
  // head
  c.rect(x + 1, y + 3, 10, 7, C.cream);
  c.rect(x + 1, y + 9, 10, 1, C.creamShade);
  c.rect(x + 2, y + 4, 8, 4, C.screen);
  c.set(x + (opts.flip ? 4 : 4), y + 5, eye);
  c.set(x + (opts.flip ? 7 : 7), y + 5, eye);
  // body
  c.rect(x + 2, y + 10, 8, 6, C.cream);
  c.rect(x + 2, y + 15, 8, 1, C.creamShade);
  c.set(x + 5, y + 12, C.coral);
  // legs
  c.rect(x + 3, y + 16, 2, 3, C.steel);
  c.rect(x + 7, y + 16, 2, 3, C.steel);
  if (opts.carrying) {
    const bx = opts.flip ? x - 6 : x + 9;
    const bad = opts.carrying === "bad";
    c.rect(bx, y + 9, 7, 6, bad ? C.coral : C.crate);
    c.rect(bx, y + 14, 7, 1, bad ? C.coralDark : C.crateDark);
    c.rect(bx + 1, y + 11, 5, 1, bad ? C.cream : C.crateDark);
  }
}

function crate(c: Canvas, x: number, y: number, bad = false) {
  c.rect(x, y, 9, 7, bad ? C.coral : C.crate);
  c.rect(x, y + 6, 9, 1, bad ? C.coralDark : C.crateDark);
  c.rect(x, y, 9, 1, bad ? hex("#ff7a68") : hex("#dcae7e"));
  c.rect(x + 2, y + 3, 5, 1, bad ? C.cream : C.crateDark);
}

function conveyor(c: Canvas, y: number, x0 = 0, x1 = c.w) {
  c.rect(x0, y, x1 - x0, 3, C.ink);
  for (let x = x0; x < x1; x++) if (x % 4 === 0) c.set(x, y + 1, C.steelLight);
  for (let x = x0 + 6; x < x1; x += 18) {
    c.rect(x, y + 3, 2, 8, C.steel);
    c.rect(x - 1, y + 10, 4, 1, C.ink);
  }
}

const CLAW_ROWS = ["......XXXX......", ".......XX.......", ".....XXXXXX.....", "....XXooooXX....", ".....XXXXXX.....",
  "....XX.XX.XX....", "...XX......XX...", "..XX........XX..", "..X..........X..", "..XX........XX..", "...X........X..."];

function claw(c: Canvas, x: number, y: number, railY: number, scale = 1) {
  // trolley on the rail
  c.rect(x + 4 * scale, railY - 2, 8 * scale, 4, C.steel);
  c.rect(x + 7 * scale, railY + 2, 2 * scale, y - railY - 2, C.inkSoft);
  CLAW_ROWS.forEach((row, j) => [...row].forEach((ch, i) => {
    if (ch !== ".") c.rect(x + i * scale, y + j * scale, scale, scale, ch === "o" ? C.coral : C.ink);
  }));
}

/* ---------------------------------------------------------------- scenes */

function paintHero(c: Canvas) {
  const r = rng(11);
  // back wall with a faint plaster texture
  for (let y = 0; y < c.h; y++) for (let x = 0; x < c.w; x++) {
    c.set(x, y, y < 18 ? C.wallShade : dither(x, y) < 0.06 ? C.wallShade : C.wall);
  }
  // ceiling beams and the gantry rail
  c.rect(0, 0, c.w, 6, C.beam);
  for (let x = 0; x < c.w; x += 40) c.rect(x, 0, 3, 18, C.beam);
  const railY = 24;
  c.rect(0, railY, c.w, 3, C.ink);
  for (let x = 2; x < c.w; x += 12) c.set(x, railY + 1, C.steelLight);
  // tall arched windows
  const wins: [number, number, number, number][] = [[24, 40, 46, 86], [112, 40, 46, 86], [200, 40, 46, 86], [288, 40, 46, 86]];
  wins.forEach(([x, y, w, h]) => windowPane(c, x, y, w, h));
  // floor
  for (let y = 168; y < c.h; y++) for (let x = 0; x < c.w; x++) {
    c.set(x, y, (y - 168) % 9 === 0 || (x + (y >> 3) * 5) % 26 === 0 ? C.floorDark : C.floor);
  }
  c.rect(0, 166, c.w, 2, C.wallDeep);
  // server racks between the windows
  [[74, 92, 30, 76], [162, 104, 30, 64], [250, 92, 30, 76]].forEach(([x, y, w, h]) => rack(c, r, x, y, w, h));
  lightShafts(c, wins);
  lamps(c, [90, 178, 266], 32);
  // the line: conveyor, crates, robots
  conveyor(c, 150);
  [14, 58, 130, 176, 218].forEach((x) => crate(c, x, 143));
  crate(c, 306, 143, true); // the one that shouldn't ship
  robot(c, 34, 147, { carrying: "crate" });
  robot(c, 96, 147);
  robot(c, 152, 147, { flip: true });
  robot(c, 264, 147, { carrying: "crate", flip: true });
  robot(c, 332, 147, { eye: C.amber });
  // Klaw on its gantry, silhouetted against the window, over the coral crate
  claw(c, 294, 116, railY, 2);
}

function paintMonitors(c: Canvas) {
  const r = rng(23);
  gradient(c, 0, 0, c.w, c.h, ["#141220", "#1e1a2c", "#2a2236"], 5);
  // a wall of monitors in a console room
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 6; col++) {
      const x = 14 + col * 50;
      const y = 14 + row * 40;
      c.rect(x, y, 44, 32, C.ink);
      c.rect(x + 2, y + 2, 40, 26, C.screen);
      const kind = (row * 6 + col) % 4;
      for (let i = 0; i < 36; i++) {
        if (kind === 0) c.set(x + 4 + i, y + 22 - Math.round(Math.abs(Math.sin(i / 4 + row)) * 12), C.green);
        if (kind === 1 && i % 4 === 0) c.rect(x + 4 + i, y + 26 - Math.floor(r() * 18), 3, 1 + Math.floor(r() * 18), C.amber);
        if (kind === 2 && i < 24) c.rect(x + 4, y + 5 + Math.floor(i / 4) * 3, 8 + Math.floor(r() * 24), 1, i % 7 === 3 ? C.coral : C.steelLight);
      }
      if (kind === 3) claw(c, x + 14, y + 8, y + 3, 1);
      c.rect(x + 18, y + 32, 8, 3, C.ink);
    }
  }
  c.rect(0, c.h - 18, c.w, 18, C.wallDeep);
  robot(c, 60, c.h - 36);
  robot(c, 230, c.h - 36, { flip: true, eye: C.amber });
}

function paintBench(c: Canvas, seed: number, palette: string[], subject: "terminal" | "robots" | "brain") {
  const r = rng(seed);
  gradient(c, 0, 0, c.w, c.h, palette, 5);
  c.rect(0, c.h - 20, c.w, 20, C.floor);
  c.rect(0, c.h - 21, c.w, 2, C.beam);
  if (subject === "terminal") {
    c.rect(c.w / 2 - 30, c.h - 64, 60, 42, C.ink);
    c.rect(c.w / 2 - 27, c.h - 61, 54, 34, C.screen);
    for (let i = 0; i < 5; i++) c.rect(c.w / 2 - 23, c.h - 56 + i * 6, 10 + Math.floor(r() * 30), 2, i === 0 ? C.coral : C.green);
    c.rect(c.w / 2 - 6, c.h - 22, 12, 2, C.ink);
  } else if (subject === "robots") {
    for (let i = 0; i < 4; i++) robot(c, 18 + i * 34, c.h - 40, { carrying: i === 2 ? "bad" : i % 2 ? "crate" : undefined });
    claw(c, 18 + 2 * 34 + 2, 10, 4, 1);
  } else {
    rack(c, r, 20, c.h - 76, 34, 56);
    rack(c, r, c.w - 54, c.h - 76, 34, 56);
    // a glowing chip in the middle
    c.rect(c.w / 2 - 14, c.h - 58, 28, 28, C.ink);
    c.rect(c.w / 2 - 10, c.h - 54, 20, 20, C.coral);
    for (let i = 0; i < 6; i++) {
      c.rect(c.w / 2 - 12 + i * 5, c.h - 62, 2, 4, C.steelLight);
      c.rect(c.w / 2 - 12 + i * 5, c.h - 30, 2, 4, C.steelLight);
    }
  }
}

function paintLine(c: Canvas) {
  gradient(c, 0, 0, c.w, c.h, ["#171a42", "#33296a", "#7d4379", "#e2684f"], 5);
  const r = rng(77);
  for (let i = 0; i < c.w; i += 4 + Math.floor(r() * 4)) {
    const th = 10 + Math.floor(r() * 40);
    c.rect(i, c.h - 24 - th, 4, th, hex("#141533"));
    for (let j = c.h - 24 - th + 2; j < c.h - 26; j += 4) if (r() < 0.3) c.set(i + 1, j, hex("#ffc779"));
  }
  c.rect(0, c.h - 24, c.w, 24, C.floorDark);
  conveyor(c, c.h - 30);
  for (let x = 8; x < c.w; x += 30) {
    if ((x / 30) % 3 < 1) robot(c, x, c.h - 49, { carrying: "crate" });
    else crate(c, x, c.h - 37, x % 7 === 0);
  }
}

function paintRobotSprite(c: Canvas) {
  robot(c, 2, 4, { carrying: "crate" });
}

function paintClawSprite(c: Canvas) {
  claw(c, 0, 18, 2, 1);
  crate(c, 3, 31, true);
}

/* ------------------------------------------------------------- component */

export type SceneName = "hero" | "monitors" | "line" | "robot" | "claw" | "terminal" | "robots" | "brain";

const SIZES: Record<SceneName, [number, number]> = {
  hero: [360, 220],
  monitors: [320, 180],
  line: [480, 90],
  robot: [22, 26],
  claw: [16, 40],
  terminal: [160, 96],
  robots: [160, 96],
  brain: [160, 96],
};

function paint(name: SceneName, c: Canvas) {
  switch (name) {
    case "hero":
      return paintHero(c);
    case "monitors":
      return paintMonitors(c);
    case "line":
      return paintLine(c);
    case "robot":
      return paintRobotSprite(c);
    case "claw":
      return paintClawSprite(c);
    case "terminal":
      return paintBench(c, 3, ["#1d1626", "#3a2436"], "terminal");
    case "robots":
      return paintBench(c, 5, ["#121a36", "#233160"], "robots");
    case "brain":
      return paintBench(c, 9, ["#1e1430", "#3a2452"], "brain");
  }
}

export function PixelScene({
  name,
  className = "",
  fit = "cover",
  position = "center",
}: {
  name: SceneName;
  className?: string;
  fit?: "cover" | "contain";
  position?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const [w, h] = SIZES[name];
    const c = new Canvas(w, h);
    paint(name, c);
    el.width = w;
    el.height = h;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(w, h);
    img.data.set(c.data);
    ctx.putImageData(img, 0, 0);
  }, [name]);
  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      className={className}
      style={{ imageRendering: "pixelated", objectFit: fit, objectPosition: position, width: "100%", height: "100%", display: "block" }}
    />
  );
}
