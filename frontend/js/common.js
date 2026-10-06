/* Shared frontend helpers: API access, navigation, formatting, UI primitives. */

const API = '/api';

/* ---- constants (kept in sync with backend/models.py) ---------------- */
const RESOURCE_TYPES = ['personnel', 'equipment', 'time'];
const OBJECTIVE_TYPES = ['makespan', 'total_completion', 'weighted_completion',
  'tardiness', 'cost', 'overtime_cost', 'custom'];
const HARD_CONSTRAINT_TYPES = ['precedence', 'time_window', 'fixed_start',
  'resource_capacity', 'non_overlap', 'max_concurrent', 'resource_assignment'];
const SOFT_CONSTRAINT_TYPES = ['due_date', 'preferred_window', 'min_gap',
  'resource_balance', 'setup_time', 'max_makespan'];
const SOLVER_NAMES = ['lp', 'ip', 'genetic', 'simulated_annealing', 'greedy'];
const PARETO_SOLVER = 'nsga2';

const SOLVER_LABELS = {
  lp: '线性规划（松弛）',
  ip: '整数规划（分支定界）',
  genetic: '遗传算法',
  simulated_annealing: '模拟退火',
  greedy: '贪心（优先规则）',
  nsga2: '多目标 NSGA-II（Pareto 前沿）',
};

const OBJECTIVE_LABELS = {
  makespan: '最小完工时间',
  total_completion: '总完工时间',
  weighted_completion: '加权完工时间',
  tardiness: '加权拖期',
  cost: '资源成本',
  overtime_cost: '加班成本',
  custom: '自定义组合',
};

const RESOURCE_TYPE_LABELS = {
  personnel: '人员',
  equipment: '设备',
  time: '时间',
};

const HARD_CONSTRAINT_LABELS = {
  precedence: '先后顺序',
  time_window: '时间窗口',
  fixed_start: '固定开始时间',
  resource_capacity: '资源容量',
  non_overlap: '互斥（不重叠）',
  max_concurrent: '最大并发数',
  resource_assignment: '资源指派',
};

const SOFT_CONSTRAINT_LABELS = {
  due_date: '截止日期（拖期）',
  preferred_window: '偏好窗口',
  min_gap: '最小间隔',
  resource_balance: '资源均衡',
  setup_time: '准备/切换时间',
  max_makespan: '最大完工时间',
};

const STATUS_LABELS = {
  optimal: '最优',
  feasible: '可行',
  infeasible: '不可行',
  timeout: '超时',
  error: '错误',
};

function objectiveLabel(t) { return OBJECTIVE_LABELS[t] || t; }
function resourceTypeLabel(t) { return RESOURCE_TYPE_LABELS[t] || t; }
function constraintLabel(t) {
  return HARD_CONSTRAINT_LABELS[t] || SOFT_CONSTRAINT_LABELS[t] || t;
}
function solverLabel(n) { return SOLVER_LABELS[n] || n; }
function statusLabel(s) { return STATUS_LABELS[s] || s; }

/* Short labels for the canonical objective vector used in table columns and
   axis titles; mirrors backend.objectives.CANONICAL_OBJECTIVES. */
const OBJECTIVE_SHORT = {
  makespan: '完工',
  total_completion: '总完工',
  weighted_completion: '加权完工',
  tardiness: '拖期',
  cost: '成本',
  overtime_cost: '加班',
};
function objectiveShort(t) { return OBJECTIVE_SHORT[t] || t; }

/* The canonical value of an objective for a solution.  Every page reads the
   stored ``objectives`` vector (computed once at solve time) and never a
   re-derived number, so the two Pareto objectives stay identical across the
   results / compare / Gantt / report pages. */
function solutionObjective(s, name) {
  if (s && s.objectives && Object.prototype.hasOwnProperty.call(s.objectives, name)) {
    return s.objectives[name];
  }
  if (name === 'makespan') return s ? s.makespan : null;
  if (name === 'cost') return s?.metrics?.resource_cost;
  if (name === 'overtime_cost') return s?.metrics?.overtime_cost;
  if (name === 'tardiness') return s?.metrics?.total_tardiness;
  return s?.metrics?.[name];
}

/* What a run actually optimised (fallback for legacy single-objective
   solutions that predate the field). */
function optimizedObjectives(s) {
  if (s && Array.isArray(s.optimized_objectives) && s.optimized_objectives.length) {
    return s.optimized_objectives;
  }
  return ['makespan'];
}

