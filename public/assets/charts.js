'use strict';

function nreChart(canvas, series, opts) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  const pad = { l: 40, r: 12, t: 10, b: 22 };
  ctx.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.documentElement);
  const muted = css.getPropertyValue('--text-muted').trim() || '#9ca3af';
  const accent = css.getPropertyValue('--accent').trim() || '#00b4d8';
  const grid = css.getPropertyValue('--border').trim() || '#1e2433';

  ctx.fillStyle = css.getPropertyValue('--bg-card').trim() || '#303030';
  ctx.fillRect(0, 0, w, h);

  const colors = (opts && opts.colors) || [accent, '#22c55e', '#f59e0b'];
  const all = [];
  series.forEach(s => (s.points || []).forEach(p => all.push(p.y)));
  let min = 0;
  let max = Math.max(1, ...(all.length ? all : [1]));
  if (opts && opts.max != null) max = opts.max;
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  ctx.fillStyle = muted;
  ctx.font = '11px Consolas, monospace';
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (innerH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(w - pad.r, y);
    ctx.stroke();
    const val = max - ((max - min) * i) / 4;
    ctx.fillText(String(Math.round(val)), 4, y + 4);
  }

  series.forEach((s, si) => {
    const pts = s.points || [];
    if (pts.length < 2) return;
    ctx.strokeStyle = colors[si % colors.length];
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const x = pad.l + (i / Math.max(1, pts.length - 1)) * innerW;
      const y = pad.t + innerH - ((p.y - min) / (max - min)) * innerH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function nreToPoints(rows, key) {
  return (rows || []).map(r => ({
    t: r.ts,
    y: r[key] == null ? 0 : Number(r[key]),
  }));
}
