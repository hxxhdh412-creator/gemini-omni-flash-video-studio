#!/usr/bin/env node
/**
 * Nhạc nền GỐC tạo bằng thuật toán (không sample, không bản quyền bên thứ 3) – an toàn để đăng quảng cáo.
 *
 *   node scripts/compose-music.mjs --preset luxury --bpm 120 --duration 30 \
 *        --markers 0,4,10,16,22 --impacts 10,26 --out public/music/theme.wav
 *   node scripts/compose-music.mjs --config music.config.json
 *
 * Preset: luxury | corporate | tech | energetic | lofi | cinematic | playful | synthwave | ambient
 * markers = mốc bắt đầu các cảnh (giây) → whoosh nhẹ ở mỗi mốc; mốc CUỐI = cảnh CTA (outro).
 * impacts = cú "boom" (giây) – có riser 2 ô nhịp dồn trước đó. Giữ ≤ 2–3 impact.
 * Bố cục: intro (tới marker[1]) → main → build (2 ô nhịp trước marker cuối) → outro (CTA).
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

// ---------- Tham số ----------
const argv = process.argv.slice(2);
const arg = (k) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 ? argv[i + 1] : undefined;
};
let cfg = {};
if (arg("config")) cfg = JSON.parse(readFileSync(arg("config"), "utf8"));
const list = (v) => (Array.isArray(v) ? v.map(Number) : String(v).split(",").filter(Boolean).map(Number));

const PRESET = arg("preset") ?? cfg.preset ?? "luxury";
const DUR = Number(arg("duration") ?? cfg.duration ?? 30);
const MARKERS = list(arg("markers") ?? cfg.markers ?? [0]);
const IMPACTS = list(arg("impacts") ?? cfg.impacts ?? []);
const OUT = arg("out") ?? cfg.out ?? "public/music/theme.wav";
let seed = Number(arg("seed") ?? cfg.seed ?? 1337);

const PRESETS = {
  luxury: { bpm: 120, prog: [["D", "min"], ["A#", "maj"], ["F", "maj"], ["C", "maj"]], keys: "piano", bass: "groove", drums: "soft", pad: 0.042, swing: 0, rev: 2.2 },
  corporate: { bpm: 112, prog: [["C", "maj"], ["G", "maj"], ["A", "min"], ["F", "maj"]], keys: "pluck", bass: "pulse8", drums: "pop", pad: 0.035, swing: 0, rev: 1.6 },
  tech: { bpm: 124, prog: [["A", "min"], ["F", "maj"], ["C", "maj"], ["G", "maj"]], keys: "arp16", bass: "pulse8", drums: "four", pad: 0.04, swing: 0, rev: 1.8 },
  energetic: { bpm: 128, prog: [["F#", "min"], ["D", "maj"], ["A", "maj"], ["E", "maj"]], keys: "stab", bass: "offbeat", drums: "four", pad: 0.03, swing: 0, rev: 1.4 },
  lofi: { bpm: 84, prog: [["F", "maj7"], ["E", "min7"], ["D", "min7"], ["C", "maj7"]], keys: "epiano", bass: "sustain", drums: "lofi", pad: 0.03, swing: 0.16, rev: 1.8, crackle: true, lowpass: 0.35 },
  cinematic: { bpm: 90, prog: [["D", "min"], ["A#", "maj"], ["G", "min"], ["A", "maj"]], keys: "piano-slow", bass: "drone", drums: "taiko", pad: 0.055, swing: 0, rev: 3 },
  playful: { bpm: 118, prog: [["C", "maj"], ["A", "min"], ["F", "maj"], ["G", "maj"]], keys: "bell", bass: "bounce", drums: "pop", pad: 0.025, swing: 0.1, rev: 1.4 },
  synthwave: { bpm: 110, prog: [["A", "min"], ["F", "maj"], ["C", "maj"], ["G", "maj"]], keys: "saw-arp", bass: "pulse8", drums: "gated", pad: 0.045, swing: 0, rev: 2.4 },
  ambient: { bpm: 80, prog: [["C", "maj7"], ["A", "min7"], ["F", "maj7"], ["G", "sus2"]], keys: "bell-slow", bass: "sustain", drums: "none", pad: 0.05, swing: 0, rev: 3 },
};
const P = PRESETS[PRESET];
if (!P) {
  console.error(`Preset không hợp lệ: ${PRESET}. Chọn: ${Object.keys(PRESETS).join(", ")}`);
  process.exit(1);
}
const BPM = Number(arg("bpm") ?? cfg.bpm ?? P.bpm);
const SR = 44100;
const N = Math.floor(SR * DUR);
const BEAT = 60 / BPM;
const BAR = BEAT * 4;

const L = new Float32Array(N);
const R = new Float32Array(N);
const sendL = new Float32Array(N);
const sendR = new Float32Array(N);

const rnd = () => {
  seed = (seed * 1664525 + 1013904223) >>> 0;
  return seed / 4294967296;
};
const noise = () => rnd() * 2 - 1;
const mtof = (m) => 440 * Math.pow(2, (m - 69) / 12);
const TAU = Math.PI * 2;
const panGains = (p) => [Math.cos(((p + 1) * Math.PI) / 4), Math.sin(((p + 1) * Math.PI) / 4)];
function put(i, l, r, send = 0) {
  if (i < 0 || i >= N) return;
  L[i] += l;
  R[i] += r;
  if (send) {
    sendL[i] += l * send;
    sendR[i] += r * send;
  }
}

// ---------- Hợp âm ----------
const NOTE = { C: 0, "C#": 1, D: 2, "D#": 3, E: 4, F: 5, "F#": 6, G: 7, "G#": 8, A: 9, "A#": 10, B: 11 };
const QUAL = { maj: [0, 4, 7], min: [0, 3, 7], maj7: [0, 4, 7, 11], min7: [0, 3, 7, 10], dom7: [0, 4, 7, 10], sus2: [0, 2, 7] };
const chord = ([root, q]) => {
  const r = NOTE[root];
  const iv = QUAL[q];
  const bassM = 36 + r > 45 ? 24 + r : 36 + r; // 36..47 → bass E2..
  const padRoot = 48 + r > 57 ? 36 + r : 48 + r;
  const pad = iv.map((x) => padRoot + x).concat(iv.length === 3 ? [padRoot + 12] : []);
  const arp = iv.map((x) => padRoot + 12 + x).concat([padRoot + 24]);
  return { bass: bassM, pad, arp };
};

// ---------- Nhạc cụ ----------
function pad(notes, start, dur, gain, send = 0.5, att = 0.7, saw = false) {
  const rel = 1.0;
  const s0 = Math.floor(start * SR);
  const len = Math.floor((dur + rel) * SR);
  notes.forEach((m, ni) => {
    const f = mtof(m);
    [-8, 0, 8].forEach((cents, vi) => {
      const fr = f * Math.pow(2, cents / 1200);
      const [gl, gr] = panGains((vi - 1) * 0.7);
      const ph0 = rnd() * TAU;
      for (let k = 0; k < len; k += 1) {
        const t = k / SR;
        let env = Math.min(1, t / att);
        if (t > dur) env *= Math.max(0, 1 - (t - dur) / rel);
        const lfo = 1 + 0.12 * Math.sin(TAU * 0.25 * t + ni);
        const ph = TAU * fr * t + ph0;
        const h = saw
          ? Math.sin(ph) + 0.5 * Math.sin(2 * ph) + 0.33 * Math.sin(3 * ph) + 0.25 * Math.sin(4 * ph) + 0.2 * Math.sin(5 * ph)
          : Math.sin(ph) + 0.35 * Math.sin(2 * ph) + 0.12 * Math.sin(3 * ph) + 0.05 * Math.sin(4 * ph);
        const v = h * env * lfo * gain * (saw ? 0.6 : 1);
        put(s0 + k, v * gl, v * gr, send);
      }
    });
  });
}

function piano(m, start, vel = 1, pan = 0, gain = 0.09, send = 0.35, decay = 1) {
  const f = mtof(m);
  const s0 = Math.floor(start * SR);
  const len = Math.floor(1.8 * decay * SR);
  const [gl, gr] = panGains(pan);
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    const att = Math.min(1, t / 0.003);
    let v = 0;
    for (let h = 1; h <= 6; h++) v += (Math.sin(TAU * f * h * t * (1 + 0.0004 * h)) / Math.pow(h, 1.4)) * Math.exp((-t * (1.8 + h * 1.3)) / decay);
    v *= att * vel * gain;
    put(s0 + k, v * gl, v * gr, send);
  }
}

function epiano(m, start, vel = 1, pan = 0, gain = 0.08) {
  const f = mtof(m);
  const s0 = Math.floor(start * SR);
  const len = Math.floor(2.2 * SR);
  const [gl, gr] = panGains(pan);
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    const env = Math.min(1, t / 0.004) * Math.exp(-t * 1.6);
    const mod = Math.sin(TAU * f * 14 * t) * 0.6 * Math.exp(-t * 9);
    const trem = 1 + 0.15 * Math.sin(TAU * 4.5 * t);
    const v = (Math.sin(TAU * f * t + mod) + 0.2 * Math.sin(TAU * f * 2 * t)) * env * trem * vel * gain;
    put(s0 + k, v * gl, v * gr, 0.3);
  }
}

function pluck(m, start, vel = 1, pan = 0, gain = 0.07, saw = false, len_ = 0.45) {
  const f = mtof(m);
  const s0 = Math.floor(start * SR);
  const len = Math.floor(len_ * SR);
  const [gl, gr] = panGains(pan);
  let lp = 0;
  let ph = rnd();
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    ph += f / SR;
    const raw = saw ? 2 * (ph % 1) - 1 + (2 * ((ph * 1.006) % 1) - 1) : (ph % 1 < 0.5 ? 1 : -1);
    const fc = 300 + 5000 * Math.exp(-t * (saw ? 6 : 14));
    lp += (1 - Math.exp((-TAU * fc) / SR)) * (raw - lp);
    const v = lp * Math.min(1, t / 0.002) * Math.exp(-t * (saw ? 4 : 7)) * vel * gain;
    put(s0 + k, v * gl, v * gr, 0.35);
  }
}

function bell(m, start, vel = 1, pan = 0, gain = 0.06, decay = 1) {
  const f = mtof(m);
  const s0 = Math.floor(start * SR);
  const len = Math.floor(2.5 * decay * SR);
  const [gl, gr] = panGains(pan);
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    const env = Math.min(1, t / 0.002) * Math.exp((-t * 2.4) / decay);
    const v = Math.sin(TAU * f * t + 2.2 * Math.exp(-t * 3) * Math.sin(TAU * f * 3.5 * t)) * env * vel * gain;
    put(s0 + k, v * gl, v * gr, 0.5);
  }
}

function bass(m, start, dur, gain = 0.16, shape = "warm") {
  const f = mtof(m);
  const s0 = Math.floor(start * SR);
  const len = Math.floor((dur + 0.08) * SR);
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    let env = Math.min(1, t / 0.01) * (shape === "drone" ? 1 : Math.exp(-t * 0.8));
    if (shape === "drone") env = Math.min(1, t / 0.8);
    if (t > dur) env *= Math.max(0, 1 - (t - dur) / 0.08);
    const ph = TAU * f * t;
    const v = (shape === "sub" ? Math.sin(ph) : Math.tanh(1.6 * (Math.sin(ph) + 0.25 * Math.sin(2 * ph)))) * env * gain;
    put(s0 + k, v, v);
  }
}

function kick(start, gain = 0.5, tom = false) {
  const s0 = Math.floor(start * SR);
  const len = Math.floor((tom ? 0.9 : 0.5) * SR);
  let ph = 0;
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    const f = tom ? 70 + 60 * Math.exp(-t * 12) : 44 + 90 * Math.exp(-t * 28);
    ph += (TAU * f) / SR;
    const v = (Math.sin(ph) * Math.exp(-t * (tom ? 4 : 7)) + noise() * (tom ? 0.3 : 0.15) * Math.exp(-t * (tom ? 40 : 200))) * gain;
    put(s0 + k, v, v, tom ? 0.6 : 0);
  }
}

function makeBiquad(type, fc, q) {
  const w = (TAU * fc) / SR;
  const a = Math.sin(w) / (2 * q);
  const c = Math.cos(w);
  const [b0, b1, b2] = type === "bp" ? [a, 0, -a] : [(1 + c) / 2, -(1 + c), (1 + c) / 2];
  const a0 = 1 + a,
    a1 = -2 * c,
    a2 = 1 - a;
  let x1 = 0,
    x2 = 0,
    y1 = 0,
    y2 = 0;
  return (x) => {
    const y = (b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
    x2 = x1;
    x1 = x;
    y2 = y1;
    y1 = y;
    return y;
  };
}

function clap(start, gain = 0.22, gated = false) {
  const s0 = Math.floor(start * SR);
  const len = Math.floor((gated ? 0.28 : 0.35) * SR);
  const bp = makeBiquad("bp", gated ? 1800 : 1400, 0.9);
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    const bursts = t < 0.03 ? 0.6 + 0.4 * Math.sin(TAU * 100 * t) ** 2 : 1;
    const env = gated ? (t < 0.22 ? 1 : Math.max(0, 1 - (t - 0.22) / 0.06)) * Math.exp(-t * 3) : Math.exp(-t * 16);
    const v = bp(noise()) * env * bursts * gain * 3;
    put(s0 + k, v * 0.9, v, gated ? 0.8 : 0.5);
  }
}

function hat(start, gain = 0.05, open = false) {
  const s0 = Math.floor(start * SR);
  const len = Math.floor((open ? 0.25 : 0.06) * SR);
  const hp = makeBiquad("hp", 8000, 0.7);
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    const v = hp(noise()) * Math.exp(-t * (open ? 14 : 70)) * gain;
    put(s0 + k, v * 0.8, v, 0.15);
  }
}

function riser(start, dur, gain = 0.12) {
  const s0 = Math.floor(start * SR);
  const len = Math.floor(dur * SR);
  let lp = 0,
    ph = 0;
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    const p = t / dur;
    const fc = 200 + 7000 * p * p;
    lp += (1 - Math.exp((-TAU * fc) / SR)) * (noise() - lp);
    ph += (TAU * (180 + 700 * p * p)) / SR;
    const v = (lp * 0.9 + Math.sin(ph) * 0.15) * p * p * gain;
    put(s0 + k, v, v, 0.6);
  }
}

function whoosh(peak, gain = 0.06) {
  const dur = 0.7;
  const s0 = Math.floor((peak - dur * 0.55) * SR);
  const len = Math.floor(dur * SR);
  let hi = 0,
    lo = 0;
  for (let k = 0; k < len; k++) {
    const p = k / len;
    const fc = 400 + 3000 * Math.sin(Math.PI * p);
    const x = noise();
    hi += (1 - Math.exp((-TAU * fc * 1.6) / SR)) * (x - hi);
    lo += (1 - Math.exp((-TAU * fc * 0.6) / SR)) * (x - lo);
    const v = (hi - lo) * Math.sin(Math.PI * p) ** 2 * gain * 3;
    const [gl, gr] = panGains(-0.6 + 1.2 * p);
    put(s0 + k, v * gl, v * gr, 0.4);
  }
}

function impact(start, gain = 0.42) {
  const s0 = Math.floor(start * SR);
  const len = Math.floor(2.2 * SR);
  let ph = 0,
    lp = 0;
  for (let k = 0; k < len; k++) {
    const t = k / SR;
    ph += (TAU * (30 + 60 * Math.exp(-t * 10))) / SR;
    lp += 0.06 * (noise() - lp);
    const v = (Math.sin(ph) * Math.exp(-t * 2.2) + lp * 1.4 * Math.exp(-t * 4)) * gain;
    put(s0 + k, v, v, 0.7);
  }
}

// ---------- Bố cục ----------
const nBars = Math.ceil(DUR / BAR);
const lastMarker = MARKERS.length > 1 ? MARKERS[MARKERS.length - 1] : DUR - Math.min(4, DUR * 0.2);
const introEnd = MARKERS.length > 1 ? Math.min(MARKERS[1], BAR * 2) : BAR * 2;
const outroBar = Math.floor(lastMarker / BAR + 1e-6);
const sectionOf = (b) => {
  const t0 = b * BAR;
  if (t0 < introEnd - 1e-6) return "intro";
  if (b >= outroBar) return "outro";
  if (b >= outroBar - 2 && DUR > 12) return "build";
  return "main";
};
const sw = (e) => (e % 2 ? P.swing * BEAT : 0); // swing nốt móc đơn lẻ

for (let b = 0; b < nBars; b++) {
  const t0 = b * BAR;
  const ch = chord(P.prog[b % P.prog.length]);
  const sec = sectionOf(b);
  const k = b - (outroBar - 2);

  // PAD
  pad(ch.pad, t0, BAR, P.pad * (sec === "intro" ? 0.85 : sec === "outro" ? 1.15 : 1), 0.5, PRESET === "cinematic" || PRESET === "ambient" ? 1.4 : 0.7, PRESET === "synthwave" || PRESET === "cinematic");

  // BASS
  if (P.bass === "drone") bass(ch.bass - 12, t0, BAR, 0.16, "drone");
  else if (sec === "intro") bass(ch.bass, t0, BAR, 0.08);
  else if (sec === "outro") bass(ch.bass, t0, BAR * (b === outroBar ? 1.5 : 1), 0.15);
  else if (sec === "build") bass(ch.bass, t0, BAR * 0.95, 0.12);
  else if (P.bass === "pulse8") for (let e = 0; e < 8; e++) bass(ch.bass, t0 + e * (BEAT / 2), BEAT / 2 - 0.04, 0.11);
  else if (P.bass === "offbeat") for (let e = 0; e < 4; e++) bass(ch.bass, t0 + e * BEAT + BEAT / 2, BEAT / 2 - 0.05, 0.14);
  else if (P.bass === "bounce") [0, 1.5, 2, 3].forEach((q, i) => bass(ch.bass + (i === 2 ? 12 : 0), t0 + q * BEAT, BEAT * 0.45, 0.13));
  else if (P.bass === "sustain") bass(ch.bass, t0, BAR * 0.98, 0.12, "sub");
  else {
    bass(ch.bass, t0, BEAT * 1.5);
    bass(ch.bass, t0 + BEAT * 1.5, BEAT * 0.5);
    bass(ch.bass + 12, t0 + BEAT * 2.5, BEAT * 0.5, 0.1);
    bass(ch.bass, t0 + BEAT * 3, BEAT);
  }

  // KEYS / GIAI ĐIỆU NỀN
  const busy = sec === "main" || sec === "build";
  const vel = (e) => (e % 2 === 0 ? 0.9 : 0.6) * (sec === "build" ? 0.75 : 1);
  const pan = (i) => (i % 2 ? 0.35 : -0.35);
  const pat8 = [0, 2, 1, 3, 2, 1, 3, 2];
  if (sec === "outro") ch.arp.forEach((m, i) => (P.keys.startsWith("bell") ? bell : piano)(m, t0 + i * 0.06, 0.9, -0.3 + i * 0.2, 0.075));
  else if (!busy || P.keys === "piano-slow" || P.keys === "bell-slow") {
    [0, 2].forEach((q) => {
      const m = ch.arp[q === 0 ? 0 : 2] - 12;
      if (P.keys === "bell-slow" || P.keys === "bell") bell(m + 12, t0 + q * BEAT, 0.7, q ? 0.3 : -0.3, 0.05, 1.5);
      else if (P.keys === "epiano") epiano(m, t0 + q * BEAT, 0.8, q ? 0.3 : -0.3);
      else piano(m, t0 + q * BEAT, 0.8, q ? 0.3 : -0.3, 0.09, 0.4, PRESET === "cinematic" ? 1.6 : 1);
    });
  } else if (P.keys === "piano") pat8.forEach((idx, e) => piano(ch.arp[idx], t0 + e * (BEAT / 2), vel(e), pan(idx), 0.075));
  else if (P.keys === "pluck") pat8.forEach((idx, e) => pluck(ch.arp[idx], t0 + e * (BEAT / 2), vel(e), pan(idx), 0.06));
  else if (P.keys === "arp16") for (let e = 0; e < 16; e++) pluck(ch.arp[[0, 1, 2, 3, 2, 1][e % 6]], t0 + e * (BEAT / 4), e % 4 === 0 ? 1 : 0.6, pan(e), 0.05, true, 0.25);
  else if (P.keys === "saw-arp") for (let e = 0; e < 8; e++) pluck(ch.arp[[0, 1, 2, 3][e % 4]], t0 + e * (BEAT / 2), vel(e), pan(e), 0.06, true, 0.4);
  else if (P.keys === "stab") [0.5, 1.5, 2.5, 3.5].forEach((q) => ch.arp.slice(0, 3).forEach((m, i) => pluck(m, t0 + q * BEAT, 0.8, -0.3 + i * 0.3, 0.045, true, 0.3)));
  else if (P.keys === "bell") pat8.forEach((idx, e) => e % 2 === 0 && bell(ch.arp[idx], t0 + e * (BEAT / 2) + sw(e), vel(e), pan(idx), 0.05));
  else if (P.keys === "epiano") [0, 1.5, 2.5].forEach((q, i) => ch.pad.forEach((m, j) => epiano(m + 12, t0 + q * BEAT + j * 0.012 + sw(i), 0.55, -0.2 + j * 0.15, 0.045)));

  // TRỐNG
  const D = P.drums;
  if (D === "none") continue;
  if (sec === "intro") {
    if (D !== "taiko" && D !== "lofi") for (let e = 0; e < 8; e++) hat(t0 + e * (BEAT / 2), e % 2 ? 0.022 : 0.012);
    continue;
  }
  if (sec === "outro") {
    if (b === outroBar) kick(t0, D === "taiko" ? 0.6 : 0.55, D === "taiko");
    if (b === outroBar + 1) kick(t0 + 2 * BEAT, 0.3, D === "taiko");
    continue;
  }
  if (sec === "build") {
    for (let e = 0; e < 4; e++) kick(t0 + e * BEAT, (0.3 + k * 0.06) * (D === "lofi" ? 0.6 : 1), D === "taiko");
    for (let e = 0; e < 8; e++) hat(t0 + e * (BEAT / 2), e % 2 ? 0.045 : 0.03);
    if (k >= 1) for (let e = 0; e < 16; e++) clap(t0 + e * (BEAT / 4), (0.05 + 0.15 * (e / 15)) * (D === "lofi" ? 0.5 : 1));
    continue;
  }
  // main
  if (D === "four") {
    for (let e = 0; e < 4; e++) kick(t0 + e * BEAT, 0.5);
    clap(t0 + BEAT, 0.2);
    clap(t0 + 3 * BEAT, 0.2);
    for (let e = 0; e < 4; e++) hat(t0 + e * BEAT + BEAT / 2, 0.06, e === 3 && b % 2 === 1);
    for (let e = 0; e < 16; e++) if (e % 4 !== 2) hat(t0 + e * (BEAT / 4), 0.015);
  } else if (D === "pop" || D === "soft") {
    const g = D === "soft" ? 0.85 : 1;
    kick(t0, 0.5 * g);
    kick(t0 + 2 * BEAT, 0.45 * g);
    if (b % 2 === 0) kick(t0 + 2.5 * BEAT, 0.3 * g);
    clap(t0 + BEAT, 0.2 * g);
    clap(t0 + 3 * BEAT, 0.2 * g);
    for (let e = 0; e < 8; e++) hat(t0 + e * (BEAT / 2) + sw(e), e % 2 ? 0.05 : 0.025);
  } else if (D === "lofi") {
    kick(t0, 0.38);
    kick(t0 + 1.75 * BEAT, 0.25);
    kick(t0 + 2.5 * BEAT, 0.3);
    clap(t0 + BEAT, 0.12);
    clap(t0 + 3 * BEAT, 0.12);
    for (let e = 0; e < 8; e++) hat(t0 + e * (BEAT / 2) + sw(e), e % 2 ? 0.03 : 0.018);
  } else if (D === "taiko") {
    kick(t0, 0.5, true);
    kick(t0 + 1.5 * BEAT, 0.3, true);
    kick(t0 + 2 * BEAT, 0.42, true);
    if (b % 2) [3, 3.25, 3.5, 3.75].forEach((q, i) => kick(t0 + q * BEAT, 0.18 + i * 0.05, true));
  } else if (D === "gated") {
    kick(t0, 0.5);
    kick(t0 + 2 * BEAT, 0.5);
    clap(t0 + BEAT, 0.18, true);
    clap(t0 + 3 * BEAT, 0.18, true);
    for (let e = 0; e < 8; e++) hat(t0 + e * (BEAT / 2), e % 2 ? 0.04 : 0.02);
  }
}

// ---------- Hiệu ứng khớp cảnh ----------
MARKERS.filter((m) => m > 0.5 && m < DUR - 0.5).forEach((m) => whoosh(m + 0.25, 0.05));
IMPACTS.filter((t) => t < DUR).forEach((t) => {
  riser(Math.max(0, t - BAR), Math.min(BAR, t), 0.11);
  impact(t, 0.42);
});

// ---------- Vinyl crackle (lofi) ----------
if (P.crackle) for (let n = 0; n < N; n++) if (rnd() < 0.0004) put(n, noise() * 0.08, noise() * 0.08);

// ---------- Reverb (Freeverb rút gọn) ----------
function reverb(input, offset) {
  const combs = [1116, 1188, 1277, 1356, 1422, 1491].map((d) => ({ buf: new Float32Array(d + offset), i: 0, lp: 0 }));
  const aps = [556, 441, 341].map((d) => ({ buf: new Float32Array(d + offset), i: 0 }));
  const out = new Float32Array(N);
  for (let n = 0; n < N; n++) {
    const x = input[n] * 0.015;
    let y = 0;
    for (const c of combs) {
      const o = c.buf[c.i];
      c.lp = o * 0.75 + c.lp * 0.25;
      c.buf[c.i] = x + c.lp * 0.86;
      c.i = (c.i + 1) % c.buf.length;
      y += o;
    }
    for (const a of aps) {
      const o = a.buf[a.i];
      const v = y + o * 0.5;
      a.buf[a.i] = v;
      a.i = (a.i + 1) % a.buf.length;
      y = o - v * 0.5;
    }
    out[n] = y;
  }
  return out;
}
const rvL = reverb(sendL, 0);
const rvR = reverb(sendR, 23);
let lpL = 0,
  lpR = 0;
for (let n = 0; n < N; n++) {
  L[n] += rvL[n] * P.rev;
  R[n] += rvR[n] * P.rev;
  if (P.lowpass) {
    lpL += P.lowpass * (L[n] - lpL);
    lpR += P.lowpass * (R[n] - lpR);
    L[n] = lpL;
    R[n] = lpR;
  }
}

// ---------- Master ----------
for (let n = 0; n < N; n++) {
  const t = n / SR;
  const g = Math.min(1, t / 0.05) * (t > DUR - 2.5 ? Math.max(0, (DUR - t) / 2.5) : 1);
  L[n] *= g;
  R[n] *= g;
}
let peak = 0;
for (let n = 0; n < N; n++) peak = Math.max(peak, Math.abs(L[n]), Math.abs(R[n]));
const norm = 0.95 / (peak || 1);
const pcm = Buffer.alloc(44 + N * 4);
pcm.write("RIFF", 0);
pcm.writeUInt32LE(36 + N * 4, 4);
pcm.write("WAVE", 8);
pcm.write("fmt ", 12);
pcm.writeUInt32LE(16, 16);
pcm.writeUInt16LE(1, 20);
pcm.writeUInt16LE(2, 22);
pcm.writeUInt32LE(SR, 24);
pcm.writeUInt32LE(SR * 4, 28);
pcm.writeUInt16LE(4, 32);
pcm.writeUInt16LE(16, 34);
pcm.write("data", 36);
pcm.writeUInt32LE(N * 4, 40);
const sc = (x) => Math.tanh(x * norm * 1.15) / Math.tanh(1.15);
for (let n = 0; n < N; n++) {
  pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, sc(L[n]))) * 32767), 44 + n * 4);
  pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, sc(R[n]))) * 32767), 46 + n * 4);
}
mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, pcm);
console.log(`OK ${OUT}  preset=${PRESET} bpm=${BPM} ${DUR}s bars=${nBars} outroAt=${(outroBar * BAR).toFixed(1)}s peak=${peak.toFixed(3)}`);