/* Compact "完工 20 · 加班 120" rendering of the optimised vector. */
function objectiveVectorText(s) {
  return optimizedObjectives(s)
    .map(n => `${objectiveShort(n)} ${fmt(solutionObjective(s, n))}`).join(' · ');
}

const PALETTE = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc949', '#af7aa1', '#ff9da7', '#9c755f', '#bab0ab', '#86bcb6', '#b07aa1'];

/* ---- current problem selection --------------------------------------- */
function currentProblemId() {
  const q = new URLSearchParams(location.search).get('id');
  if (q) { localStorage.setItem('activeProblem', q); return q; }
  return localStorage.getItem('activeProblem') || null;
}

function setProblemParam(id) {
  const url = new URL(location.href);
  if (id) {
    localStorage.setItem('activeProblem', id);
    url.searchParams.set('id', id);
  } else {
    localStorage.removeItem('activeProblem');
    url.searchParams.delete('id');
  }
  history.replaceState(null, '', url);
}

/* ---- API ------------------------------------------------------------- */
async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let body = null;
  try { body = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const msg = (body && (body.error || (body.details && body.details.join('; ')))) || res.statusText;
    throw new Error(msg);
  }
  return body;
}

function toast(msg, type = 'ok') {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = 'toast ' + type;
  requestAnimationFrame(() => el.classList.add('show'));
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 3200);
}

/* ---- navigation ------------------------------------------------------ */
const NAV = [
  ['index.html', '仪表盘'],
  ['resources.html', '资源管理'],
  ['tasks.html', '任务与依赖'],
  ['constraints.html', '约束配置'],
  ['solvers.html', '求解器与参数'],
  ['gantt.html', '甘特图'],
  ['results.html', '结果与目标值'],
  ['pareto.html', '多目标前沿'],
  ['sensitivity.html', '敏感性分析'],
  ['compare.html', '方案对比'],
  ['report.html', '报告生成'],
  ['history.html', '历史实例'],
];

function renderNav(active) {
  const nav = document.getElementById('nav');
  if (!nav) return;
  nav.innerHTML = NAV.map(([href, label]) =>
    `<a href="${href}${currentProblemId() ? '?id=' + encodeURIComponent(currentProblemId()) : ''}"
        class="${active === href ? 'active' : ''}">${label}</a>`).join('');
}

/* Wire up the shared sidebar (navigation + problem picker). */
function initSidebar(active) {
  renderNav(active);
  const sel = document.getElementById('problem-picker');
  if (sel) {
    renderProblemPicker(sel, (id) => { setProblemParam(id); location.reload(); });
  }
}

function renderProblemPicker(selectEl, onSelect, includeEmpty = true) {
  api('/problems').then(({ problems }) => {
    const cur = currentProblemId();
    let html = includeEmpty ? '<option value="">— 请选择问题实例 —</option>' : '';
    html += problems.map(p =>
      `<option value="${p.id}" ${p.id === cur ? 'selected' : ''}>${p.name || p.id}</option>`).join('');
    selectEl.innerHTML = html;
    if (onSelect) selectEl.addEventListener('change', () => onSelect(selectEl.value));
    // First visit only: adopt the first problem and reload exactly once so the
    // page loads it.  On later loads `cur` is set, so this never fires again —
    // that guard is what stops the infinite refresh loop.
    if (!cur && problems.length) {
      selectEl.value = problems[0].id;
      if (onSelect) onSelect(problems[0].id);
    }
  }).catch(e => toast('Failed to load problems: ' + e.message, 'error'));
}

/* ---- formatting ------------------------------------------------------ */
function fmt(v, digits = 2) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(digits);
  return String(v);
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function statusBadge(status) {
  const cls = { optimal: 'ok', feasible: 'info', infeasible: 'bad',
    timeout: 'warn', error: 'bad' }[status] || 'muted';
  return `<span class="badge ${cls}">${escapeHtml(statusLabel(status))}</span>`;
}

/* ---- DOM helpers ----------------------------------------------------- */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    el.append(c.nodeType ? c : document.createTextNode(c));
  }
  return el;
}

function colorFor(key, i = 0) {
  let hash = 0;
  const s = String(key);
  for (let j = 0; j < s.length; j++) hash = (hash * 31 + s.charCodeAt(j)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}
