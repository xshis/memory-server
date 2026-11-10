const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');
const statsEl = document.getElementById('stats');
const hudEl = document.getElementById('hud');
let embedInfo = null;
// 极简模式（Obsidian graph 风）：纯点+细环，无珠体光晕；localStorage 持久化
let minimalMode = false;
try { minimalMode = localStorage.getItem('kb-minimal') === '1'; } catch (error) { minimalMode = false; }
// 脑图内搜索词：非匹配节点淡出到 5%（Obsidian graph 的搜索过滤交互）
let mapQuery = '';
// P1：局部视图（选中文件的语义邻居+同目录聚光）、时间着色、新文件生长动效
let localViewId = null;
let timeColorMode = false;
try { timeColorMode = localStorage.getItem('kb-timecolor') === '1'; } catch (error) { timeColorMode = false; }
const settledNodes = new Set();
const spawnAnim = new Map();
function easeOutBack(t) {
  const c1 = 1.70158, c3 = c1 + 1;
  const u = t - 1;
  return 1 + c3 * u * u * u + c1 * u * u;
}
const TIME_PALETTE = [
  {days: 7, color: '#fbbf24'},   // 近7天：暖金
  {days: 30, color: '#34d399'},  // 30天内：翠绿
  {days: 90, color: '#38bdf8'},  // 90天内：天蓝
  {days: 365, color: '#818cf8'}, // 1年内：靛蓝
  {days: Infinity, color: '#64748b'}, // 更早：灰
];
function timeColorOf(mtime) {
  const ageDays = (Date.now() / 1000 - Number(mtime || 0)) / 86400;
  for (const item of TIME_PALETTE) {
    if (ageDays <= item.days) return item.color;
  }
  return '#64748b';
}
const toastEl = document.getElementById('toast');
const chatLog = document.getElementById('chatLog');
const chatInput = document.getElementById('chatInput');
const assistantPanel = document.querySelector('aside.chat');
const detailWin = document.getElementById('detailWin');
const detailBody = document.getElementById('detailBody');
const detailTitle = document.getElementById('detailTitle');
const organizeWin = document.getElementById('organizeWin');
const orgPathEl = document.getElementById('orgPath');
const orgStrategyEl = document.getElementById('orgStrategy');
const orgRecursiveEl = document.getElementById('orgRecursive');
const orgApplyBtn = document.getElementById('orgApplyBtn');
const orgUndoBtn = document.getElementById('orgUndoBtn');
const orgResultEl = document.getElementById('orgResult');
const cleanWin = document.getElementById('cleanWin');
const cleanBody = document.getElementById('cleanBody');

let graph = null;
// 图例悬停高亮（P0/P1）：悬停的分类 id，绘制时非同类节点压暗
let legendDimCluster = null;
let nodeById = new Map();
let allNodes = [];
let allLinks = [];
let visibleNodes = [];
let visibleLinks = [];
let tagById = new Map();
let extraNodes = [];
let extraLinks = [];
let selected = null;
let hovered = null;
let projected = new Map();
let rotX = -0.32, rotY = 0.55;
let camX = 0, camY = 0, camZ = 0;
let camTargetX = 0, camTargetY = 0, camTargetZ = 0;
let zoom = 1600, zoomTarget = 1600;
let focusedCluster = null;
let focusAmount = 0;
let clusterCenters = new Map();
let clusterAngles = new Map();
let clusterSpeeds = new Map();
let loadingTimer = null;
let selfcheckTimer = null;
let autoRotate = true;
let dragging = false, moved = false, lastX = 0, lastY = 0;
let nodeDrag = null;
let nodeOffsets = new Map();
let mousePos = {x: 0, y: 0, inside: false};
let inertiaX = 0, inertiaY = 0, spinX = 0, spinY = 0, spinAt = 0;
let width = 0, height = 0, dpr = 1;
let lastFrame = performance.now();
let elapsed = 0;
let lastVersion = 0;
let statusTimer = null;
let toastTimer = null;
let lastReveal = {path: '', at: 0};
let chatBusy = false;
let chatAbort = null;
let pendingDt = 0;
let lastDrawAt = 0;
let lastInteraction = performance.now();
let lastCameraInput = performance.now();
let needsDraw = true;
let backgroundGradient = null;
const TARGET_FPS = 42;
let orgPlanId = '';
let cleanBusy = false;
let cleanResult = null;
let orgAppliedId = '';
let welcomed = false;
let firstLoadDone = false;
const FOV = 950;
const stars = Array.from({length: 240}, () => ({
  x: Math.random(), y: Math.random(), r: Math.random() * 1.4 + .3,
  a: Math.random() * .55 + .15, phase: Math.random() * Math.PI * 2,
  speed: .0007 + Math.random() * .0014, cross: Math.random() < .08
}));

