"use client";

/**
 * Animated pixel-art scenes for the Klaw site, drawn in code (no assets).
 *
 * The world: an agent workshop at dusk. Robots walk the floor and ride crates
 * along a conveyor; racks blink; the city beyond the windows twinkles; and
 * the Klaw claw waits on its gantry, drops onto the one coral crate that
 * shouldn't ship, and carries it to the reject bin.
 *
 * Each scene paints its static backdrop once, then redraws only the moving
 * parts on a small clock (about 12 frames a second, the pace of pixel art).
 * Animation runs only while the scene is on screen and never for visitors
 * who ask for reduced motion.
 */

import { useEffect, useRef } from "react";

type RGB = [number, number, number];
const hex = (h: string): RGB => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);
const dither = (x: number, y: number) => BAYER[(y & 3) * 4 + (x & 3)];
const hash = (a: number, b = 0) => {
  let h = (a * 374761393 + b * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};

class Canvas {
  data: Uint8ClampedArray;
  constructor(public w: number, public h: number) {
    this.data = new Uint8ClampedArray(w * h * 4);
  }
  set(x: number, y: number, c: RGB) {
    x = Math.round(x);
    y = Math.round(y);
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    const i = (y * this.w + x) * 4;
    this.data[i] = c[0];
    this.data[i + 1] = c[1];
    this.data[i + 2] = c[2];
    this.data[i + 3] = 255;
  }
  get(x: number, y: number): RGB {
    const i = (Math.round(y) * this.w + Math.round(x)) * 4;
    return [this.data[i], this.data[i + 1], this.data[i + 2]];
  }
  blend(x: number, y: number, c: RGB, t: number) {
    x = Math.round(x);
    y = Math.round(y);
    if (x < 0 || y < 0 || x >= this.w || y >= this.h || t <= 0) return;
    this.set(x, y, mix(this.get(x, y), c, Math.min(1, t)));
  }
  rect(x: number, y: number, w: number, h: number, c: RGB) {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) this.set(x + i, y + j, c);
  }
}

/* ------------------------------------------------------------- palette */

const P = {
  ceiling: hex("#131017"),
  wall: hex("#1d1922"),
  brick: hex("#211c27"),
  mortar: hex("#18141d"),
  pipe: hex("#2c2733"),
  pipeHi: hex("#3d3746"),
  frame: hex("#2b2531"),
  sill: hex("#383040"),
  skyline: hex("#0e0f22"),
  lit: hex("#e2ad6e"),
  rack: hex("#0d0c11"),
  rackPanel: hex("#16141b"),
  vent: hex("#221f29"),
  ledG: hex("#3fbf8f"),
  ledA: hex("#d9a24a"),
  ledC: hex("#e2604a"),
  floor: hex("#191520"),
  plank: hex("#15111b"),
  grain: hex("#1d1925"),
  belt: hex("#0b0a0e"),
  roller: hex("#3a3542"),
  leg: hex("#2a2631"),
  crate: hex("#6d4c33"),
  crateHi: hex("#84603f"),
  crateLo: hex("#523824"),
  bad: hex("#d4553f"),
  badHi: hex("#e8735c"),
  badLo: hex("#9e3a2b"),
  bot: hex("#d9d2c6"),
  botLo: hex("#aaa297"),
  screen: hex("#141a1c"),
  eye: hex("#59d39f"),
  eyeA: hex("#e0a44f"),
  metal: hex("#4a4554"),
  ink: hex("#0b0a0e"),
  claw: hex("#0b0a0e"),
  clawAccent: hex("#e05a44"),
  lamp: hex("#f0c27a"),
  dusk: ["#12142c", "#201e44", "#3f2e58", "#7d4559", "#b2614b", "#c98650"],
  bin: hex("#26222d"),
};

/* ------------------------------------------------------------- painters */

