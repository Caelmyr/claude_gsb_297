/* Lightweight SVG charts (bar, horizontal bar / tornado, line). No external deps. */

function _svg(container, width, height) {
  container.innerHTML = '';
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  svg.style.fontFamily = 'system-ui, sans-serif';
  container.appendChild(svg);
  return svg;
}

function _axis(svg, x, y, label, opts = {}) {
  const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  t.setAttribute('x', x); t.setAttribute('y', y);
  t.setAttribute('font-size', opts.size || 10);
  t.setAttribute('fill', '#6b7280');
  t.setAttribute(opts.anchor || 'text-anchor', opts.anchor || 'middle');
  t.textContent = label;
  svg.appendChild(t);
}

/* Vertical bar chart: labels along x, numeric values along y. */
function barChart(container, { labels, values, height = 240, color = '#4e79a7', valueFmt = v => fmt(v) }) {
  const W = 560, H = height, padL = 46, padB = 44, padT = 16;
  const max = Math.max(1, ...values.map(v => Math.abs(v)));
  const innerW = W - padL - 12, innerH = H - padT - padB;
  const svg = _svg(container, W, H);

  const bw = Math.min(52, innerW / labels.length * 0.6);
  const stepX = innerW / labels.length;

  // y axis
  for (let i = 0; i <= 4; i++) {
    const val = max * i / 4;
    const y = padT + innerH - innerH * i / 4;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', padL); line.setAttribute('y1', y);
    line.setAttribute('x2', W - 12); line.setAttribute('y2', y);
    line.setAttribute('stroke', '#eef0f3');
    svg.appendChild(line);
    _axis(svg, padL - 6, y + 3, valueFmt(val), { anchor: 'end' });
  }

  labels.forEach((lb, i) => {
    const v = values[i];
    const x = padL + i * stepX + (stepX - bw) / 2;
    const barH = innerH * Math.abs(v) / max;
    const y = padT + innerH - barH;
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', x); rect.setAttribute('y', y);
    rect.setAttribute('width', bw); rect.setAttribute('height', Math.max(0, barH));
    rect.setAttribute('rx', 3); rect.setAttribute('fill', color);
    svg.appendChild(rect);
    _axis(svg, x + bw / 2, y - 5, valueFmt(v));
    _axis(svg, x + bw / 2, padT + innerH + 16, lb, { size: 10 });
  });
  return svg;
}

/* Horizontal bar chart (tornado for +/- deltas): zero line in the middle. */
function hBarChart(container, { labels, values, height = 260, valueFmt = v => fmt(v) }) {
  const W = 640, H = height, padL = 130, padR = 60, padT = 16, padB = 12;
  const max = Math.max(1, ...values.map(v => Math.abs(v)));
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const svg = _svg(container, W, H);
  const centerX = padL + innerW / 2;

  // zero line
  const zl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  zl.setAttribute('x1', centerX); zl.setAttribute('y1', padT);
  zl.setAttribute('x2', centerX); zl.setAttribute('y2', padT + innerH);
  zl.setAttribute('stroke', '#9ca3af'); zl.setAttribute('stroke-width', 1);
  svg.appendChild(zl);

  const rowH = Math.min(46, innerH / labels.length);
  labels.forEach((lb, i) => {
    const v = values[i];
    const y = padT + i * rowH;
    const barW = innerW / 2 * Math.abs(v) / max;
    const x = v >= 0 ? centerX : centerX - barW;
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', x); rect.setAttribute('y', y + 4);
    rect.setAttribute('width', Math.max(1, barW)); rect.setAttribute('height', Math.max(8, rowH - 10));
    rect.setAttribute('rx', 3);
    rect.setAttribute('fill', v >= 0 ? '#e15759' : '#4e79a7');
    svg.appendChild(rect);
    _axis(svg, padL - 8, y + rowH / 2 + 4, lb, { anchor: 'end', size: 11 });
    const valTxt = valueFmt(v);
    _axis(svg, v >= 0 ? x + barW + 5 : x - 5, y + rowH / 2 + 4, valTxt, { anchor: v >= 0 ? 'start' : 'end', size: 11 });
  });
  return svg;
}

