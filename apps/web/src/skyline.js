// Hero backdrop: a 3D city silhouette whose edges glow, with the light shifting
// slowly along them — the visual thesis of the whole site (a city going dark,
// then lit again).
//
// Canvas rather than SVG because the edge highlight is a per-frame gradient
// sweep, and hand-authored path data for a few hundred edges would be
// unmaintainable. Isometric projection, no WebGL, no dependencies.

const DPR_CAP = 2;

/** Deterministic PRNG so the skyline is identical on every load. */
function mulberry32(seed) {
  return function rand() {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Isometric projection: world (x, y, z) -> screen (px, py). */
function project(x, y, z, s) {
  return {
    x: (x - y) * s.cos,
    y: (x + y) * s.sin - z * s.height,
  };
}

class Building {
  constructor(gx, gy, w, d, h, rand) {
    this.gx = gx; this.gy = gy;
    this.w = w; this.d = d; this.h = h;
    // Each building gets its own phase so the glow travels across the city
    // rather than pulsing everywhere at once.
    this.phase = rand() * Math.PI * 2;
    this.speed = 0.35 + rand() * 0.5;
    this.depth = gx + gy;
  }
}

export class Skyline {
  constructor(canvas, { seed = 20260823 } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.seed = seed;
    this.buildings = [];
    this.raf = null;
    this.t = 0;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this._onResize = () => this.resize();
  }

  build() {
    const rand = mulberry32(this.seed);
    const COLS = 14, ROWS = 14;
    const out = [];
    for (let gx = 0; gx < COLS; gx++) {
      for (let gy = 0; gy < ROWS; gy++) {
        // Leave gaps so it reads as a city, not a solid block.
        if (rand() < 0.28) continue;
        // Taller towards the middle of the grid — a downtown core.
        const cx = (gx - COLS / 2) / (COLS / 2);
        const cy = (gy - ROWS / 2) / (ROWS / 2);
        const core = Math.max(0, 1 - Math.sqrt(cx * cx + cy * cy));
        const h = 0.22 + Math.pow(core, 1.5) * 3.4 * (0.35 + rand() * 1.05);
        const w = 0.56 + rand() * 0.22;
        const d = 0.56 + rand() * 0.22;
        out.push(new Building(gx, gy, w, d, h, rand));
      }
    }
    // Painter's algorithm: far buildings first.
    out.sort((a, b) => a.depth - b.depth);
    this.buildings = out;
  }

  resize() {
    const { canvas } = this;
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.W = rect.width;
    this.H = rect.height;
  }

  start() {
    this.build();
    this.resize();
    window.addEventListener('resize', this._onResize, { passive: true });
    if (this.reduced) { this.draw(0); return; }
    const loop = (ms) => {
      this.draw(ms / 1000);
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    window.removeEventListener('resize', this._onResize);
  }

  draw(time) {
    const { ctx, W, H } = this;
    if (!W || !H) return;
    this.t = time;

    ctx.clearRect(0, 0, W, H);

    // Scale the city to the viewport; anchor it low so the copy sits above it.
    const unit = Math.max(30, Math.min(W / 13, 82));
    const s = { cos: unit * 0.92, sin: unit * 0.46, height: unit * 0.86 };
    const originX = W * 0.5;
    const originY = H * 1.16;

    for (const b of this.buildings) {
      this.drawBuilding(b, s, originX, originY, unit);
    }
  }

  drawBuilding(b, s, ox, oy, unit) {
    const { ctx } = this;
    const x0 = b.gx - 7, y0 = b.gy - 7;
    const x1 = x0 + b.w, y1 = y0 + b.d;

    const p = (x, y, z) => {
      const q = project(x, y, z, s);
      return { x: ox + q.x, y: oy + q.y };
    };

    // Six visible corners of the box.
    const tA = p(x0, y0, b.h), tB = p(x1, y0, b.h);
    const tC = p(x1, y1, b.h), tD = p(x0, y1, b.h);
    const bB = p(x1, y0, 0), bC = p(x1, y1, 0), bD = p(x0, y1, 0);

    // Cull anything fully off-screen.
    if (tC.x < -140 || tA.x > this.W + 140 || tA.y > this.H + 140) return;

    // Faces: near-black, subtly separated so the form reads.
    const face = (pts, fill) => {
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
    };
    face([tA, tB, tC, tD], '#0f0f11');           // roof
    face([tD, tC, bC, bD], '#08080a');           // right wall
    face([tB, tC, bC, bB], '#050506');           // front wall

    // The travelling highlight: a 0..1 wave per building, offset by phase.
    const wave = 0.5 + 0.5 * Math.sin(this.t * b.speed + b.phase - b.depth * 0.22);
    const glow = Math.pow(wave, 2.4);
    const alpha = 0.16 + glow * 0.84;

    ctx.lineWidth = 1;
    ctx.strokeStyle = `rgba(255,255,255,${alpha.toFixed(3)})`;
    ctx.shadowBlur = 4 + glow * 16;
    ctx.shadowColor = `rgba(220,238,255,${(glow * 0.85).toFixed(3)})`;

    // Roof outline plus the two vertical corners: the silhouette's defining edges.
    ctx.beginPath();
    ctx.moveTo(tA.x, tA.y);
    ctx.lineTo(tB.x, tB.y);
    ctx.lineTo(tC.x, tC.y);
    ctx.lineTo(tD.x, tD.y);
    ctx.closePath();
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(tB.x, tB.y); ctx.lineTo(bB.x, bB.y);
    ctx.moveTo(tC.x, tC.y); ctx.lineTo(bC.x, bC.y);
    ctx.moveTo(tD.x, tD.y); ctx.lineTo(bD.x, bD.y);
    ctx.stroke();

    ctx.shadowBlur = 0;
  }
}

/** Mount on a <canvas>; returns a stop() handle. */
export function mountSkyline(canvas) {
  if (!canvas || !canvas.getContext) return { stop() {} };
  const sky = new Skyline(canvas);
  sky.start();
  return sky;
}