function gradient(c: Canvas, x0: number, y0: number, w: number, h: number, stops: string[], bands = 7) {
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

function brickWall(c: Canvas, y0: number, y1: number) {
  for (let y = y0; y < y1; y++) {
    const row = Math.floor((y - y0) / 5);
    for (let x = 0; x < c.w; x++) {
      const mortar = (y - y0) % 5 === 4 || (x + (row % 2) * 6) % 12 === 0;
      const speck = hash(x, y) < 0.04;
      c.set(x, y, mortar ? P.mortar : speck ? P.brick : hash(x >> 2, row) < 0.5 ? P.wall : P.brick);
    }
  }
}

type Win = { x: number; y: number; w: number; h: number; towers: { x: number; w: number; h: number }[] };

function windowFrame(c: Canvas, x: number, y: number, w: number, h: number): Win {
  gradient(c, x, y, w, h, P.dusk, 7);
  const towers: Win["towers"] = [];
  for (let i = x + 1; i < x + w - 1; ) {
    const tw = 3 + Math.floor(hash(i, y) * 4);
    const th = 6 + Math.floor(hash(i, y + 1) * h * 0.42);
    towers.push({ x: i, w: tw, h: th });
    for (let j = 0; j < th; j++) for (let k = 0; k < tw && i + k < x + w; k++) c.set(i + k, y + h - 1 - j, P.skyline);
    if (th > h * 0.32) c.set(i + 1, y + h - th - 1, P.skyline);
    i += tw + (hash(i, 7) < 0.3 ? 1 : 0);
  }
  for (let i = x - 1; i <= x + w; i++) {
    c.set(i, y - 1, P.frame);
    c.set(i, y + h, P.frame);
  }
  for (let j = y; j < y + h; j++) {
    c.set(x - 1, j, P.frame);
    c.set(x + w, j, P.frame);
    c.set(x + Math.floor(w / 2), j, P.frame);
  }
  for (const f of [0.34, 0.68]) for (let i = x; i < x + w; i++) c.set(i, y + Math.round(h * f), P.frame);
  c.rect(x - 3, y + h + 1, w + 6, 2, P.sill);
  return { x, y, w, h, towers };
}

function rack(c: Canvas, x: number, y: number, w: number, h: number) {
  c.rect(x, y, w, h, P.rack);
  c.rect(x + 1, y + 1, w - 2, h - 2, P.rackPanel);
  for (let j = y + 3; j < y + h - 3; j += 5) {
    c.rect(x + 2, j, w - 4, 3, P.rack);
    for (let i = x + w - 8; i < x + w - 3; i += 2) c.set(i, j + 1, P.vent);
  }
  for (let k = 0; k < 3; k++) for (let j = 0; j < 8 + k * 3; j++) c.set(x + 3 + k * 2 + Math.round(Math.sin(j / 3) * 0.6), y - 1 - j * 0.5, P.pipe);
}

function rackLeds(c: Canvas, x: number, y: number, w: number, h: number, t: number) {
  for (let j = y + 3; j < y + h - 3; j += 5) {
    for (let i = x + 3; i < x + w - 10; i += 2) {
      const seed = hash(i, j);
      if (seed > 0.42) continue;
      const rate = 0.6 + seed * 3;
      if (Math.sin(t * rate + seed * 40) <= (seed < 0.05 ? 0.6 : -0.2)) continue;
      c.set(i, j + 1, seed < 0.03 ? P.ledC : seed < 0.1 ? P.ledA : P.ledG);
    }
  }
}

function lampLight(c: Canvas, x: number, top: number) {
  c.rect(x, 0, 1, top, P.metal);
  c.rect(x - 4, top, 9, 2, P.pipe);
  c.rect(x - 3, top + 2, 7, 1, P.pipeHi);
  c.rect(x - 1, top + 3, 3, 1, P.lamp);
  for (let j = top + 4; j < c.h; j++) {
    const spread = (j - top) * 0.45;
    const fade = 1 - (j - top) / (c.h - top);
    for (let i = Math.ceil(-spread); i <= spread; i++) {
      const edge = 1 - Math.abs(i) / (spread + 1);
      c.blend(x + i, j, P.lamp, 0.075 * fade * Math.min(1, edge * 1.8));
    }
  }
}

function floorBoards(c: Canvas, y0: number) {
  for (let y = y0; y < c.h; y++) {
    for (let x = 0; x < c.w; x++) {
      const seam = (y - y0) % 7 === 0 || (x + Math.floor((y - y0) / 7) * 17) % 38 === 0;
      c.set(x, y, seam ? P.plank : hash(x >> 3, y) < 0.08 ? P.grain : P.floor);
    }
  }
}

function crateSprite(c: Canvas, x: number, y: number, bad = false) {
  const [hi, mid, lo] = bad ? [P.badHi, P.bad, P.badLo] : [P.crateHi, P.crate, P.crateLo];
  c.rect(x, y, 11, 8, mid);
  c.rect(x, y, 11, 1, hi);
  c.rect(x, y + 7, 11, 1, lo);
  c.rect(x, y, 1, 8, lo);
  c.rect(x + 10, y, 1, 8, lo);
  c.rect(x + 3, y + 3, 5, 1, bad ? P.bot : lo);
  if (!bad) {
    c.set(x + 2, y + 1, lo);
    c.set(x + 8, y + 6, lo);
  }
}

/** Robot agent; `step` alternates the legs, `blink` closes the eyes. */
function robot(c: Canvas, x: number, y: number, o: { step?: number; carry?: boolean; flip?: boolean; eye?: RGB; blink?: boolean; armUp?: boolean } = {}) {
  const eye = o.eye ?? P.eye;
  c.set(x + 6, y, P.clawAccent);
  c.rect(x + 6, y + 1, 1, 2, P.metal);
  c.rect(x + 2, y + 3, 10, 8, P.bot);
  c.rect(x + 2, y + 10, 10, 1, P.botLo);
  c.rect(x + 3, y + 4, 8, 5, P.screen);
  if (!o.blink) {
    c.set(x + 5, y + 6, eye);
    c.set(x + 8, y + 6, eye);
  } else {
    c.rect(x + 5, y + 6, 4, 1, hex("#2a3a36"));
  }
  c.rect(x + 3, y + 11, 8, 7, P.bot);
  c.rect(x + 3, y + 17, 8, 1, P.botLo);
  c.set(x + 7, y + 13, P.clawAccent);
  const s = o.step ?? 0;
  c.rect(x + 4, y + 18, 2, s ? 3 : 2, P.metal);
  c.rect(x + 8, y + 18, 2, s ? 2 : 3, P.metal);
  if (o.armUp) c.rect(x + (o.flip ? 1 : 11), y + 10, 2, 2, P.botLo);
  if (o.carry) crateSprite(c, o.flip ? x - 9 : x + 10, y + 9);
}

const CLAW_OPEN = ["......XXXX......", ".......XX.......", ".....XXXXXX.....", "....XXooooXX....", ".....XXXXXX.....",
  "....XX.XX.XX....", "...XX......XX...", "..XX........XX..", "..X..........X..", "..XX........XX..", "...X........X..."];
const CLAW_SHUT = ["......XXXX......", ".......XX.......", ".....XXXXXX.....", "....XXooooXX....", ".....XXXXXX.....",
  ".....XX..XX.....", "....XX....XX....", "....X......X....", "....XX....XX....", ".....XX..XX.....", "......X..X......"];

function claw(c: Canvas, x: number, y: number, railY: number, shut: boolean) {
  c.rect(x + 3, railY - 1, 10, 4, P.metal);
  c.rect(x + 4, railY, 8, 1, P.pipeHi);
  c.rect(x + 7, railY + 3, 2, Math.max(0, y - railY - 3), P.pipe);
  (shut ? CLAW_SHUT : CLAW_OPEN).forEach((row, j) => [...row].forEach((ch, i) => {
    if (ch !== ".") c.set(x + i, y + j, ch === "o" ? P.clawAccent : P.claw);
  }));
}

/* ---------------------------------------------------------------- scenes */

type SceneDef = {
  w: number;
  h: number;
  base: (c: Canvas) => unknown;
  frame?: (c: Canvas, t: number, state: unknown) => void;
};

const smooth = (k: number) => k * k * (3 - 2 * k);

const HERO: SceneDef = {
  w: 480,
  h: 300,
  base(c) {
    c.rect(0, 0, c.w, c.h, P.wall);
    brickWall(c, 22, 226);
    c.rect(0, 0, c.w, 18, P.ceiling);
    for (const py of [6, 11]) {
      c.rect(0, py, c.w, 3, P.pipe);
      c.rect(0, py, c.w, 1, P.pipeHi);
      for (let x = 20 + py * 3; x < c.w; x += 64) c.rect(x, py - 1, 3, 5, P.metal);
    }
    const railY = 30;
    c.rect(0, railY, c.w, 3, P.ink);
    for (let x = 4; x < c.w; x += 16) c.rect(x, railY - 6, 1, 6, P.metal);
    const wins = [[34, 52, 60, 116], [150, 52, 60, 116], [270, 52, 60, 116], [386, 52, 60, 116]].map(([x, y, w, h]) => windowFrame(c, x, y, w, h));
    for (const win of wins) for (let j = win.y + win.h + 3; j < 226; j++) {
      const fade = 1 - (j - win.y - win.h) / 60;
      for (let i = win.x; i < win.x + win.w; i++) c.blend(i + (j - win.y - win.h) * 0.4, j, hex("#b2614b"), 0.05 * Math.max(0, fade));
    }
    const racks = [[100, 132, 40, 94], [216, 150, 40, 76], [336, 132, 40, 94]];
    racks.forEach(([x, y, w, h]) => rack(c, x, y, w, h));
    c.rect(4, 206, 26, 3, P.sill);
    c.rect(6, 209, 2, 17, P.leg);
    c.rect(26, 209, 2, 17, P.leg);
    c.rect(9, 192, 16, 12, P.ink);
    c.rect(10, 193, 14, 10, P.screen);
    c.rect(15, 204, 4, 2, P.ink);
    c.rect(0, 226, c.w, 2, P.mortar);
    floorBoards(c, 228);
    c.rect(0, 214, c.w, 3, P.belt);
    for (let x = 10; x < c.w; x += 24) {
      c.rect(x, 217, 2, 11, P.leg);
      c.rect(x - 1, 227, 4, 1, P.ink);
    }
    c.rect(448, 206, 26, 20, P.bin);
    c.rect(448, 206, 26, 2, P.pipeHi);
    c.rect(452, 212, 18, 1, P.badLo);
    [120, 236, 356].forEach((x) => lampLight(c, x, 40));
    return { wins, racks, railY };
  },
  frame(c, t, state) {
    const { wins, racks, railY } = state as { wins: Win[]; racks: number[][]; railY: number };
    for (const w of wins) for (const tw of w.towers) for (let j = 2; j < tw.h - 1; j += 3) {
      const s = hash(tw.x, j);
      if (s < 0.22 && Math.sin(t * (0.3 + s * 2) + s * 50) > -0.1) c.set(tw.x + 1, w.y + w.h - 1 - j, s < 0.04 ? P.ledC : P.lit);
    }
    racks.forEach(([x, y, w, h]) => rackLeds(c, x, y, w, h, t));
    const typed = Math.floor((t * 6) % 12);
    c.rect(11, 195, Math.min(10, typed), 1, P.ledG);
    if (Math.floor(t * 2) % 2) c.rect(11 + Math.min(10, typed), 198, 2, 1, P.ledG);
    const speed = 14;
    for (let x = 0; x < c.w; x += 6) c.set((x + t * speed) % c.w, 215, P.roller);
    // crates on the belt; once a loop the coral crate goes to the claw
    const loop = c.w + 40;
    const clawX = 400;
    const period = loop / speed;
    const phase = t % period;
    const badX0 = -20;
    const arrive = (clawX + 3 - badX0) / speed;
    const badX = badX0 + phase * speed;
    for (let k = 0; k < 6; k++) {
      const x = ((k * 82 + t * speed) % loop) - 30;
      if (Math.abs(x - badX) < 16) continue;
      crateSprite(c, x, 206);
    }
    let clawY = 60;
    let shut = false;
    let carried: [number, number] | null = null;
    let trolleyX = clawX;
    const grabY = 192;
    if (phase < arrive - 1.6) {
      crateSprite(c, badX, 206, true);
    } else if (phase < arrive) {
      clawY = 60 + (grabY - 60) * smooth((phase - (arrive - 1.6)) / 1.6);
      crateSprite(c, badX, 206, true);
    } else if (phase < arrive + 0.35) {
      clawY = grabY;
      shut = true;
      carried = [clawX + 3, 206];
    } else if (phase < arrive + 1.6) {
      clawY = grabY - (grabY - 70) * smooth((phase - arrive - 0.35) / 1.25);
      shut = true;
      carried = [clawX + 3, clawY + 14];
    } else if (phase < arrive + 2.6) {
      trolleyX = clawX + 46 * smooth((phase - arrive - 1.6) / 1.0);
      clawY = 70;
      shut = true;
      carried = [trolleyX + 3, 84];
    } else if (phase < arrive + 3.2) {
      trolleyX = clawX + 46;
      clawY = 70;
      const k = (phase - arrive - 2.6) / 0.6;
      carried = [trolleyX + 3, 84 + 120 * k * k];
    } else if (phase < arrive + 4.4) {
      const k = (phase - arrive - 3.2) / 1.2;
      trolleyX = clawX + 46 - 46 * smooth(k);
      clawY = 70 - 10 * k;
    }
    if (carried && carried[1] < 216) crateSprite(c, carried[0], carried[1], true);
    claw(c, trolleyX, Math.round(clawY), railY, shut);
    const bots = [
      { x0: 0, v: 9, carry: true },
      { x0: 160, v: 7, carry: false },
      { x0: 300, v: 10, carry: true },
    ];
    bots.forEach((b, i) => {
      const x = ((b.x0 + t * b.v) % (c.w + 40)) - 30;
      robot(c, x, 240 + i * 14, { step: Math.floor(t * 4 + i) % 2, carry: b.carry, eye: i === 2 ? P.eyeA : P.eye, blink: Math.floor(t * 0.7 + i * 3) % 9 === 0 });
    });
    for (let k = 0; k < 26; k++) {
      const lx = [120, 236, 356][k % 3];
      const life = (t * 0.12 + hash(k, 3)) % 1;
      const y = 60 + life * 160;
      const x = lx + Math.sin(t * 0.5 + k) * (y - 44) * 0.3;
      c.blend(x, y, P.lamp, 0.35 * Math.sin(life * Math.PI));
    }
  },
};

const MONITORS: SceneDef = {
  w: 480,
  h: 270,
  base(c) {
    c.rect(0, 0, c.w, c.h, P.wall);
    brickWall(c, 0, 210);
    const screens: [number, number][] = [];
    for (let row = 0; row < 2; row++) for (let col = 0; col < 4; col++) {
      const x = 52 + col * 98;
      const y = 26 + row * 76;
      c.rect(x - 2, y - 2, 88, 62, P.ink);
      c.rect(x, y, 84, 58, P.screen);
      c.rect(x + 38, y + 60, 8, 6, P.ink);
      screens.push([x, y]);
    }
    c.rect(0, 210, c.w, 2, P.mortar);
    floorBoards(c, 212);
    c.rect(290, 226, 176, 4, P.sill);
    c.rect(296, 230, 3, 30, P.leg);
    c.rect(457, 230, 3, 30, P.leg);
    c.rect(360, 220, 30, 6, P.ink);
    for (let i = 0; i < 6; i++) c.rect(363 + i * 4, 222, 3, 2, P.pipeHi);
    lampLight(c, 380, 0);
    return { screens };
  },
  frame(c, t, state) {
    const { screens } = state as { screens: [number, number][] };
    screens.forEach(([x, y], i) => {
      const kind = i % 4;
      if (kind === 0) {
        for (let k = 0; k < 9; k++) {
          const line = Math.floor(t * 2) + k;
          const w = 20 + Math.floor(hash(line, i) * 52);
          c.rect(x + 5, y + 5 + k * 6, w, 1, hash(line, 9) < 0.08 ? P.ledC : hex("#5a6b66"));
        }
      } else if (kind === 1) {
        for (let k = 0; k < 74; k++) {
          const v = Math.sin((k + t * 8) / 7 + i) * 0.5 + Math.sin((k + t * 5) / 3) * 0.2;
          c.set(x + 5 + k, y + 30 - Math.round(v * 16), mix(P.screen, P.ledG, 0.75));
        }
      } else if (kind === 2) {
        for (let k = 0; k < 10; k++) {
          const h = 4 + Math.round((Math.sin(t * 1.4 + k * 0.9 + i) * 0.5 + 0.5) * 34);
          c.rect(x + 6 + k * 7, y + 50 - h, 4, h, mix(P.screen, k === 6 ? P.ledC : P.ledA, 0.55));
        }
      } else {
        c.rect(x + 34, y + 8, 44, 7, hex("#3a3442"));
        const reply = Math.min(60, Math.floor((t * 14) % 90));
        for (let k = 0; k < 4; k++) c.rect(x + 6, y + 24 + k * 6, Math.max(0, Math.min(60, reply - k * 15)), 2, hex("#7c8f88"));
        if (Math.floor(t * 2) % 2) c.rect(x + 6 + Math.min(60, reply % 60), y + 24 + Math.min(3, Math.floor(reply / 15)) * 6, 2, 3, P.ledG);
      }
      for (let j = y + 60; j < y + 72; j++) for (let k = x; k < x + 84; k++) c.blend(k, j, hex("#3fbf8f"), 0.015);
    });
    robot(c, 366, 198, { armUp: Math.floor(t * 6) % 2 === 0, blink: Math.floor(t * 0.6) % 7 === 0 });
  },
};

const LINE: SceneDef = {
  w: 640,
  h: 120,
  base(c) {
    gradient(c, 0, 0, c.w, 84, P.dusk, 7);
    for (let i = 0; i < c.w; ) {
      const tw = 4 + Math.floor(hash(i, 1) * 6);
      const th = 12 + Math.floor(hash(i, 2) * 46);
      c.rect(i, 84 - th, tw, th, P.skyline);
      i += tw + (hash(i, 3) < 0.3 ? 1 : 0);
    }
    c.rect(0, 84, c.w, 36, P.floor);
    floorBoards(c, 90);
    c.rect(0, 84, c.w, 3, P.belt);
    for (let x = 8; x < c.w; x += 24) c.rect(x, 87, 2, 6, P.leg);
    return null;
  },
  frame(c, t) {
    for (let x = 0; x < c.w; x += 6) c.set((x + t * 16) % c.w, 85, P.roller);
    for (let k = 0; k < 8; k++) {
      const x = ((k * 86 + t * 16) % (c.w + 40)) - 30;
      crateSprite(c, x, 76, k === 5);
    }
    for (let k = 0; k < 4; k++) {
      const x = ((k * 170 + t * 11) % (c.w + 40)) - 30;
      robot(c, x, 94, { step: Math.floor(t * 4 + k) % 2, carry: k % 2 === 0 });
    }
    for (let i = 0; i < c.w; i += 5) {
      const s = hash(i, 11);
      if (s < 0.2 && Math.sin(t * (0.4 + s) + s * 30) > 0.2) c.set(i + 1, 84 - 6 - Math.floor(s * 30), P.lit);
    }
  },
};

function bench(stops: string[], subject: "terminal" | "robots" | "brain"): SceneDef {
  return {
    w: 200,
    h: 120,
    base(c) {
      gradient(c, 0, 0, c.w, c.h, stops, 6);
      c.rect(0, c.h - 24, c.w, 24, P.floor);
      floorBoards(c, c.h - 22);
      c.rect(0, c.h - 25, c.w, 2, P.mortar);
      if (subject === "terminal") {
        c.rect(c.w / 2 - 36, c.h - 76, 72, 48, P.ink);
        c.rect(c.w / 2 - 33, c.h - 73, 66, 40, P.screen);
        c.rect(c.w / 2 - 6, c.h - 28, 12, 4, P.ink);
      } else if (subject === "brain") {
        rack(c, 18, c.h - 90, 40, 66);
        rack(c, c.w - 58, c.h - 90, 40, 66);
        c.rect(c.w / 2 - 18, c.h - 70, 36, 36, P.ink);
        for (let i = 0; i < 7; i++) {
          c.rect(c.w / 2 - 15 + i * 5, c.h - 75, 2, 5, P.metal);
          c.rect(c.w / 2 - 15 + i * 5, c.h - 34, 2, 5, P.metal);
        }
      } else {
        c.rect(0, c.h - 34, c.w, 3, P.belt);
      }
      return null;
    },
    frame(c, t) {
      if (subject === "terminal") {
        const shown = Math.floor((t * 10) % 70);
        const lines: [number, RGB][] = [[18, P.clawAccent], [40, P.ledG], [30, P.ledG], [46, hex("#5a6b66")], [24, P.ledG]];
        let left = shown;
        lines.forEach(([w, col], i) => {
          c.rect(c.w / 2 - 28, c.h - 68 + i * 7, Math.max(0, Math.min(w, left)), 2, col);
          left -= w;
        });
        if (Math.floor(t * 2) % 2) c.rect(c.w / 2 - 28, c.h - 37, 3, 2, P.ledG);
      } else if (subject === "brain") {
        rackLeds(c, 18, c.h - 90, 40, 66, t);
        rackLeds(c, c.w - 58, c.h - 90, 40, 66, t);
        const glow = 0.55 + 0.45 * Math.sin(t * 2);
        c.rect(c.w / 2 - 13, c.h - 65, 26, 26, mix(P.badLo, P.bad, glow));
        c.rect(c.w / 2 - 7, c.h - 59, 14, 14, mix(P.bad, P.badHi, glow));
      } else {
        for (let x = 0; x < c.w; x += 6) c.set((x + t * 12) % c.w, c.h - 33, P.roller);
        for (let k = 0; k < 4; k++) {
          const x = ((k * 56 + t * 12) % (c.w + 30)) - 20;
          robot(c, x, c.h - 55, { step: Math.floor(t * 4 + k) % 2, carry: k !== 1 });
        }
        claw(c, 120, 18 + Math.round(Math.sin(t) * 6), 4, Math.sin(t) > 0.6);
      }
    },
  };
}

const ROBOT: SceneDef = {
  w: 26,
  h: 26,
  base: () => null,
  frame(c, t) {
    robot(c, 2, 2 + (Math.floor(t * 2) % 2), { carry: true, blink: Math.floor(t * 0.8) % 6 === 0 });
  },
};

const CLAW: SceneDef = {
  w: 16,
  h: 44,
  base: () => null,
  frame(c, t) {
    const k = (Math.sin(t * 1.2) + 1) / 2;
    const y = 8 + Math.round(k * 16);
    claw(c, 0, y, 1, k > 0.85);
    if (k > 0.85) crateSprite(c, 2, y + 12, true);
  },
};

const SCENES = {
  hero: HERO,
  monitors: MONITORS,
  line: LINE,
  robot: ROBOT,
  claw: CLAW,
  terminal: bench(["#1b1622", "#2b1f2c"], "terminal"),
  robots: bench(["#12162c", "#1d2445"], "robots"),
  brain: bench(["#1a1328", "#2a1d3c"], "brain"),
} satisfies Record<string, SceneDef>;

export type SceneName = keyof typeof SCENES;

/* ------------------------------------------------------------- component */

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
    const ctx = el?.getContext("2d");
    if (!el || !ctx) return;
    const def: SceneDef = SCENES[name];
    const bg = new Canvas(def.w, def.h);
    const state = def.base(bg);
    const frame = new Canvas(def.w, def.h);
    const img = ctx.createImageData(def.w, def.h);
    el.width = def.w;
    el.height = def.h;
    const draw = (t: number) => {
      frame.data.set(bg.data);
      def.frame?.(frame, t, state);
      img.data.set(frame.data);
      ctx.putImageData(img, 0, 0);
    };
    draw(0);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !def.frame) return;
    let visible = false;
    let raf = 0;
    let last = 0;
    const start = performance.now();
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (!visible || now - last < 80) return; // about 12 frames a second
      last = now;
      draw((now - start) / 1000);
    };
    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
    }, { threshold: 0.01 });
    io.observe(el);
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
    };
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