/* Scatter chart for Pareto fronts.
   series: [{ name, color, points: [{x, y, label, payload}] }]
   onPoint(payload, point, series) is invoked when a marker is clicked.
   Rendering is protected against front explosion: each series is capped at
   maxPoints (server-side downsampling is the primary guard; this is a second
   net) and a note is shown when capping kicks in. */
function scatterChart(container, { series, xLabel = 'x', yLabel = 'y',
  height = 420, maxPoints = 500, onPoint = null, valueFmt = v => fmt(v) }) {
  const W = 640, H = height, padL = 64, padR = 18, padT = 18, padB = 52;
  let all = series.flatMap(s => s.points);
  let cappedNote = '';
  series = series.map(s => {
    if (s.points.length > maxPoints) {
      const pts = [];
      const seen = new Set();
      for (let i = 0; i < maxPoints; i++) {
        const idx = Math.round(i * (s.points.length - 1) / (maxPoints - 1));
        if (!seen.has(idx)) { seen.add(idx); pts.push(s.points[idx]); }
      }
      cappedNote = `（仅绘制每族前 ${maxPoints} 点；完整数据已在求解端降采样）`;
      return { ...s, points: pts };
    }
    return s;
  });
  all = series.flatMap(s => s.points);
  const svg = _svg(container, W, H);
  if (!all.length) {
    _axis(svg, W / 2, H / 2, '暂无前沿数据', { size: 13 });
    return svg;
  }
  const xs = all.map(p => p.x), ys = all.map(p => p.y);
  let minX = Math.min(...xs), maxX = Math.max(...xs);
  let minY = Math.min(...ys), maxY = Math.max(...ys);
  if (minX === maxX) { minX -= 1; maxX += 1; }
  if (minY === maxY) { minY -= 1; maxY += 1; }
  const padX = (maxX - minX) * 0.08, padY = (maxY - minY) * 0.08;
  minX -= padX; maxX += padX; minY -= padY; maxY += padY;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const X = v => padL + innerW * (v - minX) / (maxX - minX);
  const Y = v => padT + innerH - innerH * (v - minY) / (maxY - minY);

  for (let i = 0; i <= 4; i++) {
    const vx = minX + (maxX - minX) * i / 4;
    const vy = minY + (maxY - minY) * i / 4;
    const gx = X(vx), gy = Y(vy);
    let lx = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    lx.setAttribute('x1', gx); lx.setAttribute('y1', padT);
    lx.setAttribute('x2', gx); lx.setAttribute('y2', padT + innerH);
    lx.setAttribute('stroke', '#f3f4f6'); svg.appendChild(lx);
    let ly = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    ly.setAttribute('x1', padL); ly.setAttribute('y1', gy);
    ly.setAttribute('x2', padL + innerW); ly.setAttribute('y2', gy);
    ly.setAttribute('stroke', '#f3f4f6'); svg.appendChild(ly);
    _axis(svg, gx, padT + innerH + 14, valueFmt(vx), { size: 9 });
    _axis(svg, padL - 8, gy + 3, valueFmt(vy), { anchor: 'end', size: 9 });
  }
  // axes
  const ax = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ax.setAttribute('x1', padL); ax.setAttribute('y1', padT + innerH);
  ax.setAttribute('x2', padL + innerW); ax.setAttribute('y2', padT + innerH);
  ax.setAttribute('stroke', '#9ca3af'); svg.appendChild(ax);
  const ay = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ay.setAttribute('x1', padL); ay.setAttribute('y1', padT);
  ay.setAttribute('x2', padL); ay.setAttribute('y2', padT + innerH);
  ay.setAttribute('stroke', '#9ca3af'); svg.appendChild(ay);

  // connect points within each series to visualise the front line
  series.forEach(s => {
    const ordered = [...s.points].sort((a, b) => a.x - b.x);
    if (ordered.length > 1) {
      let d = '';
      ordered.forEach((p, i) => { d += (i ? 'L' : 'M') + X(p.x) + ' ' + Y(p.y) + ' '; });
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d); path.setAttribute('fill', 'none');
      path.setAttribute('stroke', s.color || '#4e79a7');
      path.setAttribute('stroke-width', 1.5); path.setAttribute('stroke-dasharray', '4 3');
      path.setAttribute('opacity', 0.55);
      svg.appendChild(path);
    }
  });

  // points
  series.forEach(s => {
    s.points.forEach(p => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('cx', X(p.x)); c.setAttribute('cy', Y(p.y));
      c.setAttribute('r', p.r || 4.5);
      c.setAttribute('fill', s.color || '#4e79a7');
      c.setAttribute('stroke', '#fff'); c.setAttribute('stroke-width', 1.5);
      c.style.cursor = onPoint ? 'pointer' : 'default';
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = (p.label != null ? p.label + '  ' : '') +
        `${xLabel}=${valueFmt(p.x)}, ${yLabel}=${valueFmt(p.y)}`;
      c.appendChild(title);
      if (onPoint) c.addEventListener('click', () => onPoint(p.payload, p, s));
      svg.appendChild(c);
    });
  });

  _axis(svg, padL + innerW / 2, H - 8, xLabel, { size: 12 });
  const yt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  yt.setAttribute('transform', `translate(16 ${padT + innerH / 2}) rotate(-90)`);
  yt.setAttribute('text-anchor', 'middle'); yt.setAttribute('font-size', 12);
  yt.setAttribute('fill', '#374151'); yt.textContent = yLabel;
  svg.appendChild(yt);

  if (series.length > 1) {
    let hx = padL;
    series.forEach(s => {
      const sp = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      sp.setAttribute('cx', hx + 5); sp.setAttribute('cy', padT - 4); sp.setAttribute('r', 4.5);
      sp.setAttribute('fill', s.color || '#4e79a7'); svg.appendChild(sp);
      _axis(svg, hx + 12, padT, s.name, { anchor: 'start', size: 10 });
      hx += 22 + Math.max(60, s.name.length * 7);
    });
  }
  if (cappedNote) _axis(svg, padL + innerW, H - 8, cappedNote, { anchor: 'end', size: 9 });
  return svg;
}