function fmt(n) { return Number(n || 0).toLocaleString('zh-CN'); }
function formatBytes(n) {
  let value = Number(n || 0);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return (index === 0 ? Math.round(value) : value.toFixed(1)) + units[index];
}
function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}
function baseName(path) {
  const parts = String(path || '').split('/');
  return parts[parts.length - 1] || path;
}
function shortPath(path, depth = 2) {
  const parts = String(path || '').split('/').filter(Boolean);
  if (parts.length <= depth) return parts.join('/');
  return '…/' + parts.slice(-depth).join('/');
}
function tagName(id) {
  const tag = tagById.get(id);
  return tag ? tag.label : id.replace(/^[^:]+:/, '');
}
function tagColor(id) {
  const tag = tagById.get(id);
  return tag ? tag.color : '#94a3b8';
}
function toast(message) {
  toastEl.textContent = message;
  toastEl.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.style.display = 'none'; }, 3200);
}
function samePath(left, right) {
  if (!left || !right) return false;
  const a = String(left).replace(/\\/g, '/');
  const b = String(right).replace(/\\/g, '/');
  return a === b || b.endsWith('/' + a) || a.endsWith('/' + b);
}
function matchesQuery(node) {
  if (!mapQuery) return true;
  const label = String(node.label || '').toLowerCase();
  const path = String(node.path || '').toLowerCase();
  return label.includes(mapQuery) || path.includes(mapQuery);
}
function fmtMtime(value) {
  const stamp = Number(value || 0);
  if (!stamp) return '';
  const date = new Date(stamp * 1000);
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function resize() {
  const rect = canvas.parentElement.getBoundingClientRect();
  width = Math.max(320, rect.width);
  height = Math.max(240, rect.height);
  dpr = Math.min(1.6, window.devicePixelRatio || 1);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  backgroundGradient = null;
  needsDraw = true;
}

function statsHtml(summary) {
  if (!summary) return '加载中…';
  let html = `文件 <b>${fmt(summary.files)}</b> · 已索引 <b>${fmt(summary.indexed)}</b> · 节点 <b>${fmt(summary.nodes)}</b> · 标签 <b>${fmt(summary.tags)}</b> · 展示 <b>${fmt(summary.shown_files)}</b> · ${summary.bytes_text}`;
  if (embedInfo && embedInfo.embeddable > 0 &&
      (embedInfo.running || embedInfo.embedded < embedInfo.embeddable)) {
    const pct = Math.round(embedInfo.embedded * 100 / embedInfo.embeddable);
    html += ` · 语义嵌入 <b>${embedInfo.running ? '补算中 ' : ''}${pct}%</b>`;
  }
  return html;
}

function renderStats() {
  if (!graph) return;
  statsEl.innerHTML = statsHtml(graph.summary);
}

function updateStats() {
  if (!graph) return;
  renderStats();
  hudEl.innerHTML = `版本 ${graph.generated_at}<br>${autoRotate ? '自动旋转中' : '已暂停旋转'} · 拖拽旋转 / 滚轮缩放 · 拖星点回弹 / 鼠标靠近跟随 · 悬浮看完整路径`;
}

function buildDust() {
  for (const node of graph.nodes) {
    if (node.kind !== 'tag' || !['category', 'project'].includes(node.tagKind)) continue;
    const total = node.tagKind === 'category' ? 18 : 10;
    node.dust = Array.from({length: total}, (_, index) => {
      const seed = (index + 1) * ((node.label || '').length + 3);
      return {
        a: ((seed * 137.5) % 360) * Math.PI / 180,
        d: ((seed * 53) % 97) / 97,
        r: .5 + ((seed * 17) % 12) / 10,
        fade: .5 + ((seed * 29) % 50) / 100,
      };
    });
  }
}

function buildClusters() {
  clusterCenters = new Map();
  clusterAngles = new Map();
  clusterSpeeds = new Map();
  for (const node of graph.nodes) {
    if (node.kind !== 'tag' || node.tagKind !== 'category') continue;
    clusterCenters.set(node.id, {x: node.x, y: node.y, z: node.z});
    const seed = [...node.id].reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
    clusterAngles.set(node.id, (seed % 360) * Math.PI / 180);
    clusterSpeeds.set(node.id, 0.000055 + (seed % 7) * 0.000013);
  }
}

function clusterOf(node) {
  if (node.kind === 'file') return node.primary || null;
  if (node.kind === 'tag') return node.tagKind === 'category' ? node.id : (node.parent || null);
  return null;
}

function displayPos(node) {
  const clusterId = clusterOf(node);
  const center = clusterId ? clusterCenters.get(clusterId) : null;
  if (!center) return node;
  const angle = clusterAngles.get(clusterId) || 0;
  const dx = node.x - center.x;
  const dz = node.z - center.z;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  let rx = center.x + dx * cos - dz * sin;
  let rz = center.z + dx * sin + dz * cos;
  // 聚焦聚拢（融合球体布局）：聚焦某分类时，该类文件节点向中心收拢——
  // 否则文件散布全球，飞到分类位置也看不到它的文件
  if (focusedCluster && clusterId === focusedCluster && focusAmount > 0.001 && node.kind === 'file') {
    const pull = 0.72 * focusAmount;
    rx = center.x + (rx - center.x) * (1 - pull);
    rz = center.z + (rz - center.z) * (1 - pull);
  }
  return {x: rx, y: node.y, z: rz};
}

function rebuildVisible() {
  const baseNodes = (graph ? graph.nodes : []).concat(extraNodes);
  const baseLinks = (graph ? graph.links : []).concat(extraLinks);
  const ids = new Set();
  visibleNodes = baseNodes;
  for (const node of visibleNodes) ids.add(node.id);
  visibleLinks = baseLinks.filter(link => ids.has(link.source) && ids.has(link.target));
  if (selected && !ids.has(selected.id)) selected = null;
  hovered = null;
}

async function loadGraph() {
  let response;
  try {
    response = await fetch('/api/graph', {cache: 'no-store'});
  } catch (error) {
    // 服务重启/网络瞬断：返回 false 让启动流程重试，而不是把页面永久卡在加载遮罩
    return false;
  }
  const data = await response.json();
  if (data.error) {
    document.getElementById('loadingText').textContent = '读取失败：' + data.error;
    return false;
  }
  graph = data;
  lastVersion = data.version || 0;
  extraNodes = [];
  extraLinks = [];
  nodeById = new Map();
  tagById = new Map();
  for (const node of graph.nodes) nodeById.set(node.id, node);
  for (const tag of graph.tags || []) tagById.set(tag.id, tag);
  buildDust();
  buildClusters();
  if (focusedCluster && !clusterCenters.has(focusedCluster)) exitCluster();
  allNodes = graph.nodes.concat(extraNodes);
  allLinks = graph.links.concat(extraLinks);
  rebuildVisible();
  updateStats();
  needsDraw = true;
  if (selected && !nodeById.has(selected.id)) {
    selected = null;
    detailWin.hidden = true;
  }
  if (!welcomed) {
    welcomed = true;
    showWelcome();
  }
  if (!organizeWin.hidden) renderOrganizeFolderList();
  if (!cleanWin.hidden) renderCleanWindow();
  // P1 生长动效：首次加载作为基线不播；之后新出现的文件节点 850ms 弹性生长
  for (const node of graph.nodes) {
    if (node.kind !== 'file') continue;
    if (!settledNodes.has(node.id)) {
      if (firstLoadDone) spawnAnim.set(node.id, performance.now());
      settledNodes.add(node.id);
    }
  }
  firstLoadDone = true;
  return true;
}

function project(node) {
  const cy = Math.cos(rotY), sy = Math.sin(rotY);
  const px = node.x - camX;
  const py = node.y - camY;
  const pz = node.z - camZ;
  let x = px * cy - pz * sy;
  let z = px * sy + pz * cy;
  const cx = Math.cos(rotX), sx = Math.sin(rotX);
  const y = py * cx - z * sx;
  z = py * sx + z * cx;
  const depth = z + zoom;
  const scale = FOV / Math.max(160, depth);
  return {
    x: width / 2 + x * scale,
    y: height / 2 + y * scale,
    scale,
    depth: z,
  };
}

const nebulaSprites = new Map();

function nebulaSprite(color) {
  if (nebulaSprites.has(color)) return nebulaSprites.get(color);
  const size = 128;
  const sprite = document.createElement('canvas');
  sprite.width = size;
  sprite.height = size;
  const brush = sprite.getContext('2d');
  const gradient = brush.createRadialGradient(size / 2, size / 2, 2, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, rgbaColor(color, 0.16));
  gradient.addColorStop(0.45, rgbaColor(color, 0.06));
  gradient.addColorStop(1, rgbaColor(color, 0));
  brush.fillStyle = gradient;
  brush.fillRect(0, 0, size, size);
  nebulaSprites.set(color, sprite);
  return sprite;
}

function applyNodeEffects(node, base) {
  let x = base.x;
  let y = base.y;
  const offset = nodeOffsets.get(node.id);
  if (offset) {
    x += offset.x;
    y += offset.y;
  } else if (!nodeDrag && !dragging && mousePos.inside) {
    const dx = mousePos.x - base.x;
    const dy = mousePos.y - base.y;
    const distance = Math.hypot(dx, dy);
    const strength = Math.max(0, 1 - distance / 280) * 0.38;
    x += dx * strength;
    y += dy * strength;
  }
  return {...base, x, y};
}

function drawBackground() {
  if (!backgroundGradient) {
    backgroundGradient = ctx.createRadialGradient(width * .5, height * .44, 0,
      width * .5, height * .44, Math.max(width, height) * .8);
    backgroundGradient.addColorStop(0, '#0a1730');
    backgroundGradient.addColorStop(.55, '#060b16');
    backgroundGradient.addColorStop(1, '#03060d');
  }
  ctx.fillStyle = backgroundGradient;
  ctx.fillRect(0, 0, width, height);
  for (const star of stars) {
    const alpha = star.a * (0.55 + 0.45 * Math.sin(elapsed * star.speed + star.phase));
    ctx.globalAlpha = Math.max(0.04, alpha);
    ctx.fillStyle = '#a9c8ff';
    ctx.beginPath();
    ctx.arc(star.x * width, star.y * height, star.r, 0, Math.PI * 2);
    ctx.fill();
    if (star.cross) {
      ctx.strokeStyle = '#cfe2ff';
      ctx.lineWidth = .6;
      ctx.beginPath();
      ctx.moveTo(star.x * width - star.r * 2.4, star.y * height);
      ctx.lineTo(star.x * width + star.r * 2.4, star.y * height);
      ctx.moveTo(star.x * width, star.y * height - star.r * 2.4);
      ctx.lineTo(star.x * width, star.y * height + star.r * 2.4);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}

function rgbaColor(hex, alpha) {
  const value = String(hex || '#60a5fa').replace('#', '');
  const full = value.length === 3 ? value.split('').map(ch => ch + ch).join('') : value;
  const number = parseInt(full, 16) || 0;
  const red = (number >> 16) & 255;
  const green = (number >> 8) & 255;
  const blue = number & 255;
  return `rgba(${red},${green},${blue},${alpha})`;
}

function shadeColor(hex, f) {
  const value = String(hex || '#60a5fa').replace('#', '');
  const full = value.length === 3 ? value.split('').map(ch => ch + ch).join('') : value;
  const number = parseInt(full, 16) || 0;
  const target = f > 0 ? 255 : 0;
  const a = Math.abs(f);
  const mix = c => Math.round(c + (target - c) * a);
  return `rgb(${mix((number >> 16) & 255)},${mix((number >> 8) & 255)},${mix(number & 255)})`;
}

function muteColor(hex, f) {
  const value = String(hex || '#60a5fa').replace('#', '');
  const full = value.length === 3 ? value.split('').map(ch => ch + ch).join('') : value;
  const number = parseInt(full, 16) || 0;
  const red = (number >> 16) & 255, green = (number >> 8) & 255, blue = number & 255;
  const lum = Math.round(0.299 * red + 0.587 * green + 0.114 * blue);
  const mix = c => Math.round(c + (lum - c) * f);
  return `#${[mix(red), mix(green), mix(blue)].map(v => v.toString(16).padStart(2, '0')).join('')}`;
}

const beadCache = new Map();
const BEAD_SPRITE = 64;
const BEAD_CORE = 13;
function beadSprite(color) {
  let sprite = beadCache.get(color);
  if (sprite) return sprite;
  const muted = muteColor(color, .48);
  const c = document.createElement('canvas');
  c.width = c.height = BEAD_SPRITE;
  const g = c.getContext('2d');
  const half = BEAD_SPRITE / 2;
  const halo = g.createRadialGradient(half, half, BEAD_CORE * .7, half, half, half);
  halo.addColorStop(0, rgbaColor(muted, .12));
  halo.addColorStop(1, rgbaColor(muted, 0));
  g.fillStyle = halo;
  g.fillRect(0, 0, BEAD_SPRITE, BEAD_SPRITE);
  const body = g.createRadialGradient(half - BEAD_CORE * .3, half - BEAD_CORE * .34, BEAD_CORE * .15, half, half, BEAD_CORE);
  body.addColorStop(0, shadeColor(muted, .38));
  body.addColorStop(.7, muted);
  body.addColorStop(1, shadeColor(muted, -.26));
  g.beginPath();
  g.arc(half, half, BEAD_CORE, 0, Math.PI * 2);
  g.fillStyle = body;
  g.fill();
  g.strokeStyle = shadeColor(muted, .32);
  g.lineWidth = 1.3;
  g.stroke();
  beadCache.set(color, c);
  return c;
}

function drawNebulae() {
  const items = [];
  for (const node of visibleNodes) {
    if (node.kind !== 'tag') continue;
    if (!['category', 'project'].includes(node.tagKind)) continue;
    const p = projected.get(node.id);
    if (p) items.push({node, p});
  }
  if (!items.length) return;
  items.sort((a, b) => b.p.depth - a.p.depth);
  let minDepth = Infinity;
  let maxDepth = -Infinity;
  for (const item of items) {
    if (item.p.depth < minDepth) minDepth = item.p.depth;
    if (item.p.depth > maxDepth) maxDepth = item.p.depth;
  }
  const span = Math.max(1, maxDepth - minDepth);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const {node, p} of items) {
    const clusterId = clusterOf(node);
    const vis = focusedCluster
      ? (clusterId === focusedCluster ? 1 : Math.max(0, 1 - focusAmount))
      : 1;
    if (vis <= 0.02) continue;
    const fog = 1 - ((p.depth - minDepth) / span) * 0.62;
    const alpha = vis * fog;
    const base = node.tagKind === 'category'
      ? 62 + Math.log10((node.count || 0) + 1) * 20
      : 28 + Math.log10((node.count || 0) + 1) * 9;
    const radius = Math.max(18, base * p.scale * 1.35);
    const color = node.color || '#60a5fa';
    const sprite = nebulaSprite(color);
    const nebMul = minimalMode ? 0.3 : 1;
    ctx.globalAlpha = .85 * alpha * nebMul;
    ctx.drawImage(sprite, p.x - radius, p.y - radius, radius * 2, radius * 2);
    const wide = radius * 1.55;
    ctx.globalAlpha = .4 * alpha * nebMul;
    ctx.drawImage(sprite, p.x - wide, p.y - wide, wide * 2, wide * 2);
    const spin = (clusterAngles.get(clusterId) || 0) * 0.6 + rotY * 0.15;
    if (!minimalMode) {
      for (const dust of node.dust || []) {
        const angle = dust.a + spin;
        const distance = radius * (0.18 + dust.d * 0.82);
        ctx.globalAlpha = 0.3 * dust.fade * alpha;
        ctx.fillStyle = rgbaColor(color, 0.9);
        ctx.beginPath();
        ctx.arc(p.x + Math.cos(angle) * distance, p.y + Math.sin(angle) * distance * 0.76,
          dust.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

function neighborIds(node) {
  const ids = new Set();
  if (!node) return ids;
  for (const link of visibleLinks) {
    if (link.source === node.id) ids.add(link.target);
    if (link.target === node.id) ids.add(link.source);
  }
  return ids;
}

function draw(dt) {
  elapsed += dt;
  drawBackground();
  projected = new Map();
  for (const node of visibleNodes) projected.set(node.id, applyNodeEffects(node, project(displayPos(node))));
  drawNebulae();

  const entries = visibleNodes
    .map(node => ({node, p: projected.get(node.id)}))
    .filter(item => item.p)
    .sort((a, b) => b.p.depth - a.p.depth);
  let minDepth = Infinity;
  let maxDepth = -Infinity;
  for (const item of entries) {
    if (item.p.depth < minDepth) minDepth = item.p.depth;
    if (item.p.depth > maxDepth) maxDepth = item.p.depth;
  }
  const span = Math.max(1, maxDepth - minDepth);
  const fogFor = p => 1 - ((p.depth - minDepth) / span) * 0.35;
  const visibilityFor = node => {
    if (!focusedCluster) return 1;
    return clusterOf(node) === focusedCluster ? 1 : Math.max(0, 1 - focusAmount);
  };

  const focus = selected || hovered;
  const neighbors = neighborIds(focus);
  // 悬停聚焦变暗（P1）：悬停节点/图例分类时，非同类节点压暗突出主体
  const dimCluster = legendDimCluster || (focus ? clusterOf(focus) : null);
  const dimFactorOf = node => (!dimCluster || node.kind === 'root') ? 1
    : (clusterOf(node) === dimCluster ? 1 : 0.28);
  // 搜索淡出（2026-11-09）：脑图内搜索时非匹配节点压暗到 5%，对照 Obsidian graph 的搜索过滤
  const queryDimOf = node => (!mapQuery || matchesQuery(node)) ? 1 : 0.05;
  // P1 局部视图：成员 = 自身 + 语义邻居 + 同目录文件；非成员压暗到 4%
  const localViewMembers = new Set();
  if (localViewId) {
    localViewMembers.add(localViewId);
    const rootNode = nodeById.get(localViewId);
    const rootDir = rootNode && rootNode.path ? String(rootNode.path).replace(/\/[^/]*$/, '') : '';
    for (const link of visibleLinks) {
      if (link.kind !== 'semantic') continue;
      if (link.source === localViewId) localViewMembers.add(link.target);
      if (link.target === localViewId) localViewMembers.add(link.source);
    }
    if (rootDir) {
      for (const node of visibleNodes) {
        if (node.kind !== 'file' || !node.path) continue;
        if (String(node.path).startsWith(rootDir + '/')) localViewMembers.add(node.id);
      }
    }
  }
  const localDimOf = node => (!localViewId || localViewMembers.has(node.id)) ? 1 : 0.04;
  // P1 生长动效：新入库文件 850ms 弹性放大（easeOutBack 带回弹）
  const spawnFactorOf = node => {
    const start = spawnAnim.get(node.id);
    if (start == null) return 1;
    const t = (performance.now() - start) / 850;
    if (t >= 1) { spawnAnim.delete(node.id); return 1; }
    return Math.max(0.01, easeOutBack(Math.max(0, t)));
  };
  // 标签分级预算（P1）：全景靠碰撞检测自然取舍，放大/聚焦后全量显示（文件名是主要识别信息，不再设上限）
  const labelBudget = Infinity;
  // 语义连线（2026-11-09）：细紫线，透明度随相似度权重；命中的连线照常高亮
  const semanticStyle = link => {
    const weight = Math.max(0, Math.min(1, Number(link.weight || 0)));
    return {color: rgbaColor('#a78bfa', .3 + .55 * weight), baseAlpha: .14 + .3 * weight, width: .55};
  };
  for (const link of visibleLinks) {
    const a = projected.get(link.source);
    const b = projected.get(link.target);
    if (!a || !b) continue;
    const nodeA = nodeById.get(link.source);
    const nodeB = nodeById.get(link.target);
    const vis = Math.min(visibilityFor(nodeA || {}), visibilityFor(nodeB || {}));
    if (vis <= 0.02) continue;
    const highlight = focus && (link.source === focus.id || link.target === focus.id);
    const secondary = focus && (neighbors.has(link.source) || neighbors.has(link.target));
    const isSemantic = link.kind === 'semantic';
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    if (link.kind === 'hub') {
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const length = Math.hypot(dx, dy) || 1;
      const seed = String(link.target).split('').reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
      const bend = (seed % 2 ? 1 : -1) * length * 0.12;
      ctx.quadraticCurveTo(
        (a.x + b.x) / 2 - (dy / length) * bend,
        (a.y + b.y) / 2 + (dx / length) * bend,
        b.x, b.y,
      );
    } else {
      ctx.lineTo(b.x, b.y);
    }
    const sem = isSemantic ? semanticStyle(link) : null;
    ctx.strokeStyle = highlight ? 'rgba(251,191,36,.9)'
      : (secondary ? 'rgba(147,197,253,.55)'
      : (sem ? sem.color : (link.color || 'rgba(96,165,250,.2)')));
    const draggedId = nodeDrag ? nodeDrag.node.id : null;
    const dragLinked = draggedId && (link.source === draggedId || link.target === draggedId);
    const dragInfluenced = draggedId && nodeDrag.influence
      && (nodeDrag.influence.has(link.source) || nodeDrag.influence.has(link.target));
    let baseAlpha = highlight ? .95 : (secondary ? .55
      : (sem ? sem.baseAlpha : (link.kind === 'hub' ? .38 : .2)));
    let lineWidth = highlight ? 2 : (sem ? sem.width : (link.kind === 'hub' ? 1.3 : .7));
    if (dragLinked) {
      baseAlpha = .95;
      lineWidth = 2.2;
    } else if (dragInfluenced) {
      baseAlpha = Math.max(baseAlpha, .5);
      lineWidth = Math.max(lineWidth, 1.15);
    }
    ctx.globalAlpha = baseAlpha * vis;
    ctx.lineWidth = lineWidth;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  for (const {node, p} of entries) {
    const vis = visibilityFor(node);
    if (vis <= 0.02) continue;
    const fog = fogFor(p);
    const spawnF = spawnFactorOf(node);
    const alpha = vis * fog * dimFactorOf(node) * queryDimOf(node) * localDimOf(node)
      * (node.kind === 'file' ? Math.min(1, spawnF) : 1);
    const minRadius = node.kind === 'file' ? 4.2 : 1.4;
    const radius = Math.max(minRadius, (node.radius || 5) * p.scale) * (node.kind === 'file' ? spawnF : 1);
    const nodeColor = (timeColorMode && node.kind === 'file') ? timeColorOf(node.mtime) : (node.color || '#b4c4da');
    const isDragged = nodeDrag && nodeDrag.node.id === node.id;
    const isFocus = (focus && node.id === focus.id) || isDragged;
    const isNeighbor = focus && neighbors.has(node.id);
    const showTagLabel = node.kind === 'tag' && (
      ['category', 'project', 'recommend'].includes(node.tagKind) || isFocus || isNeighbor
    );
    if (node.kind === 'search') {
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.globalAlpha = alpha * .95;
      ctx.fillStyle = '#fb7185';
      ctx.fill();
    } else if (minimalMode) {
      // 极简模式（Obsidian graph 风）：纯点 + 分类色细环，无珠体/光晕
      const dotColor = nodeColor;
      ctx.beginPath();
      ctx.arc(p.x, p.y, Math.max(2, radius * .82), 0, Math.PI * 2);
      ctx.globalAlpha = alpha * .5;
      ctx.fillStyle = rgbaColor(dotColor, .85);
      ctx.fill();
      ctx.globalAlpha = alpha * .8;
      ctx.strokeStyle = rgbaColor(dotColor, 1);
      ctx.lineWidth = .9;
      ctx.stroke();
    } else {
      const sprite = beadSprite(nodeColor);
      const s = radius / BEAD_CORE * BEAD_SPRITE;
      ctx.globalAlpha = alpha;
      ctx.drawImage(sprite, p.x - s / 2, p.y - s / 2, s, s);
    }
    if (isFocus) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius + 4, 0, Math.PI * 2);
      ctx.strokeStyle = '#fbbf24';
      ctx.lineWidth = 2;
      ctx.globalAlpha = alpha;
      ctx.stroke();
    } else if (isNeighbor) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius + 3, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(147,197,253,.7)';
      ctx.lineWidth = 1;
      ctx.globalAlpha = alpha * .8;
      ctx.stroke();
    }
    if (node.kind === 'root' || showTagLabel || isFocus) {
      ctx.globalAlpha = alpha * .92;
      ctx.fillStyle = node.kind === 'tag' ? (node.color || '#9ec5ff') : '#dbeafe';
      ctx.font = `${node.kind === 'root' ? 16 : 11}px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      const label = node.label + (node.count != null ? ` · ${fmt(node.count)}` : '');
      ctx.fillText(label, p.x + radius + 5, p.y);
    }
  }

  const occupied = [];
  for (const {node, p} of entries) {
    if (node.kind !== 'root' && node.kind !== 'tag') continue;
    const show = node.kind === 'root' || ['category', 'project', 'recommend'].includes(node.tagKind)
      || (focus && (node.id === focus.id || neighbors.has(node.id)));
    if (!show) continue;
    const radius = Math.max(1.2, (node.radius || 5) * p.scale);
    const text = node.label + (node.count != null ? ` · ${fmt(node.count)}` : '');
    ctx.font = `${node.kind === 'root' ? 16 : 11}px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif`;
    const textWidth = ctx.measureText(text).width;
    occupied.push({x: p.x + radius + 5, y: p.y - 8, w: textWidth, h: 16});
  }
  const fileLabels = entries
    .filter(item => item.node.kind === 'file'
      || (item.node.kind === 'tag' && !['category', 'project', 'recommend'].includes(item.node.tagKind)
        && !(focus && item.node.id === focus.id)))
    .map(item => ({
      node: item.node,
      p: item.p,
      alpha: visibilityFor(item.node) * fogFor(item.p) * dimFactorOf(item.node)
        * queryDimOf(item.node) * localDimOf(item.node),
    }))
    .filter(item => item.alpha > 0.12)
    .sort((a, b) => {
      const priority = item => {
        if (selected && item.node.id === selected.id) return 2;
        if (hovered && item.node.id === hovered.id) return 1;
        return 0;
      };
      return priority(b) - priority(a) || b.p.depth - a.p.depth;
    })
    .slice(0, labelBudget);
  for (const item of fileLabels) {
    const {node, p} = item;
    const forced = (selected && node.id === selected.id) || (hovered && node.id === hovered.id);
    const radius = Math.max(4.2, (node.radius || 4) * p.scale);
    let text = String(node.label || '');
    if (!forced && zoom < 2600) {
      // 全景缩略：去扩展名 + 压到 5 字，密度大时不乱
      const stem = text.replace(/\.[^.]+$/, '');
      text = stem || text;
      if (text.length > 5) text = text.slice(0, 4) + '…';
    } else if (text.length > 18) {
      text = text.slice(0, 17) + '…';
    }
    const size = forced ? 12 : 10;
    ctx.font = `600 ${size}px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif`;
    const textWidth = ctx.measureText(text).width;
    const gap = 5;
    const candidates = [
      {x: p.x - textWidth / 2, y: p.y + radius + 11},
      {x: p.x + radius + gap, y: p.y},
      {x: p.x - radius - gap - textWidth, y: p.y},
      {x: p.x + radius + gap, y: p.y - 11},
      {x: p.x - radius - gap - textWidth, y: p.y - 11},
      {x: p.x + radius + gap, y: p.y + 11},
      {x: p.x - radius - gap - textWidth, y: p.y + 11},
      {x: p.x - textWidth / 2, y: p.y - radius - 12},
    ];
    let chosen = null;
    for (const candidate of candidates) {
      const x = Math.max(4, Math.min(width - textWidth - 4, candidate.x));
      const y = Math.max(9, Math.min(height - 8, candidate.y));
      const rect = {x: x - 2, y: y - 8, w: textWidth + 4, h: 15};
      let overlap = 0;
      for (const box of occupied) {
        const overlapX = Math.min(rect.x + rect.w, box.x + box.w) - Math.max(rect.x, box.x);
        const overlapY = Math.min(rect.y + rect.h, box.y + box.h) - Math.max(rect.y, box.y);
        if (overlapX > 0 && overlapY > 0) overlap += overlapX * overlapY;
      }
      if (!forced && overlap > rect.w * rect.h * 0.12) continue;
      chosen = {x, y, rect};
      break;
    }
    if (!chosen) continue;
    occupied.push(chosen.rect);
    ctx.globalAlpha = item.alpha * (forced ? 1 : .85);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(3, 8, 18, .92)';
    ctx.strokeText(text, chosen.x, chosen.y);
    ctx.fillStyle = forced ? '#ffffff' : '#d7e5f7';
    ctx.fillText(text, chosen.x, chosen.y);
  }
  ctx.globalAlpha = 1;
  ctx.textAlign = 'left';
}

function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(80, now - lastFrame || 16);
  lastFrame = now;
  if (!dragging && (inertiaX || inertiaY)) {
    rotY += inertiaY;
    rotX = Math.max(-1.35, Math.min(1.35, rotX + inertiaX));
    const decay = Math.pow(0.82, dt / 16.667);
    inertiaY *= decay;
    inertiaX *= decay;
    if (Math.abs(inertiaY) < 0.00002) inertiaY = 0;
    if (Math.abs(inertiaX) < 0.00002) inertiaX = 0;
  }
  if (autoRotate && clusterAngles.size) {
    // 融合球体（2026-11-05）：cluster 自转冻结——节点只随全局 rotY/rotX 整体转动，
    // 避免各板块绕 distant 中心错位蠕动破坏"一个整体球体"的观感
    for (const [id, angle] of clusterAngles) {
      clusterAngles.set(id, angle);
    }
  }
  if (nodeOffsets.size) {
    for (const [id, offset] of nodeOffsets) {
      if (nodeDrag && nodeDrag.node.id === id) continue;
      offset.vx += -offset.x * 0.18;
      offset.vy += -offset.y * 0.18;
      offset.vx *= 0.82;
      offset.vy *= 0.82;
      offset.x += offset.vx;
      offset.y += offset.vy;
      if (Math.abs(offset.x) < 0.08 && Math.abs(offset.y) < 0.08
          && Math.abs(offset.vx) < 0.05 && Math.abs(offset.vy) < 0.05) {
        nodeOffsets.delete(id);
      }
    }
  }
  if (autoRotate && !dragging && !nodeDrag) {
    // 整体球体自转（2026-11-05）：cluster 自转冻结后 autoRotate 只驱动俯仰摆动，
    // 静止画面显得乱——补回绕 Y 轴的全局持续旋转
    rotY += dt * 0.00008;
  }
  if (autoRotate && !dragging && !nodeDrag && !inertiaX && !inertiaY
      && performance.now() - lastCameraInput > 12000) {
    const targetPitch = -0.28 + Math.sin(elapsed * 0.00007) * 0.42;
    rotX += (targetPitch - rotX) * Math.min(1, dt / 2000);
  }
  const ease = Math.min(1, dt / 110);
  camX += (camTargetX - camX) * ease;
  camY += (camTargetY - camY) * ease;
  camZ += (camTargetZ - camZ) * ease;
  zoom += (zoomTarget - zoom) * ease;
  focusAmount += ((focusedCluster ? 1 : 0) - focusAmount) * ease;
  const moving = Math.abs(camTargetX - camX) > 0.4
    || Math.abs(camTargetY - camY) > 0.4
    || Math.abs(camTargetZ - camZ) > 0.4
    || Math.abs(zoomTarget - zoom) > 0.4
    || Math.abs((focusedCluster ? 1 : 0) - focusAmount) > 0.01;
  const busy = autoRotate || dragging || nodeDrag || nodeOffsets.size || inertiaX || inertiaY || needsDraw || moving;
  pendingDt += dt;
  if (!busy) {
    pendingDt = 0;
    return;
  }
  const idle = performance.now() - lastInteraction > 2500;
  const fps = (dragging || moving || needsDraw) ? TARGET_FPS : (idle ? 22 : TARGET_FPS);
  if (now - lastDrawAt < 1000 / fps) return;
  draw(Math.min(80, pendingDt));
  pendingDt = 0;
  lastDrawAt = now;
  needsDraw = false;
}

function pick(x, y) {
  let best = null;
  let bestDistance = Infinity;
  for (const node of visibleNodes) {
    if (focusedCluster && focusAmount > 0.5 && clusterOf(node) !== focusedCluster) continue;
    const p = projected.get(node.id);
    if (!p) continue;
    const radius = Math.max(4, (node.radius || 4) * p.scale + 3);
    const distance = Math.hypot(p.x - x, p.y - y);
    if (distance <= radius && distance < bestDistance) {
      best = node;
      bestDistance = distance;
    }
  }
  return best;
}

function relativePoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {x: event.clientX - rect.left, y: event.clientY - rect.top};
}

function touchInteraction() {
  lastInteraction = performance.now();
  needsDraw = true;
}

function buildDragInfluence(node) {
  const base = projected.get(node.id) || project(displayPos(node));
  const candidates = [];
  for (const other of visibleNodes) {
    if (other.id === node.id || other.kind !== 'file') continue;
    const p = projected.get(other.id);
    if (!p) continue;
    const distance = Math.hypot(p.x - base.x, p.y - base.y);
    if (distance < 320) candidates.push([distance, other.id]);
  }
  candidates.sort((a, b) => a[0] - b[0]);
  const influence = new Map();
  for (const [distance, id] of candidates.slice(0, 80)) {
    influence.set(id, 0.9 * Math.pow(1 - distance / 320, 1.0));
  }
  return influence;
}

canvas.addEventListener('mousedown', event => {
  touchInteraction();
  lastCameraInput = performance.now();
  const point = relativePoint(event);
  mousePos = {x: point.x, y: point.y, inside: true};
  moved = false;
  inertiaX = 0;
  inertiaY = 0;
  spinX = 0;
  spinY = 0;
  const node = pick(point.x, point.y);
  if (node) {
    const existing = nodeOffsets.get(node.id);
    const basePoint = project(displayPos(node));
    const current = projected.get(node.id);
    const startX = existing ? existing.x : (current ? current.x - basePoint.x : 0);
    const startY = existing ? existing.y : (current ? current.y - basePoint.y : 0);
    nodeOffsets.set(node.id, {x: startX, y: startY, vx: 0, vy: 0});
    nodeDrag = {
      node,
      startX: point.x,
      startY: point.y,
      baseX: startX,
      baseY: startY,
      lastDx: 0,
      lastDy: 0,
      influence: buildDragInfluence(node),
    };
    dragging = false;
    canvas.classList.add('dragging');
    tooltip.style.display = 'none';
    return;
  }
  dragging = true;
  lastX = point.x;
  lastY = point.y;
  canvas.classList.add('dragging');
});
window.addEventListener('mouseup', () => {
  if (nodeDrag) {
    const offset = nodeOffsets.get(nodeDrag.node.id) || {x: 0, y: 0, vx: 0, vy: 0};
    offset.vx = Math.max(-12, Math.min(12, nodeDrag.lastDx * 0.8));
    offset.vy = Math.max(-12, Math.min(12, nodeDrag.lastDy * 0.8));
    nodeOffsets.set(nodeDrag.node.id, offset);
    nodeDrag = null;
  } else if (dragging && moved && performance.now() - spinAt < 110) {
    inertiaY = Math.max(-0.05, Math.min(0.05, spinY));
    inertiaX = Math.max(-0.04, Math.min(0.04, spinX));
  }
  dragging = false;
  canvas.classList.remove('dragging');
});
canvas.addEventListener('mousemove', event => {
  touchInteraction();
  const point = relativePoint(event);
  mousePos = {x: point.x, y: point.y, inside: true};
  if (nodeDrag) {
    const offset = nodeOffsets.get(nodeDrag.node.id) || {x: 0, y: 0, vx: 0, vy: 0};
    const nextX = point.x - nodeDrag.startX + nodeDrag.baseX;
    const nextY = point.y - nodeDrag.startY + nodeDrag.baseY;
    const dx = nextX - offset.x;
    const dy = nextY - offset.y;
    offset.x = nextX;
    offset.y = nextY;
    offset.vx = dx;
    offset.vy = dy;
    nodeDrag.lastDx = dx;
    nodeDrag.lastDy = dy;
    nodeOffsets.set(nodeDrag.node.id, offset);
    const dragX = nextX - nodeDrag.baseX;
    const dragY = nextY - nodeDrag.baseY;
    for (const [id, weight] of nodeDrag.influence) {
      const other = nodeOffsets.get(id) || {x: 0, y: 0, vx: 0, vy: 0};
      const targetX = dragX * weight;
      const targetY = dragY * weight;
      const nx = other.x + (targetX - other.x) * 0.5;
      const ny = other.y + (targetY - other.y) * 0.5;
      other.vx = nx - other.x;
      other.vy = ny - other.y;
      other.x = nx;
      other.y = ny;
      nodeOffsets.set(id, other);
    }
    if (Math.hypot(nextX - nodeDrag.baseX, nextY - nodeDrag.baseY) > 3) moved = true;
    needsDraw = true;
    tooltip.style.display = 'none';
    return;
  }
  if (dragging) {
    const dx = point.x - lastX;
    const dy = point.y - lastY;
    if (Math.abs(dx) + Math.abs(dy) > 1) moved = true;
    const stepY = dx * 0.007;
    const stepX = dy * 0.007;
    rotY += stepY;
    rotX = Math.max(-1.35, Math.min(1.35, rotX + stepX));
    spinY = stepY;
    spinX = stepX;
    spinAt = performance.now();
    lastX = point.x;
    lastY = point.y;
    tooltip.style.display = 'none';
    return;
  }
  const node = pick(point.x, point.y);
  if (node !== hovered) {
    hovered = node;
    needsDraw = true;
  }
  if (node) {
    tooltip.textContent = node.path || node.label;
    tooltip.style.display = 'block';
    tooltip.style.left = Math.min(width - 20, point.x + 14) + 'px';
    tooltip.style.top = Math.min(height - 30, point.y + 14) + 'px';
  } else {
    tooltip.style.display = 'none';
  }
});
canvas.addEventListener('mouseenter', () => { mousePos.inside = true; });
canvas.addEventListener('mouseleave', () => {
  mousePos.inside = false;
  hovered = null;
  tooltip.style.display = 'none';
  needsDraw = true;
});

function nearestCluster(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  let best = null;
  let bestDistance = 520;
  for (const id of clusterCenters.keys()) {
    const p = projected.get(id);
    if (!p) continue;
    const distance = Math.hypot(p.x - x, p.y - y);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = id;
    }
  }
  return best;
}

function enterCluster(id) {
  if (!clusterCenters.has(id)) return;
  touchInteraction();
  const center = clusterCenters.get(id);
  focusedCluster = id;
  camTargetX = center.x;
  camTargetY = center.y;
  camTargetZ = center.z;
  zoomTarget = 560;
  needsDraw = true;
  const node = nodeById.get(id);
  document.getElementById('focusBack').hidden = false;
  if (node) {
    hudEl.innerHTML = `聚焦：${escapeHtml(node.label)}<br>滚轮缩小返回全景 · 拖动旋转`;
  }
}

function exitCluster() {
  touchInteraction();
  focusedCluster = null;
  camTargetX = 0;
  camTargetY = 0;
  camTargetZ = 0;
  zoomTarget = 1600;
  needsDraw = true;
  document.getElementById('focusBack').hidden = true;
  updateStats();
}

canvas.addEventListener('click', event => {
  if (moved) return;
  touchInteraction();
  const point = relativePoint(event);
  const node = pick(point.x, point.y);
  if (!node) return;
  needsDraw = true;
  if (node.kind === 'tag') {
    if (node.tagKind === 'category') {
      enterCluster(node.id);
    } else if (node.parent && clusterCenters.has(node.parent)) {
      enterCluster(node.parent);
    }
    return;
  }
  selected = node;
  if (node.kind === 'file' || node.kind === 'search') openDetail(node, event);
});
canvas.addEventListener('dblclick', () => {
  if (focusedCluster) exitCluster();
});
canvas.addEventListener('wheel', event => {
  event.preventDefault();
  touchInteraction();
  lastCameraInput = performance.now();
  zoomTarget = Math.max(420, Math.min(2600, zoomTarget + event.deltaY * 0.9));
  if (focusedCluster && zoomTarget > 1200) exitCluster();
  if (!focusedCluster && event.deltaY < 0 && zoomTarget <= 980) {
    const cluster = nearestCluster(event.clientX, event.clientY);
    if (cluster) enterCluster(cluster);
  }
  needsDraw = true;
}, {passive: false});
window.addEventListener('resize', () => {
  resize();
});

async function revealPath(path) {
  const now = Date.now();
  if (lastReveal.path === path && now - lastReveal.at < 1500) return;
  lastReveal = {path, at: now};
  try {
    const response = await fetch('/api/reveal?path=' + encodeURIComponent(path), {cache: 'no-store'});
    const data = await response.json();
    if (data.error) toast('打开失败：' + data.error);
  } catch (error) {
    toast('打开失败：' + error);
  }
}

function armConfirm(button, label, action, ms) {
  if (button.dataset.armed === '1') {
    button.dataset.armed = '0';
    button.textContent = label;
    action();
    return;
  }
  button.dataset.armed = '1';
  button.textContent = '再点一次确认';
  setTimeout(() => {
    if (button.dataset.armed === '1') {
      button.dataset.armed = '0';
      button.textContent = label;
    }
  }, ms || 4000);
}

function makeDraggable(win, head) {
  let start = null;
  head.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return;
    const rect = win.getBoundingClientRect();
    start = {dx: event.clientX - rect.left, dy: event.clientY - rect.top};
    win.style.left = rect.left + 'px';
    win.style.top = rect.top + 'px';
    win.style.right = 'auto';
    win.style.bottom = 'auto';
    win.dataset.pinned = '1';
    head.setPointerCapture(event.pointerId);
  });
  head.addEventListener('pointermove', event => {
    if (!start) return;
    const x = Math.max(6, Math.min(window.innerWidth - win.offsetWidth - 6, event.clientX - start.dx));
    const y = Math.max(6, Math.min(window.innerHeight - win.offsetHeight - 6, event.clientY - start.dy));
    win.style.left = x + 'px';
    win.style.top = y + 'px';
  });
  head.addEventListener('pointerup', () => { start = null; });
  head.addEventListener('pointercancel', () => { start = null; });
}
makeDraggable(detailWin, document.getElementById('detailHead'));
makeDraggable(organizeWin, document.getElementById('organizeHead'));
makeDraggable(cleanWin, document.getElementById('cleanHead'));

function positionFloat(win, event) {
  if (!event || win.dataset.pinned === '1') return;
  const w = win.offsetWidth || 390;
  const h = win.offsetHeight || 320;
  const x = Math.max(8, Math.min(window.innerWidth - w - 8, event.clientX + 16));
  const y = Math.max(70, Math.min(window.innerHeight - h - 8, event.clientY + 16));
  win.style.left = x + 'px';
  win.style.top = y + 'px';
  win.style.right = 'auto';
  win.style.bottom = 'auto';
}

function openDetail(node, event) {
  selected = node;
  needsDraw = true;
  detailWin.hidden = false;
  detailTitle.textContent = node.label || '文件详情';
  const rows = [];
  rows.push(['名称', escapeHtml(node.label)]);
  if (node.path) rows.push(['路径', `<span class="path">${escapeHtml(node.path)}</span>`]);
  if (node.count != null) rows.push(['数量', fmt(node.count)]);
  if (node.size) rows.push(['大小', escapeHtml(node.size)]);
  if (node.mtime) rows.push(['修改时间', escapeHtml(fmtMtime(node.mtime))]);
  if (node.status) rows.push(['状态', escapeHtml(node.status)]);
  if (node.kind === 'tag') rows.push(['类型', escapeHtml(node.tagKind || 'tag')]);
  if (node.tags && node.tags.length) {
    rows.push(['标签', node.tags.map(tag => `<span class="chip" style="color:${tagColor(tag)}">${escapeHtml(tagName(tag))}</span>`).join('')]);
  }
  detailBody.innerHTML = `<div class="kv">${rows.map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join('')}</div>`;
  // 档案卡增强（2026-11-09）：正文预览 + 语义相似文件（/api/file_info）
  if (node.kind === 'file' && node.path) {
    const extra = document.createElement('div');
    extra.className = 'detail-extra';
    extra.innerHTML = '<div class="muted" style="font-size:11px">读取预览与相似文件…</div>';
    detailBody.appendChild(extra);
    const token = String(node.path);
    fetch('/api/file_info?path=' + encodeURIComponent(node.path), {cache: 'no-store'})
      .then(response => response.json())
      .then(info => {
        if (info.error || !detailBody.contains(extra)) return;
        if (selected !== node) return;
        let html = '';
        if (info.preview) {
          html += `<div class="file-preview">${escapeHtml(info.preview.slice(0, 500))}</div>`;
        }
        if (info.similar && info.similar.length) {
          html += '<div class="similar-head">语义相似文件（点击跳转）</div><div class="similar-list">';
          for (const item of info.similar) {
            html += `<div class="similar-row" data-path="${escapeHtml(item.path)}">` +
              `<span class="similar-name">${escapeHtml(item.name)}</span>` +
              `<span class="similar-score">${Math.round(item.score * 100)}%</span></div>`;
          }
          html += '</div>';
        }
        if (!html) html = '<div class="muted" style="font-size:11px">该文件暂无正文预览与相似文件。</div>';
        extra.innerHTML = html;
        for (const row of extra.querySelectorAll('.similar-row')) {
          row.addEventListener('click', () => showNodeByPath(row.dataset.path));
        }
      })
      .catch(() => {
        if (detailBody.contains(extra)) extra.innerHTML = '';
      });
  }
  if (node.path) {
    const actions = document.createElement('div');
    actions.className = 'detail-actions';
    if (node.kind === 'file') {
      const localBtn = document.createElement('button');
      localBtn.textContent = '局部视图';
      localBtn.className = 'primary';
      localBtn.title = '只看这个文件的语义邻居和同目录文件（Esc 退出）';
      localBtn.addEventListener('click', () => setLocalView(node.id));
      actions.appendChild(localBtn);
    }
    const openBtn = document.createElement('button');
    openBtn.textContent = '打开所在目录';
    openBtn.addEventListener('click', () => revealPath(node.path));
    actions.appendChild(openBtn);
    const copyBtn = document.createElement('button');
    copyBtn.textContent = '复制路径';
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(node.path).then(() => { copyBtn.textContent = '已复制'; });
    });
    actions.appendChild(copyBtn);
    const trashable = (node.tags || []).some(tag => ['rec:duplicate', 'rec:empty', 'rec:temp'].includes(tag));
    if (node.kind === 'file' && trashable) {
      const delBtn = document.createElement('button');
      delBtn.className = 'danger';
      delBtn.textContent = '移到废纸篓';
      delBtn.addEventListener('click', () => armConfirm(delBtn, '移到废纸篓', () => trashPaths([node.path])));
      actions.appendChild(delBtn);
    }
    detailBody.appendChild(actions);
  }
  positionFloat(detailWin, event);
}

function setLocalView(id) {
  const node = nodeById.get(id);
  if (!node) return;
  localViewId = id;
  selected = node;
  const clusterId = node.primary || (node.parent || null);
  if (clusterId && clusterCenters.has(clusterId) && focusedCluster !== clusterId) {
    enterCluster(clusterId);
  }
  detailWin.hidden = true;
  hudEl.innerHTML = `局部视图：${escapeHtml(node.label)} 的关联文件<br>Esc 退出`;
  needsDraw = true;
}
function clearLocalView() {
  if (!localViewId) return;
  localViewId = null;
  updateStats();
  needsDraw = true;
}

function showNodeByPath(path) {
  const target = allNodes.find(item => item.path && samePath(item.path, path));
  if (!target) {
    focusPath(path);
    return;
  }
  const clusterId = target.primary || target.parent || null;
  if (clusterId && clusterCenters.has(clusterId) && focusedCluster !== clusterId) {
    selected = target;
    enterCluster(clusterId);
  }
  openDetail(target);
}

function focusPath(path) {
  let node = allNodes.find(item => item.path && samePath(item.path, path));
  if (!node) {
    node = {
      id: 'search:' + path,
      label: path.split('/').pop() || path,
      kind: 'search',
      path,
      tags: [],
      color: '#fb7185',
      radius: 8,
      x: (Math.random() - .5) * 180,
      y: (Math.random() - .5) * 180,
      z: (Math.random() - .5) * 180,
    };
    extraNodes.push(node);
    allNodes = (graph ? graph.nodes : []).concat(extraNodes);
    nodeById.set(node.id, node);
    extraLinks.push({source: 'root', target: node.id, kind: 'search', color: '#fb7185'});
    rebuildVisible();
  }
  openDetail(node);
  return node;
}

function attachToggle(msg) {
  if (msg.querySelector(':scope > .msg-toggle')) return;
  let inner = msg.querySelector(':scope > .msg-inner');
  if (!inner) {
    inner = document.createElement('div');
    inner.className = 'msg-inner';
    while (msg.firstChild) inner.appendChild(msg.firstChild);
    msg.appendChild(inner);
  }
  const button = document.createElement('button');
  button.className = 'msg-toggle';
  button.textContent = '收起';
  button.title = '收起这条消息';
  button.addEventListener('click', () => {
    msg.classList.toggle('collapsed');
    const collapsed = msg.classList.contains('collapsed');
    button.textContent = collapsed ? '展开' : '收起';
    button.title = collapsed ? '展开这条消息' : '收起这条消息';
    if (!collapsed) inner.scrollTop = 0;
  });
  msg.appendChild(button);
  setTimeout(() => {
    if (!msg.isConnected) return;
    if (msg.scrollHeight <= 110) button.remove();
  }, 30);
}

function typeWriter(target, text, message) {
  return new Promise(resolve => {
    if (!target) {
      resolve();
      return;
    }
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || !text) {
      target.textContent = text;
      if (message) attachToggle(message);
      resolve();
      return;
    }
    target.classList.add('typing');
    const total = text.length;
    const chunk = Math.max(1, Math.ceil(total / 180));
    let index = 0;
    const timer = setInterval(() => {
      if (!target.isConnected) {
        clearInterval(timer);
        resolve();
        return;
      }
      index = Math.min(total, index + chunk);
      target.textContent = text.slice(0, index);
      if (chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 80) {
        chatLog.scrollTop = chatLog.scrollHeight;
      }
      if (index >= total) {
        clearInterval(timer);
        target.classList.remove('typing');
        if (message) attachToggle(message);
        resolve();
      }
    }, 16);
  });
}

function answerMessage(text, metaHtml) {
  const wide = text.length > 180;
  const msg = chatMsg('ai', `<div class="answer"></div>${metaHtml ? `<div class="meta">${metaHtml}</div>` : ''}`, wide);
  const old = msg.querySelector(':scope > .msg-toggle');
  if (old) old.remove();
  const target = msg.querySelector('.answer');
  typeWriter(target, text, msg);
  return msg;
}

const ASSISTANT_STYLES = {
  concise: '说话简短、直接，能一句说清就不说两句',
  warm: '语气温和、有耐心，像朋友聊天',
  humorous: '轻松幽默，但不油滑',
};

function savedAssistant() {
  try {
    return JSON.parse(localStorage.getItem('kb-assistant') || '{}') || {};
  } catch (error) {
    return {};
  }
}

function showWelcome() {
  const saved = savedAssistant();
  const name = saved.name || '你的专属小助手';
  chatMsg('ai', `你好，我是${escapeHtml(name)}。可以搜文件、问知识库；输入 <b>推荐删除</b> 或 <b>推荐整理</b> 也能帮你清理。`, true);
  if (!saved.set) {
    const card = document.createElement('div');
    card.className = 'msg ai wide';
    card.innerHTML = '<div class="meta">第一次使用</div><div>可以给我起个名字、选个说话风格，之后我就按这个风格回复。</div>';
    const button = document.createElement('button');
    button.className = 'primary';
    button.style.marginTop = '6px';
    button.textContent = '设置助手信息';
    button.addEventListener('click', () => openAssistantSettings());
    card.appendChild(button);
    attachToggle(card);
    chatLog.appendChild(card);
    scrollChat();
  }
}

function openAssistantSettings() {
  const saved = savedAssistant();
  const style = ASSISTANT_STYLES[saved.style] ? saved.style : 'concise';
  const card = document.createElement('div');
  card.className = 'msg ai wide settings-card';
  card.innerHTML = `<div class="meta">助手信息</div>
    <label>名字<input id="assistantNameInput" type="text" value="${escapeHtml(saved.name || '小助手')}" maxlength="20"></label>
    <label>说话风格<select id="assistantStyleInput">
      <option value="concise">简洁直接</option>
      <option value="warm">温和耐心</option>
      <option value="humorous">轻松幽默</option>
    </select></label>
    <div class="settings-actions">
      <button class="primary" id="assistantSaveBtn">保存</button>
      <button id="assistantCancelBtn">取消</button>
    </div>`;
  chatLog.appendChild(card);
  scrollChat();
  const nameInput = card.querySelector('#assistantNameInput');
  const styleInput = card.querySelector('#assistantStyleInput');
  styleInput.value = style;
  card.querySelector('#assistantCancelBtn').addEventListener('click', () => card.remove());
  card.querySelector('#assistantSaveBtn').addEventListener('click', async () => {
    const name = nameInput.value.trim() || '小助手';
    const chosen = styleInput.value;
    const persona = ASSISTANT_STYLES[chosen] || ASSISTANT_STYLES.concise;
    try {
      const response = await fetch('/api/assistant', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, persona}),
      });
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      localStorage.setItem('kb-assistant', JSON.stringify({name, style: chosen, set: true}));
      card.remove();
      chatMsg('ai', `好，以后叫我${escapeHtml(name)}，我会${escapeHtml(persona)}。`, false);
    } catch (error) {
      toast('保存失败：' + error);
    }
  });
}

function chatMsg(role, html, wide) {
  const div = document.createElement('div');
  div.className = 'msg ' + role + (wide ? ' wide' : '');
  div.innerHTML = html;
  if (role === 'ai' && !String(html).includes('spinner')) attachToggle(div);
  chatLog.appendChild(div);
  while (chatLog.children.length > 80) chatLog.firstChild.remove();
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

function typing(text) {
  return chatMsg('ai', `<span class="spinner"></span>${escapeHtml(text)}`);
}

function scrollChat() {
  chatLog.scrollTop = chatLog.scrollHeight;
}

function renderHits(items, title) {
  if (!items || !items.length) {
    chatMsg('ai', '没有找到匹配的文件。');
    return;
  }
  const box = document.createElement('div');
  box.className = 'msg ai wide';
  box.innerHTML = `<div class="meta">${escapeHtml(title)} · ${items.length} 个</div>`;
  for (const item of items) {
    const row = document.createElement('div');
    row.className = 'hit';
    row.innerHTML = `<b>${escapeHtml(item.name)}</b>
      <span class="reason">${escapeHtml(item.size_text || '')} ${escapeHtml(item.content_status || '')}</span>
      <div class="reason">${escapeHtml(shortPath(item.path))}</div>`;
    const actions = document.createElement('div');
    actions.className = 'hit-actions';
    const copy = document.createElement('button');
    copy.textContent = '复制路径';
    copy.addEventListener('click', () => copyText(item.path, copy));
    actions.appendChild(copy);
    const open = document.createElement('button');
    open.textContent = '打开目录';
    open.addEventListener('click', () => revealPath(item.path));
    actions.appendChild(open);
    row.appendChild(actions);
    box.appendChild(row);
  }
  attachToggle(box);
  chatLog.appendChild(box);
  scrollChat();
}

function renderSources(sources) {
  if (!sources || !sources.length) return;
  const box = document.createElement('div');
  box.className = 'msg ai wide';
  box.innerHTML = `<div class="meta">引用来源</div>`;
  for (const source of sources) {
    const row = document.createElement('div');
    row.className = 'hit';
    row.innerHTML = `<b>${escapeHtml(source.name)}</b>
      <div class="reason">${escapeHtml(shortPath(source.path))}</div>`;
    const actions = document.createElement('div');
    actions.className = 'hit-actions';
    const copy = document.createElement('button');
    copy.textContent = '复制路径';
    copy.addEventListener('click', () => copyText(source.path, copy));
    actions.appendChild(copy);
    const open = document.createElement('button');
    open.textContent = '打开目录';
    open.addEventListener('click', () => revealPath(source.path));
    actions.appendChild(open);
    row.appendChild(actions);
    box.appendChild(row);
  }
  attachToggle(box);
  chatLog.appendChild(box);
  scrollChat();
}

async function sendChat(value) {
  const text = String(value == null ? chatInput.value : value).trim();
  if (!text) return;
  chatInput.value = '';
  chatMsg('user', escapeHtml(text));
  if (/推荐整理|整理建议|哪些.*整理|整理.*目录/.test(text)) {
    renderOrganizeSuggestions();
    return;
  }
  if (/推荐删除|清理建议|删除建议|哪些.*(删|清理)|列出.*删/.test(text)) {
    renderRecommendations(/列出|明细/.test(text));
    return;
  }
  const identityLike = /^(你是谁|你叫什么|你的名字|介绍一下你|介绍下你|你能做什么|你会什么|who\s+are\s+you)/i.test(text);
  if (identityLike) {
    chatBusy = true;
    const controller = new AbortController();
    chatAbort = controller;
    const pending = typing('思考中，请稍后…');
    try {
      const response = await fetch('/api/query?q=' + encodeURIComponent(text),
        {cache: 'no-store', signal: controller.signal});
      const data = await response.json();
      pending.remove();
      if (data.answer) {
        answerMessage(String(data.answer), '');
      } else {
        chatMsg('ai', '我是你的专属小助手，可以帮你找文件、整理资料。');
      }
    } catch (error) {
      pending.remove();
      if (!error || error.name !== 'AbortError') chatMsg('ai', `失败：${escapeHtml(error)}`);
    } finally {
      if (chatAbort === controller) chatAbort = null;
      chatBusy = false;
    }
    return;
  }
  if (chatBusy) {
    chatMsg('ai', '上一句还在处理，稍等一下。');
    return;
  }
  chatBusy = true;
  const controller = new AbortController();
  chatAbort = controller;
  const questionLike = /[?？]|吗|怎么|如何|什么|为什么|哪|谁|多少|是不是|能否|可不可以|介绍|总结|区别|帮我|推荐一下/.test(text);
  let pending = typing('思考中，请稍后…');
  try {
    const searchResponse = await fetch('/api/search?q=' + encodeURIComponent(text),
      {cache: 'no-store', signal: controller.signal});
    const results = await searchResponse.json();
    pending.remove();
    const found = Array.isArray(results) ? results : [];
    if (found.length) renderHits(found.slice(0, 6), '找到的文件');
    if (!questionLike && found.length) return;
    pending = typing('思考中，请稍后…');
    const response = await fetch('/api/query?q=' + encodeURIComponent(text),
      {cache: 'no-store', signal: controller.signal});
    const data = await response.json();
    pending.remove();
    if (data.error && !data.answer) {
      chatMsg('ai', `失败：${escapeHtml(data.error)}`);
      return;
    }
    if (data.answer) {
      const answerText = String(data.answer || '');
      const meta = `${escapeHtml(data.ask_mode || '')}${data.model ? ' · ' + escapeHtml(data.model) : ''}`;
      answerMessage(answerText, meta);
      renderSources(data.sources || []);
    } else if (!found.length) {
      renderHits(data.results || [], '找到的文件');
    }
  } catch (error) {
    pending.remove();
    if (error && error.name === 'AbortError') return;
    chatMsg('ai', `失败：${escapeHtml(error)}`);
  } finally {
    if (chatAbort === controller) chatAbort = null;
    chatBusy = false;
  }
}

function renderRecommendations(expand) {
  if (!graph) {
    chatMsg('ai', '索引数据还在加载，请稍等。');
    return;
  }
  const recs = (graph.recommendations || []).filter(item => item.count > 0);
  const box = document.createElement('div');
  box.className = 'msg ai wide';
  if (!recs.length) {
    box.innerHTML = '<div class="meta">推荐删除</div>目前没有可清理项。';
    attachToggle(box);
  chatLog.appendChild(box);
    scrollChat();
    return;
  }
  box.innerHTML = `<div class="meta">推荐删除 · 移到废纸篓，可从废纸篓恢复</div>`;
  const deletable = recs.filter(rec => ['duplicate', 'empty', 'temp'].includes(rec.id));
  const allPaths = [];
  for (const rec of deletable) for (const item of rec.items || []) allPaths.push(item.path);
  if (allPaths.length) {
    const topActions = document.createElement('div');
    topActions.className = 'rec-actions';
    const allBtn = document.createElement('button');
    allBtn.className = 'danger';
    allBtn.textContent = `一键删除列表中的 ${allPaths.length} 项`;
    allBtn.addEventListener('click', () => armConfirm(allBtn, `一键删除列表中的 ${allPaths.length} 项`,
      () => trashPaths(allPaths)));
    topActions.appendChild(allBtn);
    box.appendChild(topActions);
  }
  for (const rec of recs) {
    const block = document.createElement('div');
    block.className = 'rec-block';
    block.innerHTML = `<div class="rec-head"><b>${escapeHtml(rec.label)}</b>
      <span class="n">${fmt(rec.count)} 项 · ${escapeHtml(rec.bytes_text || '')}</span></div>
      <p>${escapeHtml(rec.hint || '')}</p>`;
    const actions = document.createElement('div');
    actions.className = 'rec-actions';
    const items = rec.items || [];
    const boxItems = document.createElement('div');
    boxItems.className = 'rec-items';
    boxItems.hidden = !expand;
    const toggle = document.createElement('button');
    toggle.textContent = expand ? '收起明细' : '列出明细';
    toggle.addEventListener('click', () => {
      boxItems.hidden = !boxItems.hidden;
      toggle.textContent = boxItems.hidden ? '列出明细' : '收起明细';
    });
    actions.appendChild(toggle);
    if (['duplicate', 'empty', 'temp'].includes(rec.id) && items.length) {
      const groupBtn = document.createElement('button');
      groupBtn.className = 'danger';
      groupBtn.textContent = `删除这组 ${items.length} 项`;
      groupBtn.addEventListener('click', () => armConfirm(groupBtn, `删除这组 ${items.length} 项`,
        () => trashPaths(items.map(item => item.path))));
      actions.appendChild(groupBtn);
    }
    block.appendChild(actions);
    for (const item of items) {
      const row = document.createElement('div');
      row.className = 'rec-item';
      row.innerHTML = `<div class="reason">${escapeHtml(shortPath(item.path))}</div>
        <div class="reason">${escapeHtml(item.reason || '')} ${escapeHtml(item.size || '')}</div>`;
      const buttons = document.createElement('div');
      buttons.className = 'rec-btns';
      const copy = document.createElement('button');
      copy.textContent = '复制路径';
      copy.addEventListener('click', () => copyText(item.path, copy));
      buttons.appendChild(copy);
      if (rec.id === 'cache') {
        const open = document.createElement('button');
        open.textContent = '打开目录';
        open.addEventListener('click', () => revealPath(item.path));
        buttons.appendChild(open);
      } else {
        const del = document.createElement('button');
        del.className = 'danger';
        del.textContent = '删除';
        del.addEventListener('click', () => armConfirm(del, '删除', () => trashPaths([item.path])));
        buttons.appendChild(del);
      }
      row.appendChild(buttons);
      boxItems.appendChild(row);
    }
    block.appendChild(boxItems);
    box.appendChild(block);
  }
  attachToggle(box);
  chatLog.appendChild(box);
  scrollChat();
}

function renderOrganizeSuggestions() {
  if (!graph) {
    chatMsg('ai', '索引数据还在加载，请稍等。');
    return;
  }
  const list = graph.organize_suggestions || [];
  const box = document.createElement('div');
  box.className = 'msg ai wide';
  box.innerHTML = '<div class="meta">推荐整理的目录</div>';
  if (!list.length) {
    box.innerHTML += '<div class="muted">暂时没有明显需要整理的目录。</div>';
    attachToggle(box);
  chatLog.appendChild(box);
    scrollChat();
    return;
  }
  for (const item of list) {
    const row = document.createElement('div');
    row.className = 'hit';
    row.innerHTML = `<b>${escapeHtml(item.dir)}</b>
      <span class="meta">${fmt(item.files)} 个文件 · ${fmt(item.images)} 张图片 · ${escapeHtml(item.bytes_text || '')}</span>
      <div class="reason">${escapeHtml(item.reason || '')}</div>
      <div class="reason">${escapeHtml(shortPath(item.path))}</div>`;
    const actions = document.createElement('div');
    actions.className = 'hit-actions';
    const organize = document.createElement('button');
    organize.className = 'primary';
    organize.textContent = '整理';
    organize.addEventListener('click', () => openOrganize(item.path, item.images >= 3 ? 'image' : 'type'));
    actions.appendChild(organize);
    const copy = document.createElement('button');
    copy.textContent = '复制路径';
    copy.addEventListener('click', () => copyText(item.path, copy));
    actions.appendChild(copy);
    const open = document.createElement('button');
    open.textContent = '打开目录';
    open.addEventListener('click', () => revealPath(item.path));
    actions.appendChild(open);
    row.appendChild(actions);
    box.appendChild(row);
  }
  attachToggle(box);
  chatLog.appendChild(box);
  scrollChat();
}

async function trashPaths(paths, onDone) {
  const list = (paths || []).filter(Boolean);
  if (!list.length) return;
  cleanBusy = true;
  cleanResult = null;
  if (!cleanWin.hidden) renderCleanWindow();
  let data = {};
  try {
    const response = await fetch('/api/trash', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({paths: list, confirm: true}),
    });
    data = await response.json();
  } catch (error) {
    cleanBusy = false;
    cleanResult = {error: String(error)};
    if (!cleanWin.hidden) renderCleanWindow();
    toast('删除失败：' + error);
    return;
  }
  if (data.error) {
    cleanBusy = false;
    cleanResult = {error: data.error};
    if (!cleanWin.hidden) renderCleanWindow();
    toast('删除失败：' + data.error);
    return;
  }
  cleanBusy = false;
  cleanResult = {moved: data.moved_count || 0, failed: data.failed_count || 0};
  const message = `已移到废纸篓 ${data.moved_count || 0} 项` + (data.failed_count ? `，失败 ${data.failed_count} 项` : '');
  toast(message);
  chatMsg('ai', escapeHtml(message), true);
  applyLocalTrash(data.moved || []);
  if (!cleanWin.hidden) renderCleanWindow();
  if (selected && !nodeById.has(selected.id)) {
    detailWin.hidden = true;
    selected = null;
  }
  if (onDone) onDone(data);
  pollStatus(true);
}

function applyLocalTrash(moved) {
  if (!graph || !moved.length) return;
  const paths = new Set(moved.map(item => String(item.path)));
  const removedIds = new Set();
  for (const node of graph.nodes) {
    if (node.kind === 'file' && paths.has(String(node.path))) removedIds.add(node.id);
  }
  graph.nodes = graph.nodes.filter(node => !removedIds.has(node.id));
  graph.links = graph.links.filter(link => !removedIds.has(link.source) && !removedIds.has(link.target));
  for (const id of removedIds) nodeById.delete(id);
  graph.summary.files = Math.max(0, (graph.summary.files || 0) - removedIds.size);
  graph.summary.nodes = graph.nodes.length;
  const reasonToId = {'重复文件': 'duplicate', '空文件': 'empty', '临时文件': 'temp'};
  const dec = {};
  for (const item of moved) {
    const id = reasonToId[item.reason];
    if (id) dec[id] = (dec[id] || 0) + 1;
  }
  const movedPaths = new Set(moved.map(item => String(item.path)));
  for (const rec of graph.recommendations || []) {
    rec.count = Math.max(0, (rec.count || 0) - (dec[rec.id] || 0));
    rec.items = (rec.items || []).filter(item => !movedPaths.has(String(item.path)));
  }
  allNodes = graph.nodes.concat(extraNodes);
  allLinks = graph.links.concat(extraLinks);
  rebuildVisible();
  updateStats();
}

function renderCleanWindow() {
  if (!cleanBody) return;
  if (!graph) {
    cleanBody.innerHTML = '<div class="clean-note">索引还在加载，请稍等…</div>';
    return;
  }
  const recs = (graph.recommendations || []).filter(item => item.count > 0);
  const deletable = recs.filter(item => ['duplicate', 'empty', 'temp'].includes(item.id));
  const totalBytes = deletable.reduce((sum, item) => sum + Number(item.bytes || 0), 0);
  const allPaths = [];
  for (const rec of deletable) {
    for (const item of rec.items || []) allPaths.push(item.path);
  }
  cleanBody.innerHTML = `<div class="clean-summary"><span>可移除的重复 / 空 / 临时文件</span>
    <b>${formatBytes(totalBytes)}</b></div>`;
  const cacheRec = recs.find(item => item.id === 'cache');
  if (cacheRec) {
    cleanBody.innerHTML += `<div class="clean-note" style="border:0;padding-top:0;margin-top:0">
      工具/缓存目录另占 ${escapeHtml(cacheRec.bytes_text || '')}，只建议移出索引或手动清理，不在这里删除。</div>`;
  }
  cleanBody.innerHTML += `<div class="clean-note" style="border:0;padding-top:0;margin-top:0">
    命令行跑 <b>kb scan-junk</b> 可深度扫描下载/临时/缓存目录（不限索引范围，通用规则，只报告不自动删）。</div>`;
  if (cleanBusy) {
    cleanBody.innerHTML += `<div class="clean-loading"><span class="spinner"></span>
      <span>正在移到废纸篓，请稍候…</span><div class="bar"><i></i></div></div>`;
    return;
  }
  if (cleanResult) {
    if (cleanResult.error) {
      cleanBody.innerHTML += `<div class="clean-result warn">清理失败：${escapeHtml(cleanResult.error)}</div>`;
    } else {
      const failed = Number(cleanResult.failed || 0);
      const extra = failed ? `，失败 ${failed} 项` : '';
      cleanBody.innerHTML += `<div class="clean-result${failed ? ' warn' : ''}">已清理 ${cleanResult.moved || 0} 项${extra}；可在废纸篓恢复。</div>`;
    }
  }
  if (!recs.length) {
    cleanBody.innerHTML += '<div class="clean-note">目前没有可清理项，空间挺干净。</div>';
    return;
  }
  for (const rec of recs) {
    const row = document.createElement('div');
    row.className = 'clean-row';
    const items = rec.items || [];
    row.innerHTML = `<span class="clean-name">${escapeHtml(rec.label)}</span>
      <span class="clean-num">${fmt(rec.count)} 项<br>${escapeHtml(rec.bytes_text || '')}</span>`;
    const action = document.createElement('button');
    if (rec.id === 'cache') {
      action.textContent = '仅建议';
      action.disabled = true;
    } else if (items.length) {
      action.className = 'danger';
      action.textContent = `清理 ${items.length} 项`;
      action.addEventListener('click', () => armConfirm(
        action,
        `清理 ${items.length} 项`,
        () => trashPaths(items.map(item => item.path)),
      ));
    } else {
      action.textContent = '无列表';
      action.disabled = true;
    }
    row.appendChild(action);
    cleanBody.appendChild(row);
  }
  if (allPaths.length) {
    const actions = document.createElement('div');
    actions.className = 'clean-actions';
    const allButton = document.createElement('button');
    allButton.className = 'danger';
    allButton.textContent = `一键清理列表中的 ${allPaths.length} 项`;
    allButton.addEventListener('click', () => armConfirm(
      allButton,
      `一键清理列表中的 ${allPaths.length} 项`,
      () => trashPaths(allPaths),
    ));
    actions.appendChild(allButton);
    cleanBody.appendChild(actions);
  }
  const note = document.createElement('div');
  note.className = 'clean-note';
  note.textContent = '删除会把文件移到系统废纸篓，可从废纸篓恢复；只处理聊天/面板列出的重复、空、临时文件，缓存目录只给建议。';
  cleanBody.appendChild(note);
}

function renderOrganizeFolderList() {
  const box = document.getElementById('orgSuggestions');
  const count = document.getElementById('orgSuggestCount');
  if (!box) return;
  if (!graph) {
    box.innerHTML = '<div class="org-suggest-empty">索引加载中…</div>';
    count.textContent = '';
    return;
  }
  const strategy = orgStrategyEl.value;
  const all = graph.organize_suggestions || [];
  const list = strategy === 'image'
    ? all.filter(item => item.images >= 3).sort((a, b) => b.images - a.images)
    : all.filter(item => item.files >= 20).sort((a, b) => b.files - a.files);
  count.textContent = list.length ? `${list.length} 个` : '暂无';
  box.innerHTML = '';
  if (!list.length) {
    box.innerHTML = '<div class="org-suggest-empty">没有符合当前方式的文件夹</div>';
    return;
  }
  for (const item of list.slice(0, 12)) {
    const row = document.createElement('div');
    row.className = 'suggest-row' + (orgPathEl.value.trim() === item.path ? ' active' : '');
    const detail = strategy === 'image'
      ? `${fmt(item.images)} 图 · ${fmt(item.files)} 个`
      : `${fmt(item.files)} 个文件`;
    row.innerHTML = `<span class="dir" title="${escapeHtml(item.path)}">${escapeHtml(item.dir)}</span>
      <span class="n">${detail}</span>`;
    row.title = `${item.reason || ''}\n${item.path}`;
    row.addEventListener('click', () => {
      orgPathEl.value = item.path;
      renderOrganizeFolderList();
    });
    box.appendChild(row);
  }
}

function openOrganize(path, strategy) {
  cleanWin.hidden = true;
  organizeWin.hidden = false;
  if (path) orgPathEl.value = path;
  if (strategy) orgStrategyEl.value = strategy;
  renderOrganizeFolderList();
  if (organizeWin.dataset.pinned !== '1') {
    organizeWin.style.right = '24px';
    organizeWin.style.top = '82px';
    organizeWin.style.left = 'auto';
    organizeWin.style.bottom = 'auto';
  }
}

async function runOrganize(action) {
  if (action === 'plan' && !orgPathEl.value.trim()) {
    toast('请填写要整理的目录');
    return;
  }
  orgResultEl.innerHTML = '<div class="muted"><span class="spinner"></span>处理中，图片多时需要等一会儿…</div>';
  const payload = {
    action,
    path: orgPathEl.value.trim(),
    strategy: orgStrategyEl.value,
    recursive: orgRecursiveEl.checked,
  };
  if (action === 'apply') payload.plan_id = orgPlanId;
  if (action === 'undo') payload.plan_id = orgAppliedId;
  let data = {};
  try {
    const response = await fetch('/api/organize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    data = await response.json();
  } catch (error) {
    orgResultEl.innerHTML = `<div class="answer">失败：${escapeHtml(error)}</div>`;
    return;
  }
  if (data.error) {
    orgResultEl.innerHTML = `<div class="answer">失败：${escapeHtml(data.error)}</div>`;
    return;
  }
  if (action === 'plan') {
    orgPlanId = data.plan_id || '';
    orgApplyBtn.disabled = !(data.count > 0);
    if (!data.count) {
      orgResultEl.innerHTML = '<div class="muted">这个目录没有需要移动的文件。</div>';
      return;
    }
    const cats = (data.categories || []).map(item => `<span class="chip">${escapeHtml(item.name || '其他')} · ${fmt(item.count)}</span>`).join('');
    const rows = (data.operations || []).map(op => `<div class="org-op"><span>${escapeHtml(baseName(op.src))}</span>
      <span class="cat">${escapeHtml(op.category || '')}</span></div>`).join('');
    orgResultEl.innerHTML = `<div class="kv" style="margin-top:10px"><span>计划</span><span>${escapeHtml(data.plan_id || '')}</span>
      <span>目录</span><span class="path">${escapeHtml(data.target || '')}</span>
      <span>操作</span><span>${fmt(data.count)} 个${data.truncated ? '（列表只显示前 40 个）' : ''}</span></div>
      <div>${cats}</div><div class="org-ops">${rows}</div>
      <div class="muted" style="margin-top:6px">确认无误后点“执行”；执行后可用“撤销”还原。</div>`;
    return;
  }
  if (action === 'apply') {
    orgAppliedId = orgPlanId;
    orgUndoBtn.disabled = !orgAppliedId;
    orgApplyBtn.disabled = true;
    const failed = (data.failed || []).slice(0, 8).map(item => `<div class="org-op"><span>${escapeHtml(baseName(item.src))}</span>
      <span class="cat" style="color:#fca5a5">${escapeHtml(item.error || '失败')}</span></div>`).join('');
    orgResultEl.innerHTML = `<div class="answer">已执行：成功 ${fmt(data.applied_count)} 个，失败 ${fmt(data.failed_count)} 个。</div>
      ${failed ? `<div class="org-ops">${failed}</div>` : ''}`;
    toast(`整理完成：成功 ${data.applied_count || 0}，失败 ${data.failed_count || 0}`);
    pollStatus(true);
    return;
  }
  orgAppliedId = '';
  orgUndoBtn.disabled = true;
  orgResultEl.innerHTML = `<div class="answer">已撤销：恢复 ${fmt(data.restored_count)} 个，失败 ${fmt(data.failed_count)} 个。</div>`;
  toast(`撤销完成：恢复 ${data.restored_count || 0}，失败 ${data.failed_count || 0}`);
  pollStatus(true);
}

async function refreshIndex() {
  const response = await fetch('/api/refresh', {method: 'POST'});
  const data = await response.json();
  toast(data.message || '已开始更新索引，完成后会自动刷新');
  pollStatus(true);
}

function pollStatus(force) {
  clearTimeout(statusTimer);
  fetch('/api/status', {cache: 'no-store'}).then(r => r.json()).then(data => {
    embedInfo = data.embedding || embedInfo;
    if (data.indexing) {
      hudEl.innerHTML = '正在更新索引… 页面会自动刷新';
    } else {
      renderStats();
      if (force || (data.version && data.version !== lastVersion)) {
        loadGraph();
      }
    }
    statusTimer = setTimeout(pollStatus, 5000);
  }).catch(() => {
    statusTimer = setTimeout(pollStatus, 5000);
  });
}

document.getElementById('chatSend').addEventListener('click', () => sendChat());
chatInput.addEventListener('keydown', event => {
  if (event.key === 'Enter') sendChat();
});
for (const button of document.querySelectorAll('[data-quick]')) {
  button.addEventListener('click', () => sendChat(button.dataset.quick));
}
document.getElementById('refreshBtn').addEventListener('click', refreshIndex);
document.getElementById('rotateBtn').addEventListener('click', event => {
  autoRotate = !autoRotate;
  event.target.textContent = autoRotate ? '暂停旋转' : '继续旋转';
  updateStats();
});
const minimalBtn = document.getElementById('minimalBtn');
function syncMinimalBtn() {
  if (!minimalBtn) return;
  minimalBtn.textContent = minimalMode ? '华丽' : '极简';
  minimalBtn.classList.toggle('active', minimalMode);
}
if (minimalBtn) {
  syncMinimalBtn();
  minimalBtn.addEventListener('click', () => {
    minimalMode = !minimalMode;
    try { localStorage.setItem('kb-minimal', minimalMode ? '1' : '0'); } catch (error) { /* 隐私模式忽略 */ }
    syncMinimalBtn();
    needsDraw = true;
  });
}
const timeBtn = document.getElementById('timeBtn');
function syncTimeBtn() {
  if (!timeBtn) return;
  timeBtn.classList.toggle('active', timeColorMode);
  timeBtn.textContent = timeColorMode ? '分类色' : '时间着色';
}
if (timeBtn) {
  syncTimeBtn();
  timeBtn.addEventListener('click', () => {
    timeColorMode = !timeColorMode;
    try { localStorage.setItem('kb-timecolor', timeColorMode ? '1' : '0'); } catch (error) { /* 忽略 */ }
    syncTimeBtn();
    needsDraw = true;
  });
}
document.getElementById('organizeBtn').addEventListener('click', () => {
  if (organizeWin.hidden) openOrganize();
  else organizeWin.hidden = true;
});
document.getElementById('organizeClose').addEventListener('click', () => { organizeWin.hidden = true; });
document.getElementById('cleanupBtn').addEventListener('click', () => {
  if (cleanWin.hidden) {
    organizeWin.hidden = true;
    cleanWin.hidden = false;
    cleanBusy = false;
    cleanResult = null;
    renderCleanWindow();
  } else {
    cleanWin.hidden = true;
  }
});
document.getElementById('cleanClose').addEventListener('click', () => { cleanWin.hidden = true; });
document.getElementById('detailClose').addEventListener('click', () => { detailWin.hidden = true; });
document.getElementById('orgPlanBtn').addEventListener('click', () => runOrganize('plan'));
orgStrategyEl.addEventListener('change', renderOrganizeFolderList);
orgApplyBtn.addEventListener('click', () => armConfirm(orgApplyBtn, '执行', () => runOrganize('apply')));
orgUndoBtn.addEventListener('click', () => armConfirm(orgUndoBtn, '撤销', () => runOrganize('undo')));

function makeAssistantDraggable() {
  const head = document.getElementById('assistantHead');
  let start = null;
  head.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return;
    const rect = assistantPanel.getBoundingClientRect();
    start = {dx: event.clientX - rect.left, dy: event.clientY - rect.top};
    if (assistantPanel.classList.contains('docked')) {
      assistantPanel.classList.remove('docked');
      assistantPanel.style.height = rect.height + 'px';
    }
    assistantPanel.style.left = rect.left + 'px';
    assistantPanel.style.top = rect.top + 'px';
    assistantPanel.style.right = 'auto';
    head.setPointerCapture(event.pointerId);
  });
  head.addEventListener('pointermove', event => {
    if (!start) return;
    const x = Math.max(6, Math.min(window.innerWidth - assistantPanel.offsetWidth - 6, event.clientX - start.dx));
    const y = Math.max(6, Math.min(window.innerHeight - assistantPanel.offsetHeight - 6, event.clientY - start.dy));
    assistantPanel.style.left = x + 'px';
    assistantPanel.style.top = y + 'px';
  });
  head.addEventListener('pointerup', () => { start = null; });
  head.addEventListener('pointercancel', () => { start = null; });
}
makeAssistantDraggable();

(function makeAssistantResizable() {
  const handle = document.getElementById('assistantResize');
  let start = null;
  handle.addEventListener('pointerdown', event => {
    event.preventDefault();
    event.stopPropagation();
    const rect = assistantPanel.getBoundingClientRect();
    start = {x: event.clientX, y: event.clientY, w: rect.width, h: rect.height};
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove', event => {
    if (!start) return;
    const width = Math.max(300, Math.min(window.innerWidth - 20, start.w + event.clientX - start.x));
    const height = Math.max(220, Math.min(window.innerHeight - 90, start.h + event.clientY - start.y));
    assistantPanel.style.width = width + 'px';
    assistantPanel.style.height = height + 'px';
  });
  handle.addEventListener('pointerup', () => { start = null; });
  handle.addEventListener('pointercancel', () => { start = null; });
})();

document.getElementById('assistantSettings').addEventListener('click', openAssistantSettings);
document.getElementById('assistantHead').addEventListener('dblclick', event => {
  if (event.target.closest('button')) return;
  document.getElementById('assistantMin').click();
});
document.getElementById('assistantClear').addEventListener('click', () => {
  if (chatAbort) {
    chatAbort.abort();
    chatAbort = null;
  }
  chatBusy = false;
  chatLog.innerHTML = '';
  welcomed = true;
  chatMsg('ai', '对话已清空，可以重新提问。', true);
  toast('已清空对话');
});

document.getElementById('assistantDock').addEventListener('click', () => {
  const dock = document.getElementById('assistantDock');
  assistantPanel.hidden = false;
  const docked = assistantPanel.classList.toggle('docked');
  assistantPanel.style.left = '';
  assistantPanel.style.top = '';
  assistantPanel.style.right = 'auto';
  assistantPanel.style.height = '';
  dock.textContent = docked ? '⇥' : '⇤';
  dock.title = docked ? '取消左侧固定' : '固定到左侧';
});
document.getElementById('assistantMin').addEventListener('click', event => {
  assistantPanel.classList.toggle('min');
  const minimized = assistantPanel.classList.contains('min');
  if (minimized) {
    assistantPanel.dataset.prevHeight = assistantPanel.style.height || '';
    assistantPanel.style.height = '';
  } else if (assistantPanel.dataset.prevHeight) {
    assistantPanel.style.height = assistantPanel.dataset.prevHeight;
  }
  event.target.textContent = minimized ? '▢' : '—';
});
document.getElementById('assistantHide').addEventListener('click', () => { assistantPanel.hidden = true; });
document.getElementById('assistantToggle').addEventListener('click', () => {
  assistantPanel.hidden = !assistantPanel.hidden;
  if (!assistantPanel.hidden) assistantPanel.classList.remove('min');
});

const fullscreenBtn = document.getElementById('fullscreenBtn');
async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else if (document.documentElement.requestFullscreen) {
      await document.documentElement.requestFullscreen();
    }
  } catch (error) {
    toast('全屏失败：' + error);
  }
}
fullscreenBtn.addEventListener('click', toggleFullscreen);
document.addEventListener('fullscreenchange', () => {
  fullscreenBtn.textContent = document.fullscreenElement ? '退出全屏' : '全屏';
  setTimeout(resize, 80);
  setTimeout(resize, 300);
});
document.addEventListener('keydown', event => {
  if (event.key.toLowerCase() !== 'f' || event.metaKey || event.ctrlKey || event.altKey) return;
  if (/input|textarea|select/i.test(document.activeElement.tagName)) return;
  event.preventDefault();
  toggleFullscreen();
});

function startLoadingProgress() {
  const box = document.getElementById('loading');
  const bar = document.getElementById('progressBar');
  const percent = document.getElementById('loadingPercent');
  const text = document.getElementById('loadingText');
  box.style.display = 'flex';
  bar.style.width = '2%';
  percent.textContent = '2%';
  text.textContent = '正在读取知识库索引…';
  let shown = 2;
  const tick = async () => {
    try {
      const response = await fetch('/api/status', {cache: 'no-store'});
      const data = await response.json();
      const progress = data.progress || {};
      if (progress.active || progress.percent) {
        const target = Math.max(2, Math.min(100, Number(progress.percent) || 0));
        if (target > shown) shown = target;
        else if (progress.active && shown < 28) shown = Math.min(28, shown + 0.9);
        const value = Math.round(shown);
        bar.style.width = value + '%';
        percent.textContent = value + '%';
        if (progress.message) text.textContent = progress.message;
      }
    } catch (error) {
      // 首次建图时服务可能刚启动，忽略轮询失败
    }
  };
  tick();
  clearInterval(loadingTimer);
  loadingTimer = setInterval(tick, 450);
}

function stopLoadingProgress() {
  clearInterval(loadingTimer);
  loadingTimer = null;
  const bar = document.getElementById('progressBar');
  const percent = document.getElementById('loadingPercent');
  const text = document.getElementById('loadingText');
  bar.style.width = '100%';
  percent.textContent = '100%';
  text.textContent = '索引已就绪';
  setTimeout(() => { document.getElementById('loading').style.display = 'none'; }, 320);
}

async function refreshSelfcheck() {
  const badge = document.getElementById('selfcheckBadge');
  try {
    const response = await fetch('/api/selfcheck', {cache: 'no-store'});
    const data = await response.json();
    if (!data.available) {
      badge.className = '';
      badge.innerHTML = '<span class="dot"></span>自检 暂无记录';
      return;
    }
    const time = String(data.time || '').slice(11, 16);
    badge.className = data.status || '';
    badge.innerHTML = `<span class="dot"></span>自检 ${time} ${String(data.status || '').toUpperCase()}`
      + ` · 文件 ${fmt(data.files)} · 服务 ${fmt(data.services)}`;
    const warnings = (data.warnings || []).map(item => `${item.name}: ${item.message}`);
    badge.title = warnings.length ? warnings.join('\n') : '系统自检全部正常';
  } catch (error) {
    badge.className = '';
    badge.innerHTML = '<span class="dot"></span>自检 读取失败';
  }
}

document.getElementById('focusBack').addEventListener('click', exitCluster);

resize();
startLoadingProgress();
refreshSelfcheck();
selfcheckTimer = setInterval(refreshSelfcheck, 60000);
/* 启动：加载失败自动重试（服务重启、数据库瞬间锁都可能导致首次加载失败） */
async function bootMap() {
  const ok = await loadGraph();
  if (ok === false) {
    document.getElementById('loadingText').textContent = '连接服务失败，3秒后自动重试…';
    setTimeout(bootMap, 3000);
    return;
  }
  allNodes = (graph ? graph.nodes : []).concat(extraNodes);
  allLinks = (graph ? graph.links : []).concat(extraLinks);
  renderLegend();
  pollStatus(false);
  stopLoadingProgress();
}
window.addEventListener('error', event => {
  const loading = document.getElementById('loading');
  const text = document.getElementById('loadingText');
  if (text && loading && loading.style.display !== 'none') {
    text.textContent = '脚本错误：' + (event.message || '未知错误') + '（可刷新重试）';
  }
});
bootMap();

/* ---- 分类图例（P0）：点击聚焦分类，悬停压暗其他分类 ---- */
function renderLegend() {
  const legendEl = document.getElementById('legend');
  if (!legendEl) return;
  const cats = (graph ? graph.nodes : []).filter(n => n.tagKind === 'category');
  legendEl.innerHTML = cats.map(n =>
    `<div class="legend-item" data-id="${n.id}">` +
    `<i style="background:${n.color};color:${n.color}"></i>` +
    `<span>${escapeHtml(n.label)}</span><em>${fmt(n.count || 0)}</em></div>`
  ).join('');
  for (const item of legendEl.querySelectorAll('.legend-item')) {
    item.addEventListener('mouseenter', () => {
      legendDimCluster = item.dataset.id;
      needsDraw = true;
    });
    item.addEventListener('mouseleave', () => {
      legendDimCluster = null;
      needsDraw = true;
    });
    item.addEventListener('click', () => {
      legendDimCluster = null;
      enterCluster(item.dataset.id);
    });
  }
}

/* ---- 脑图内搜索定位（P0）：匹配节点并飞行定位 ---- */
(function initMapSearch() {
  const input = document.getElementById('mapSearchInput');
  const drop = document.getElementById('mapSearchDrop');
  if (!input || !drop) return;
  let matches = [];
  const KIND_LABEL = { file: '文件', category: '分类', ext: '类型', dir: '目录', project: '项目', recommend: '推荐', other: '其他' };
  const doSearch = () => {
    const q = input.value.trim().toLowerCase();
    mapQuery = q;
    needsDraw = true;
    if (!q) { drop.hidden = true; matches = []; return; }
    matches = [];
    for (const node of (graph ? graph.nodes : [])) {
      if (matches.length >= 8) break;
      const label = String(node.label || '').toLowerCase();
      const path = String(node.path || '').toLowerCase();
      if (label.includes(q) || path.includes(q)) matches.push(node);
    }
    if (!matches.length) {
      drop.innerHTML = '<div class="ms-empty">没有匹配的节点</div>';
    } else {
      drop.innerHTML = matches.map((n, i) => {
        const kind = n.tagKind || n.kind || '';
        const kindText = KIND_LABEL[kind] || kind;
        const pathText = n.path ? String(n.path).split('/').slice(-2).join('/') : (n.count != null ? fmt(n.count) + ' 项' : '');
        return `<div class="ms-item${i === 0 ? ' active' : ''}" data-i="${i}">` +
          `<span class="ms-kind">${escapeHtml(kindText)}</span>` +
          `<span>${escapeHtml(n.label)}</span>` +
          `<span class="ms-path">${escapeHtml(pathText)}</span></div>`;
      }).join('');
      for (const el of drop.querySelectorAll('.ms-item')) {
        el.addEventListener('mousedown', ev => { ev.preventDefault(); locate(matches[Number(el.dataset.i)]); });
      }
    }
    drop.hidden = false;
  };
  const locate = node => {
    if (!node) return;
    input.blur();
    drop.hidden = true;
    const clusterId = node.tagKind === 'category' ? node.id : (node.primary || node.parent);
    if (clusterId && clusterCenters.has(clusterId)) {
      selected = nodeById.get(node.id) || node;
      enterCluster(clusterId);
      needsDraw = true;
    }
  };
  input.addEventListener('input', doSearch);
  input.addEventListener('focus', doSearch);
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); locate(matches[0]); }
    if (ev.key === 'Escape') {
      input.value = ''; mapQuery = ''; needsDraw = true;
      drop.hidden = true; input.blur();
    }
  });
  input.addEventListener('blur', () => setTimeout(() => { drop.hidden = true; }, 150));
})();

/* ---- 命令面板（P1）：Ctrl/⌘+P，Obsidian 式键盘入口 ---- */
const paletteWin = document.getElementById('palette');
const paletteInput = document.getElementById('paletteInput');
const paletteList = document.getElementById('paletteList');
let paletteItems = [];

function paletteCommands() {
  const cmds = [];
  if (localViewId) cmds.push({title: '退出局部视图', hint: '视图', run: clearLocalView});
  if (selected && selected.kind === 'file') {
    cmds.push({title: `局部视图：${selected.label}`, hint: '视图', run: () => setLocalView(selected.id)});
  }
  cmds.push(
    {title: focusedCluster ? '返回全景' : '回到球心', hint: '视图', run: () => { exitCluster(); clearLocalView(); }},
    {title: autoRotate ? '暂停旋转' : '继续旋转', hint: '视图', run: () => document.getElementById('rotateBtn').click()},
    {title: minimalMode ? '关闭极简模式' : '极简模式', hint: '视图', run: () => minimalBtn && minimalBtn.click()},
    {title: timeColorMode ? '恢复分类着色' : '按时间着色', hint: '视图', run: () => timeBtn && timeBtn.click()},
    {title: '全屏切换', hint: '视图', run: toggleFullscreen},
    {title: '更新索引', hint: '索引', run: refreshIndex},
    {title: assistantPanel.hidden ? '打开助手' : '收起助手', hint: '面板', run: () => document.getElementById('assistantToggle').click()},
    {title: '打开整理', hint: '面板', run: () => openOrganize()},
    {title: '打开清理', hint: '面板', run: () => document.getElementById('cleanupBtn').click()},
  );
  for (const node of (graph ? graph.nodes : [])) {
    if (node.tagKind === 'category') {
      cmds.push({title: `聚焦分类：${node.label}`, hint: `分类 · ${fmt(node.count || 0)}`, run: () => enterCluster(node.id)});
    }
  }
  return cmds;
}
function renderPalette(query) {
  const q = String(query || '').trim().toLowerCase();
  const all = paletteCommands();
  paletteItems = (q ? all.filter(cmd => cmd.title.toLowerCase().includes(q) || cmd.hint.toLowerCase().includes(q)) : all).slice(0, 14);
  paletteList.innerHTML = paletteItems.map((cmd, i) =>
    `<div class="palette-item${i === 0 ? ' active' : ''}" data-i="${i}">` +
    `<span>${escapeHtml(cmd.title)}</span><em>${escapeHtml(cmd.hint)}</em></div>`
  ).join('') || '<div class="palette-empty">无匹配命令</div>';
  for (const el of paletteList.querySelectorAll('.palette-item')) {
    el.addEventListener('mousedown', event => { event.preventDefault(); runPalette(Number(el.dataset.i)); });
  }
}
function openPalette() {
  paletteWin.hidden = false;
  paletteInput.value = '';
  renderPalette('');
  setTimeout(() => paletteInput.focus(), 30);
}
function closePalette() { paletteWin.hidden = true; }
function runPalette(index) {
  const cmd = paletteItems[index];
  if (!cmd) return;
  closePalette();
  cmd.run();
}
paletteInput.addEventListener('input', () => renderPalette(paletteInput.value));
paletteInput.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    const active = paletteList.querySelector('.palette-item.active');
    runPalette(active ? Number(active.dataset.i) : 0);
  } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const items = [...paletteList.querySelectorAll('.palette-item')];
    if (!items.length) return;
    let index = items.findIndex(el => el.classList.contains('active'));
    index = event.key === 'ArrowDown' ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
    items.forEach(el => el.classList.remove('active'));
    items[index].classList.add('active');
    items[index].scrollIntoView({block: 'nearest'});
  }
});
paletteInput.addEventListener('blur', () => setTimeout(closePalette, 120));
document.getElementById('paletteBtn').addEventListener('click', openPalette);
document.addEventListener('keydown', event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'p') {
    event.preventDefault();
    paletteWin.hidden ? openPalette() : closePalette();
    return;
  }
  if (event.key !== 'Escape') return;
  if (!paletteWin.hidden) { closePalette(); return; }
  if (localViewId) { clearLocalView(); }
});
requestAnimationFrame(frame);
