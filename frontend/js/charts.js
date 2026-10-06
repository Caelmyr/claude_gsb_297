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

/* Scatter plot for Pareto fronts: points sorted by x and joined by a line.
   If there are more than maxPoints points they are evenly down-sampled so a
   huge front can never freeze the page; a note reports the sampling.  Points
   are clickable (onSelect) and carry a native <title> tooltip. */
function scatterChart(container, { points, xLabel = '', yLabel = '', height = 340,
                                   maxPoints = 400, onSelect = null, selectedId = null,
                                   color = '#2563eb', valueFmt = v => fmt(v) }) {
  container.innerHTML = '';
  if (!points.length) {
    container.innerHTML = '<div class="empty">暂无数据。</div>';
    return null;
  }
  const sorted = points.slice().sort((a, b) => a.x - b.x);
  let pts = sorted;
  let sampled = false;
  if (sorted.length > maxPoints) {
    sampled = true;
    pts = [];
    const step = sorted.length / maxPoints;
    for (let i = 0; i < maxPoints; i++) pts.push(sorted[Math.floor(i * step)]);
    // always keep the extremes of the front
    if (pts[pts.length - 1] !== sorted[sorted.length - 1]) pts.push(sorted[sorted.length - 1]);
  }

  const W = 640, H = height, padL = 58, padB = 46, padT = 18, padR = 18;
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const xPad = (xMax - xMin) * 0.05 || 1, yPad = (yMax - yMin) * 0.08 || 1;
  const x0 = xMin - xPad, x1 = xMax + xPad, y0 = yMin - yPad, y1 = yMax + yPad;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const X = v => padL + (v - x0) / (x1 - x0) * innerW;
  const Y = v => padT + innerH - (v - y0) / (y1 - y0) * innerH;

  const svg = _svg(container, W, H);

  // gridlines + tick labels
  for (let i = 0; i <= 4; i++) {
    const xv = x0 + (x1 - x0) * i / 4;
    const xx = X(xv);
    const vline = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    vline.setAttribute('x1', xx); vline.setAttribute('y1', padT);
    vline.setAttribute('x2', xx); vline.setAttribute('y2', padT + innerH);
    vline.setAttribute('stroke', '#f3f4f6');
    svg.appendChild(vline);
    _axis(svg, xx, padT + innerH + 16, valueFmt(xv));

    const yv = y0 + (y1 - y0) * i / 4;
    const yy = Y(yv);
    const hline = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    hline.setAttribute('x1', padL); hline.setAttribute('y1', yy);
    hline.setAttribute('x2', W - padR); hline.setAttribute('y2', yy);
    hline.setAttribute('stroke', '#eef0f3');
    svg.appendChild(hline);
    _axis(svg, padL - 6, yy + 3, valueFmt(yv), { anchor: 'end' });
  }

  // front polyline
  if (pts.length > 1) {
    let d = '';
    pts.forEach((p, i) => { d += (i ? 'L' : 'M') + X(p.x) + ' ' + Y(p.y) + ' '; });
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-opacity', 0.45);
    path.setAttribute('stroke-width', 1.5);
    svg.appendChild(path);
  }

  pts.forEach(p => {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', X(p.x)); c.setAttribute('cy', Y(p.y));
    const selected = selectedId != null && p.id === selectedId;
    c.setAttribute('r', selected ? 6.5 : 4.5);
    c.setAttribute('fill', selected ? '#dc2626' : color);
    c.setAttribute('stroke', '#fff');
    c.setAttribute('stroke-width', 1.2);
    if (onSelect) c.style.cursor = 'pointer';
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    title.textContent = p.label || `${valueFmt(p.x)}, ${valueFmt(p.y)}`;
    c.appendChild(title);
    if (onSelect) c.addEventListener('click', () => onSelect(p));
    svg.appendChild(c);
  });

  // axis captions
  _axis(svg, padL + innerW / 2, H - 6, xLabel, { size: 11 });
  const yl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  yl.setAttribute('x', 14); yl.setAttribute('y', padT + innerH / 2);
  yl.setAttribute('font-size', 11); yl.setAttribute('fill', '#6b7280');
  yl.setAttribute('text-anchor', 'middle');
  yl.setAttribute('transform', `rotate(-90 14 ${padT + innerH / 2})`);
  yl.textContent = yLabel;
  svg.appendChild(yl);

  if (sampled) {
    const note = document.createElement('div');
    note.className = 'small muted';
    note.textContent = `前沿共 ${sorted.length} 个点，为保持流畅已均匀抽样显示 ${pts.length} 个（表格中可查看全部）。`;
    container.appendChild(note);
  }
  return svg;
}