/* Simple line chart with dots. */
function lineChart(container, { labels, values, height = 240, color = '#2563eb', valueFmt = v => fmt(v) }) {
  const W = 560, H = height, padL = 46, padB = 40, padT = 16;
  const min = Math.min(...values), max = Math.max(...values);
  const span = (max - min) || 1;
  const innerW = W - padL - 12, innerH = H - padT - padB;
  const svg = _svg(container, W, H);
  const x = i => padL + (labels.length === 1 ? innerW / 2 : innerW * i / (labels.length - 1));
  const y = v => padT + innerH - innerH * (v - min) / span;

  for (let i = 0; i <= 4; i++) {
    const val = min + span * i / 4;
    const yy = y(val);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', padL); line.setAttribute('y1', yy);
    line.setAttribute('x2', W - 12); line.setAttribute('y2', yy);
    line.setAttribute('stroke', '#eef0f3');
    svg.appendChild(line);
    _axis(svg, padL - 6, yy + 3, valueFmt(val), { anchor: 'end' });
  }

  let d = '';
  values.forEach((v, i) => { d += (i ? 'L' : 'M') + x(i) + ' ' + y(v) + ' '; });
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', d);
  path.setAttribute('fill', 'none'); path.setAttribute('stroke', color);
  path.setAttribute('stroke-width', 2);
  svg.appendChild(path);

  values.forEach((v, i) => {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', x(i)); c.setAttribute('cy', y(v)); c.setAttribute('r', 3.5);
    c.setAttribute('fill', color);
    svg.appendChild(c);
    _axis(svg, x(i), y(v) - 7, valueFmt(v));
    _axis(svg, x(i), padT + innerH + 16, labels[i], { size: 10 });
  });
  return svg;
}
