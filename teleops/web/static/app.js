/* TeleOps 后台前端 —— 无框架单页应用 */
(() => {
'use strict';

// ─────────────────────────────────────────────────────────── 工具
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const TOKEN_KEY = 'teleops_token';
const token = () => localStorage.getItem(TOKEN_KEY) || '';

async function api(path, options = {}) {
  const opt = { headers: { 'X-Token': token() }, ...options };
  if (opt.body && !(opt.body instanceof FormData)) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(opt.body);
  }
  const res = await fetch(path, opt);
  let data = {};
  try { data = await res.json(); } catch (_) { /* 空响应 */ }
  if (!res.ok) {
    if (res.status === 401) { promptToken(); }
    throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  }
  return data.data !== undefined ? data : { data: null, ...data };
}
const GET = (p) => api(p).then((r) => r.data);
const POST = (p, body) => api(p, { method: 'POST', body });
const PATCH = (p, body) => api(p, { method: 'PATCH', body });
const PUT = (p, body) => api(p, { method: 'PUT', body });
const DEL = (p) => api(p, { method: 'DELETE' });

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = esc(msg);
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, kind === 'err' ? 5200 : 2800);
}
const okToast = (m) => toast(m, 'ok');
const errToast = (m) => toast(m, 'err');

function promptToast(fn) {
  return async (...args) => {
    try { await fn(...args); } catch (e) { errToast(e.message); }
  };
}

function promptToken() {
  const t = prompt('该后台已开启口令保护，请输入访问口令：', token());
  if (t !== null) { localStorage.setItem(TOKEN_KEY, t.trim()); location.reload(); }
}

const fmtTime = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  if (isNaN(d)) return '—';
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff >= 0 && diff < 60) return '刚刚';
  if (diff >= 0 && diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 0 && diff > -86400) return `${Math.ceil(-diff / 60)} 分钟后`;
  return d.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};
const fmtDuration = (s) => {
  if (!s) return '0 秒';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} 天 ${h} 小时` : h ? `${h} 小时 ${m} 分` : `${m || 1} 分钟`;
};
const STATUS_TAG = {
  ok: '<span class="tag ok">成功</span>', partial: '<span class="tag warn">部分成功</span>',
  error: '<span class="tag err">失败</span>', running: '<span class="tag info">运行中</span>',
  skipped: '<span class="tag">跳过</span>', never: '<span class="tag">未运行</span>',
};

// ─────────────────────────────────────────────────────────── 全局缓存
const store = { plugins: [], channels: [], accounts: [], workflows: [], settings: null };

async function refreshStore() {
  const [plugins, channels, accounts, settings] = await Promise.all([
    api('/api/plugins'), GET('/api/channels'), GET('/api/accounts'), GET('/api/settings'),
  ]);
  store.plugins = plugins.data;
  store.pluginStats = plugins.stats;
  store.pluginErrors = plugins.errors || [];
  store.channels = channels;
  store.accounts = accounts;
  store.settings = settings;
}

const tgApi = () => (store.settings && store.settings.telegram) || { configured: false, api_id: 0 };

// api 没配好时，账号相关的操作都做不了——给一条能点过去的提示
const apiWarning = () => tgApi().configured ? '' : `
  <div class="card" style="border-color:var(--warn);margin-bottom:14px">
    <strong>还没有配置 Telegram API</strong>
    <p class="faint" style="margin:6px 0 10px">
      所有账号共用一套 api_id / api_hash，需要先在设置里填好才能登录或导入账号。</p>
    <button class="primary sm" data-goto-settings>去设置</button>
  </div>`;

function bindApiWarning(root) {
  $$('[data-goto-settings]', root).forEach((b) => b.onclick = () => { location.hash = '#/settings'; });
}
const pluginsOf = (type) => store.plugins.filter((p) => p.type === type);
const pluginByName = (name) => store.plugins.find((p) => p.name === name);
const channelById = (id) => store.channels.find((c) => c.id === id);

// ─────────────────────────────────────────────────────────── 模态框
function modal({ title, body, footer, wide = false, onMount }) {
  const root = $('#modal-root');
  root.innerHTML = `
    <div class="mask">
      <div class="modal ${wide ? 'wide' : ''}">
        <div class="modal-head"><h2>${esc(title)}</h2><button class="ghost sm" data-close>✕</button></div>
        <div class="modal-body">${body}</div>
        <div class="modal-foot">${footer || '<button data-close>关闭</button>'}</div>
      </div>
    </div>`;
  const close = () => { root.innerHTML = ''; };
  $$('[data-close]', root).forEach((b) => b.onclick = close);
  $('.mask', root).onclick = (e) => { if (e.target === e.currentTarget) close(); };
  if (onMount) onMount(root, close);
  return close;
}
const closeModal = () => { $('#modal-root').innerHTML = ''; };

function confirmBox(text, onYes) {
  modal({
    title: '请确认', body: `<p>${esc(text)}</p>`,
    footer: '<button data-close>取消</button><button class="primary danger" data-yes>确定</button>',
    onMount: (root, close) => { $('[data-yes]', root).onclick = async () => { close(); await onYes(); }; },
  });
}

// ─────────────────────────────────────────────────────────── 动态表单
function fieldHtml(f, value) {
  const v = value === undefined || value === null ? (f.default ?? '') : value;
  const id = `f_${f.name}`;
  const label = `${esc(f.label)}${f.required ? ' <span class="req">*</span>' : ''}`;
  const help = f.help ? `<div class="help">${esc(f.help)}</div>` : '';
  const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : '';
  let input;
  switch (f.type) {
    case 'bool':
      return `<div class="field inline"><input type="checkbox" id="${id}" data-f="${f.name}" data-t="bool" ${v ? 'checked' : ''}>
              <label for="${id}">${label}</label></div>${help}`;
    case 'text':
      input = `<textarea id="${id}" data-f="${f.name}" data-t="text"${ph}>${esc(v)}</textarea>`; break;
    case 'int': case 'float':
      input = `<input type="number" id="${id}" data-f="${f.name}" data-t="${f.type}" step="${f.type === 'int' ? 1 : 'any'}" value="${esc(v)}"${ph}>`; break;
    case 'password':
      input = `<input type="password" id="${id}" data-f="${f.name}" data-t="string" value="${esc(v)}"${ph}>`; break;
    case 'select':
      input = `<select id="${id}" data-f="${f.name}" data-t="string">${(f.options || []).map((o) =>
        `<option value="${esc(o.value)}" ${String(o.value) === String(v) ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`; break;
    case 'multiselect': {
      const arr = Array.isArray(v) ? v.map(String) : [];
      input = `<select multiple id="${id}" data-f="${f.name}" data-t="multi">${(f.options || []).map((o) =>
        `<option value="${esc(o.value)}" ${arr.includes(String(o.value)) ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`; break;
    }
    case 'json':
      input = `<textarea id="${id}" data-f="${f.name}" data-t="json">${esc(typeof v === 'string' ? v : JSON.stringify(v ?? {}, null, 2))}</textarea>`; break;
    case 'channel':
      input = `<select id="${id}" data-f="${f.name}" data-t="string"><option value="">— 不指定 —</option>${store.channels.map((c) =>
        `<option value="${esc(c.peer)}" ${c.peer === v ? 'selected' : ''}>${esc(c.title)} (${esc(c.peer)})</option>`).join('')}</select>`; break;
    case 'account':
      input = `<select id="${id}" data-f="${f.name}" data-t="int"><option value="">— 用工作流账号 —</option>${store.accounts.map((a) =>
        `<option value="${a.id}" ${String(a.id) === String(v) ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select>`; break;
    default:
      input = `<input type="text" id="${id}" data-f="${f.name}" data-t="string" value="${esc(v)}"${ph}>`;
  }
  return `<div class="field"><label for="${id}">${label}</label>${input}${help}</div>`;
}

function schemaForm(schema, values = {}) {
  if (!schema || !schema.length) return '<p class="faint">该插件无需配置。</p>';
  const groups = new Map();
  schema.forEach((f) => {
    const g = f.group || '';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(f);
  });
  let html = '';
  for (const [g, fields] of groups) {
    const inner = `<div class="form-grid">${fields.map((f) => fieldHtml(f, values[f.name])).join('')}</div>`;
    html += g ? `<fieldset><legend>${esc(g)}</legend>${inner}</fieldset>` : inner;
  }
  return html;
}

function readForm(root) {
  const out = {};
  $$('[data-f]', root).forEach((el) => {
    const name = el.dataset.f, t = el.dataset.t;
    let v;
    if (t === 'bool') v = el.checked;
    else if (t === 'int') v = el.value === '' ? null : parseInt(el.value, 10);
    else if (t === 'float') v = el.value === '' ? null : parseFloat(el.value);
    else if (t === 'multi') v = [...el.selectedOptions].map((o) => o.value);
    else if (t === 'json') { try { v = el.value.trim() ? JSON.parse(el.value) : {}; } catch (_) { v = {}; } }
    else v = el.value;
    out[name] = v;
  });
  return out;
}

// ─────────────────────────────────────────────────────────── 页面：概览
async function pageOverview(view) {
  const d = await GET('/api/overview');
  const c = d.counters;
  view.innerHTML = `
    <header class="page">
      <div><h1>概览</h1><p>运行 ${fmtDuration(d.uptime)} · 插件 ${d.plugins.source + d.plugins.filter + d.plugins.formatter + d.plugins.sink} 个</p></div>
      <div class="spacer"></div>
      <button id="refresh">刷新</button>
    </header>
    <div class="grid c4">
      ${statCard('近 24 小时发布', c.sent_24h, `${c.runs_24h} 次运行 · ${c.failed_24h} 次失败`)}
      ${statCard('工作流', `${c.workflows_enabled}/${c.workflows}`, '已启用 / 总数')}
      ${statCard('频道', `${c.channels_enabled}/${c.channels}`, '已启用 / 总数')}
      ${statCard('账号', `${c.accounts_online}/${c.accounts}`, '在线 / 总数')}
    </div>

    <div class="grid c2 mt">
      <div class="card">
        <h3>工作流状态</h3>
        <div class="t-wrap"><table>
          <thead><tr><th>名称</th><th>状态</th><th>上次</th><th>下次</th><th class="right">累计发布</th></tr></thead>
          <tbody>${d.workflow_status.length ? d.workflow_status.map((w) => `
            <tr>
              <td><a data-nav="#/workflows">${esc(w.name)}</a> ${w.enabled ? '' : '<span class="tag">停用</span>'}</td>
              <td>${STATUS_TAG[w.last_status] || esc(w.last_status)}</td>
              <td class="faint nowrap">${fmtTime(w.last_run_at)}</td>
              <td class="faint nowrap">${w.enabled ? fmtTime(w.next_run_at) : '—'}</td>
              <td class="right">${w.total_sent}</td>
            </tr>`).join('') : '<tr><td colspan="5" class="faint">还没有工作流</td></tr>'}
          </tbody>
        </table></div>
      </div>
      <div class="card">
        <h3>最近运行</h3>
        <div class="t-wrap"><table>
          <thead><tr><th>工作流</th><th>结果</th><th>采集/发布</th><th>时间</th></tr></thead>
          <tbody>${d.recent_runs.length ? d.recent_runs.map((r) => `
            <tr title="${esc(r.error || '')}">
              <td>${esc(r.workflow)} <span class="tag">${r.trigger === 'manual' ? '手动' : r.trigger === 'test' ? '试运行' : '定时'}</span></td>
              <td>${STATUS_TAG[r.status] || esc(r.status)}</td>
              <td class="mono">${r.fetched} / ${r.sent}${r.failed ? ` <span class="tag err">${r.failed}</span>` : ''}</td>
              <td class="faint nowrap">${fmtTime(r.started_at)}</td>
            </tr>`).join('') : '<tr><td colspan="4" class="faint">暂无运行记录</td></tr>'}
          </tbody>
        </table></div>
      </div>
    </div>

    <div class="grid c2 mt">
      <div class="card">
        <h3>调度队列</h3>
        ${d.jobs.length ? `<div class="t-wrap"><table><tbody>${d.jobs.map((j) => `
          <tr><td>${esc(j.name)}</td><td class="faint mono">${esc(j.trigger)}</td><td class="right faint nowrap">${fmtTime(j.next_run)}</td></tr>`).join('')}
        </tbody></table></div>` : '<p class="faint">没有已排期的任务。启用工作流后会出现在这里。</p>'}
      </div>
      <div class="card">
        <h3>插件</h3>
        <div class="chain">
          <span class="node">信息源 ${d.plugins.source}</span><span class="arrow">→</span>
          <span class="node">过滤 ${d.plugins.filter}</span><span class="arrow">→</span>
          <span class="node">格式化 ${d.plugins.formatter}</span><span class="arrow">→</span>
          <span class="node">输出 ${d.plugins.sink}</span>
        </div>
        <p class="faint mt">去重记录 ${c.dedup_records} 条 · 投递记录 ${c.delivery_records || 0} 条
        · 已清缓存 ${c.cache_cleaned || 0} 个文件 · 历史累计发布 ${c.sent_total} 条
        ${d.plugins.error ? `<br><span class="tag err">${d.plugins.error} 个插件加载失败</span>` : ''}</p>
      </div>
    </div>`;
  $('#refresh').onclick = () => render();
  $$('[data-nav]', view).forEach((a) => a.onclick = () => location.hash = a.dataset.nav);
}

const statCard = (label, value, sub) =>
  `<div class="card stat"><div class="value">${esc(value)}</div><div class="label">${esc(label)}</div><div class="sub faint">${esc(sub || '')}</div></div>`;

// ─────────────────────────────────────────────────────────── 页面：频道
async function pageChannels(view) {
  const list = store.channels;
  view.innerHTML = `
    <header class="page">
      <div><h1>频道管理</h1><p>被运营的频道/群组。一个频道可以挂载多个工作流。</p></div>
      <div class="spacer"></div>
      <div class="btn-row">
        <button id="import">从账号导入</button>
        <button class="primary" id="add">+ 添加频道</button>
      </div>
    </header>
    <div class="card">
      <div class="t-wrap"><table>
        <thead><tr><th>标题</th><th>标识</th><th>账号</th><th>类型</th><th>挂载工作流</th><th>状态</th><th></th></tr></thead>
        <tbody>${list.length ? list.map((c) => `
          <tr>
            <td>${esc(c.title)}${c.note ? `<br><span class="faint">${esc(c.note)}</span>` : ''}</td>
            <td class="mono">${esc(c.peer)}</td>
            <td>${esc(accountName(c.account_id))}</td>
            <td><span class="tag">${c.kind === 'group' ? '群组' : '频道'}</span></td>
            <td>${c.workflow_count ? `<a data-wf="${c.id}">${c.workflow_count} 个</a>` : '<span class="faint">未挂载</span>'}</td>
            <td>${c.enabled ? '<span class="tag ok">启用</span>' : '<span class="tag">停用</span>'}
                ${c.resolved && c.resolved.id ? `<span class="tag info" title="成员 ${c.resolved.participants ?? '?'}">已校验</span>` : ''}</td>
            <td class="actions">
              <button class="sm" data-check="${c.id}">校验</button>
              <button class="sm" data-edit="${c.id}">编辑</button>
              <button class="sm danger" data-del="${c.id}">删除</button>
            </td>
          </tr>`).join('') : `<tr><td colspan="7"><div class="empty"><div class="big">📢</div>还没有频道。先在「账号」里登录，再从账号导入或手动添加。</div></td></tr>`}
        </tbody>
      </table></div>
    </div>`;

  $('#add').onclick = () => channelForm(null);
  $('#import').onclick = importDialog;
  $$('[data-edit]', view).forEach((b) => b.onclick = () => channelForm(channelById(+b.dataset.edit)));
  $$('[data-del]', view).forEach((b) => b.onclick = () => {
    const c = channelById(+b.dataset.del);
    confirmBox(`删除频道「${c.title}」？挂载在它上面的工作流目标会一并解除。`, promptToast(async () => {
      await DEL(`/api/channels/${c.id}`); okToast('已删除'); await refreshStore(); render();
    }));
  });
  $$('[data-check]', view).forEach((b) => b.onclick = promptToast(async () => {
    b.disabled = true;
    try {
      const r = await POST(`/api/channels/${b.dataset.check}/resolve`);
      okToast(`可达：${r.data.title || r.data.username || r.data.id}`);
      await refreshStore(); render();
    } finally { b.disabled = false; }
  }));
  $$('[data-wf]', view).forEach((a) => a.onclick = promptToast(async () => {
    const d = await GET(`/api/channels/${a.dataset.wf}`);
    modal({
      title: `「${d.title}」上的工作流`,
      body: d.workflows.length ? `<div class="t-wrap"><table>
        <thead><tr><th>工作流</th><th>信息源</th><th>状态</th><th>上次运行</th></tr></thead>
        <tbody>${d.workflows.map((w) => `<tr><td>${esc(w.name)}</td><td class="mono">${esc(w.source_plugin)}</td>
          <td>${w.enabled ? '<span class="tag ok">启用</span>' : '<span class="tag">停用</span>'}</td>
          <td class="faint">${fmtTime(w.last_run_at)}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="faint">暂无</p>',
    });
  }));
}

const accountName = (id) => (store.accounts.find((a) => a.id === id) || {}).name || '未绑定';

function channelForm(ch) {
  const c = ch || { title: '', peer: '', account_id: null, kind: 'channel', note: '', enabled: true };
  modal({
    title: ch ? '编辑频道' : '添加频道',
    body: `
      <div class="form-grid">
        <div class="field"><label>显示名称 <span class="req">*</span></label><input data-f="title" data-t="string" value="${esc(c.title)}"></div>
        <div class="field"><label>频道标识 <span class="req">*</span></label>
          <input data-f="peer" data-t="string" value="${esc(c.peer)}" placeholder="@channel 或 -1001234567890">
          <div class="help">支持 @用户名、数字 ID 或 t.me 链接</div></div>
        <div class="field"><label>使用账号</label><select data-f="account_id" data-t="int">
          <option value="">— 未绑定 —</option>
          ${store.accounts.map((a) => `<option value="${a.id}" ${a.id === c.account_id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
        </select></div>
        <div class="field"><label>类型</label><select data-f="kind" data-t="string">
          <option value="channel" ${c.kind === 'channel' ? 'selected' : ''}>频道</option>
          <option value="group" ${c.kind === 'group' ? 'selected' : ''}>群组</option>
        </select></div>
      </div>
      <div class="field"><label>备注</label><textarea data-f="note" data-t="string" style="min-height:56px">${esc(c.note)}</textarea></div>
      <div class="field inline"><input type="checkbox" data-f="enabled" data-t="bool" ${c.enabled ? 'checked' : ''}><label>启用</label></div>`,
    footer: '<button data-close>取消</button><button class="primary" data-save>保存</button>',
    onMount: (root, close) => {
      $('[data-save]', root).onclick = promptToast(async () => {
        const v = readForm(root);
        if (!v.title || !v.peer) return errToast('名称和标识都要填');
        if (ch) await PATCH(`/api/channels/${ch.id}`, v); else await POST('/api/channels', v);
        close(); okToast('已保存'); await refreshStore(); render();
      });
    },
  });
}

function importDialog() {
  if (!store.accounts.length) return errToast('请先添加并登录一个账号');
  modal({
    title: '从账号导入频道', wide: true,
    body: `<div class="field"><label>选择账号</label><select id="imp-acc">
        ${store.accounts.map((a) => `<option value="${a.id}">${esc(a.name)}${a.status === 'online' ? '' : '（未登录）'}</option>`).join('')}
      </select></div>
      <button class="primary" id="imp-load">读取对话列表</button>
      <div id="imp-list" class="mt"></div>`,
    footer: '<button data-close>关闭</button><button class="primary" id="imp-save" disabled>导入所选</button>',
    onMount: (root, close) => {
      $('#imp-load', root).onclick = promptToast(async () => {
        const id = $('#imp-acc', root).value;
        $('#imp-list', root).innerHTML = '<p class="faint">读取中…</p>';
        const rows = await GET(`/api/accounts/${id}/dialogs`);
        const exist = new Set(store.channels.map((c) => c.peer));
        $('#imp-list', root).innerHTML = `<div class="t-wrap"><table>
          <thead><tr><th style="width:32px"></th><th>名称</th><th>标识</th><th>类型</th><th>成员</th></tr></thead>
          <tbody>${rows.map((r, i) => `<tr>
            <td><input type="checkbox" data-peer="${i}" ${exist.has(r.peer) ? 'disabled' : ''} style="width:auto"></td>
            <td>${esc(r.title)} ${r.admin ? '<span class="tag info">管理员</span>' : ''}</td>
            <td class="mono">${esc(r.peer)}${exist.has(r.peer) ? ' <span class="tag">已存在</span>' : ''}</td>
            <td><span class="tag">${r.kind === 'group' ? '群组' : '频道'}</span></td>
            <td class="faint">${r.participants ?? '—'}</td></tr>`).join('')}</tbody></table></div>`;
        root._rows = rows;
        $('#imp-save', root).disabled = false;
      });
      $('#imp-save', root).onclick = promptToast(async () => {
        const picked = $$('[data-peer]:checked', root).map((cb) => root._rows[+cb.dataset.peer]);
        if (!picked.length) return errToast('没有勾选任何频道');
        const r = await POST('/api/channels/bulk', { account_id: +$('#imp-acc', root).value, peers: picked });
        close(); okToast(`已导入 ${r.data.created} 个频道`); await refreshStore(); render();
      });
    },
  });
}

// ─────────────────────────────────────────────────────────── 页面：工作流
async function pageWorkflows(view) {
  const list = await GET('/api/workflows');
  store.workflows = list;
  view.innerHTML = `
    <header class="page">
      <div><h1>工作流</h1><p>信息源 → 过滤 → 格式化 → 输出。每个工作流可同时发布到多个频道。</p></div>
      <div class="spacer"></div>
      <button class="primary" id="add">+ 新建工作流</button>
    </header>
    ${list.length ? `<div class="grid c2">${list.map(wfCard).join('')}</div>`
      : `<div class="card"><div class="empty"><div class="big">⚙️</div>还没有工作流。<br>新建一个，把 TG 频道搬运源挂到你的频道上试试。</div></div>`}`;

  $('#add').onclick = () => workflowEditor(null);
  $$('[data-edit]', view).forEach((b) => b.onclick = promptToast(async () =>
    workflowEditor(await GET(`/api/workflows/${b.dataset.edit}`))));
  $$('[data-toggle]', view).forEach((b) => b.onclick = promptToast(async () => {
    const r = await POST(`/api/workflows/${b.dataset.toggle}/toggle`);
    okToast(r.data.enabled ? '已启用，已加入调度' : '已停用'); render();
  }));
  $$('[data-run]', view).forEach((b) => b.onclick = () => runWorkflow(+b.dataset.run, false, b));
  $$('[data-dry]', view).forEach((b) => b.onclick = () => runWorkflow(+b.dataset.dry, true, b));
  $$('[data-runs]', view).forEach((b) => b.onclick = () => showRuns(+b.dataset.runs));
  $$('[data-deliv]', view).forEach((b) => b.onclick = () => showDeliveries(+b.dataset.deliv));
  $$('[data-del]', view).forEach((b) => b.onclick = () => {
    const wf = list.find((w) => w.id === +b.dataset.del);
    confirmBox(`删除工作流「${wf.name}」？运行记录和游标会一并清除。`, promptToast(async () => {
      await DEL(`/api/workflows/${wf.id}`); okToast('已删除'); render();
    }));
  });
  $$('[data-reset]', view).forEach((b) => b.onclick = () => {
    const id = +b.dataset.reset;
    confirmBox('重置该工作流的采集游标、去重记录和投递记录？重置后可能会重复发布已发过的内容。', promptToast(async () => {
      await POST(`/api/workflows/${id}/reset?mode=all`); okToast('已重置');
    }));
  });
}

function wfCard(w) {
  const chain = [
    pluginLabel(w.source_plugin),
    ...(w.filters || []).filter((s) => s.enabled !== false).map((s) => pluginLabel(s.plugin)),
    ...(w.formatters || []).filter((s) => s.enabled !== false).map((s) => pluginLabel(s.plugin)),
    pluginLabel(w.sink_plugin),
  ];
  const sched = w.schedule_type === 'manual' ? '仅手动触发'
    : w.schedule_type === 'cron' ? `cron ${w.schedule_value}`
    : `每 ${w.schedule_value}${/^\d+$/.test(String(w.schedule_value)) ? ' 秒' : ''} 循环`;
  const limit = w.daily_limit ? ` · 每天上限 ${w.daily_limit} 条` : '';
  return `<div class="card">
    <div style="display:flex;align-items:center;gap:9px;margin-bottom:8px">
      <strong style="font-size:15px">${esc(w.name)}</strong>
      ${w.enabled ? '<span class="tag ok">持续运行中</span>' : '<span class="tag">已停用</span>'}
      ${w.dry_run ? '<span class="tag warn">试运行模式</span>' : ''}
      <span class="spacer" style="flex:1"></span>
      ${STATUS_TAG[w.last_status] || ''}
    </div>
    ${w.description ? `<p class="faint" style="margin:0 0 9px">${esc(w.description)}</p>` : ''}
    <div class="chain">${chain.map((c, i) => `${i ? '<span class="arrow">→</span>' : ''}<span class="node">${esc(c)}</span>`).join('')}</div>
    <p class="faint mt" style="margin-bottom:10px;font-size:12px">
      📤 ${w.targets.length ? w.targets.map((t) => esc(t.title)).join('、') : '<span class="tag err">未选频道</span>'}
      <br>⏱ ${esc(sched)} · 每轮 ${w.max_items_per_run} 条${limit}
      <br>🕘 ${esc(w.window_label || '全天')} · ${esc(w.days_label || '每天')}
      <br>📈 已运行 ${w.total_runs || 0} 轮 · 累计发布 ${w.total_sent} 条
      <br>上次 ${fmtTime(w.last_run_at)}${w.enabled && w.schedule_type !== 'manual'
        ? ` · <strong>下次 ${fmtTime(w.next_run_at)}</strong>` : ''}
      ${w.last_note ? `<br><span class="tag warn">${esc(w.last_note)}</span>` : ''}
    </p>
    <div class="btn-row">
      <button class="sm primary" data-run="${w.id}">立即运行</button>
      <button class="sm" data-dry="${w.id}">试运行</button>
      <button class="sm" data-toggle="${w.id}">${w.enabled ? '停用' : '启用'}</button>
      <button class="sm" data-edit="${w.id}">编辑</button>
      <button class="sm" data-runs="${w.id}">记录</button>
      <button class="sm" data-deliv="${w.id}">投递</button>
      <button class="sm" data-reset="${w.id}">重置游标</button>
      <button class="sm danger" data-del="${w.id}">删除</button>
    </div>
  </div>`;
}

const pluginLabel = (name) => (pluginByName(name) || {}).display_name || name || '?';

async function runWorkflow(id, dry, btn) {
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = dry ? '试运行中…' : '运行中…';
  try {
    const r = await POST(`/api/workflows/${id}/run`, { dry_run: dry });
    const d = r.data;
    if (dry) showPreview(d);
    else if (d.status === 'error') errToast(`运行失败：${d.error}`);
    else okToast(`采集 ${d.fetched} 条，发布 ${d.sent} 条${d.failed ? `，失败 ${d.failed} 条` : ''}`);
    render();
  } catch (e) { errToast(e.message); }
  finally { btn.disabled = false; btn.textContent = old; }
}

function showPreview(d) {
  modal({
    title: '试运行结果（未真正发送）', wide: true,
    body: `<p>采集 <strong>${d.fetched}</strong> 条 · 过滤后保留 <strong>${d.kept}</strong> 条 ·
      模拟发布 <strong>${d.sent}</strong> 条${d.failed ? ` · 失败 <strong>${d.failed}</strong> 条` : ''}</p>
      ${d.error ? `<p class="tag err">${esc(d.error)}</p>` : ''}
      ${(d.items || []).length ? d.items.map((it) => `
        <div class="preview-item">
          <div class="meta">${esc(it.uid || '')} ${it.media.length ? `· ${it.media.length} 个媒体（${esc(it.media.map((m) => m.kind).join('、'))}）` : ''} ${it.url ? `· <a href="${esc(it.url)}" target="_blank">原文</a>` : ''}</div>
          ${esc(it.text || '(无正文)')}
        </div>`).join('') : '<p class="faint">没有产出内容。可能是没有新消息，或全部被过滤规则挡掉了。</p>'}
      ${(d.detail || []).filter((x) => x.error).length ? `<h3 class="mt">问题</h3>${d.detail.filter((x) => x.error).map((x) =>
        `<div class="preview-item"><span class="tag err">${esc(x.stage || x.target || '')}</span> ${esc(x.error)}</div>`).join('')}` : ''}`,
  });
}

async function showRuns(id) {
  const rows = await GET(`/api/workflows/${id}/runs`);
  modal({
    title: '运行记录', wide: true,
    body: rows.length ? `<div class="t-wrap"><table>
      <thead><tr><th>时间</th><th>触发</th><th>结果</th><th>采集</th><th>保留</th><th>发布</th><th>失败</th><th>清缓存</th><th>详情</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td class="nowrap faint">${fmtTime(r.started_at)}</td>
        <td><span class="tag">${r.trigger === 'manual' ? '手动' : r.trigger === 'test' ? '试运行' : '定时'}</span></td>
        <td>${STATUS_TAG[r.status] || esc(r.status)}</td>
        <td class="mono">${r.fetched}</td><td class="mono">${r.kept}</td>
        <td class="mono">${r.sent}</td><td class="mono">${r.failed || ''}</td>
        <td class="mono faint">${r.cleaned || ''}</td>
        <td class="faint" style="max-width:280px;overflow:hidden;text-overflow:ellipsis" title="${esc(r.error || JSON.stringify(r.detail))}">${esc(r.error || '')}</td>
      </tr>`).join('')}</tbody></table></div>` : '<p class="faint">暂无运行记录</p>',
  });
}

async function showDeliveries(id) {
  const d = await GET(`/api/workflows/${id}/deliveries?limit=200`);
  const rows = d.rows || [];
  const t = d.totals || {};
  const m = d.media || {};
  const quota = m.quota_mb ? `${m.quota_mb} MB` : (m.global_quota_mb ? `全局 ${m.global_quota_mb} MB` : '不限');
  modal({
    title: '投递记录', wide: true,
    body: `<p class="faint">记录每条内容发到过哪个目标。成功 <strong>${t.ok || 0}</strong> 条 ·
      失败 <strong>${t.failed || 0}</strong> 条。补发时已成功的目标会自动跳过，
      本地缓存清掉后这里会标记「已清」。<br>
      本地缓存占用 <strong>${esc(m.usage_label || '0 B')}</strong> / 上限 ${esc(quota)}</p>
      ${rows.length ? `<div class="t-wrap"><table>
      <thead><tr><th>时间</th><th>内容</th><th>目标</th><th>结果</th><th>媒体</th><th>缓存</th><th>消息</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td class="nowrap faint">${fmtTime(r.sent_at)}</td>
        <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis" title="${esc(r.uid || r.item_key)}">${esc(r.preview || r.uid || r.item_key)}</td>
        <td class="nowrap">${esc(r.target)}</td>
        <td>${r.status === 'ok' ? '<span class="tag ok">成功</span>'
            : `<span class="tag err" title="${esc(r.error)}">失败</span>`}${r.attempts > 1 ? ` <span class="faint">×${r.attempts}</span>` : ''}</td>
        <td class="mono faint">${r.media_count || ''}</td>
        <td class="faint">${r.cleaned ? `已清${r.cached_files ? ` ${r.cached_files} 个` : ''}` : (r.media_count ? '保留' : '—')}</td>
        <td class="mono faint">${r.message_id || ''}</td>
      </tr>`).join('')}</tbody></table></div>` : '<p class="faint">还没有投递记录。</p>'}`,
  });
}

// ─────────────────────────────────────── 工作流编辑器
function workflowEditor(wf) {
  const w = wf || {
    name: '', description: '', enabled: false, source_plugin: '', source_config: {},
    filters: [], formatters: [], sink_plugin: 'tg_send', sink_config: {},
    schedule_type: 'interval', schedule_value: '600', jitter: 0, account_id: null,
    max_items_per_run: 5, send_interval: 3, dedup: true, dry_run: false,
    media_cleanup: '', media_quota_mb: 0, targets: [],
  };
  const state = JSON.parse(JSON.stringify(w));
  if (!state.source_plugin && pluginsOf('source')[0]) state.source_plugin = pluginsOf('source')[0].name;

  modal({
    title: wf ? `编辑工作流：${wf.name}` : '新建工作流', wide: true,
    body: `<div id="wf-form"></div>`,
    footer: `<button data-close>取消</button>
             <button id="wf-test">测试信息源</button>
             <button class="primary" data-save>保存</button>`,
    onMount: (root, close) => {
      const host = $('#wf-form', root);
      const paint = () => {
        host.innerHTML = wfFormHtml(state);
        bind();
      };
      const collectStep = (kind, idx) => {
        const box = $(`[data-step="${kind}-${idx}"]`, host);
        return box ? readForm(box) : {};
      };
      const syncFromDom = () => {
        Object.assign(state, readForm($('#wf-base', host)), readForm($('#wf-sched', host)));
        state.source_config = readForm($('#wf-source-config', host));
        state.sink_config = readForm($('#wf-sink-config', host));
        state.filters.forEach((s, i) => { s.config = collectStep('filter', i); });
        state.formatters.forEach((s, i) => { s.config = collectStep('formatter', i); });
        state.targets = $$('#wf-targets input:checked', host).map((cb) => ({
          channel_id: +cb.value, enabled: true, overrides: {},
        }));
        state.active_days = $$('.wf-day:checked', host).map((cb) => +cb.value);
      };
      const bind = () => {
        $('#wf-source-plugin', host).onchange = (e) => {
          syncFromDom(); state.source_plugin = e.target.value; state.source_config = {}; paint();
        };
        $('#wf-sink-plugin', host).onchange = (e) => {
          syncFromDom(); state.sink_plugin = e.target.value; state.sink_config = {}; paint();
        };
        $$('[data-add-step]', host).forEach((sel) => sel.onchange = (e) => {
          if (!e.target.value) return;
          syncFromDom();
          const kind = sel.dataset.addStep;
          state[kind === 'filter' ? 'filters' : 'formatters'].push({ plugin: e.target.value, config: {}, enabled: true });
          paint();
        });
        $$('[data-rm-step]', host).forEach((b) => b.onclick = () => {
          const [kind, i] = b.dataset.rmStep.split('-');
          syncFromDom();
          state[kind === 'filter' ? 'filters' : 'formatters'].splice(+i, 1);
          paint();
        });
        $$('[data-move]', host).forEach((b) => b.onclick = () => {
          const [kind, i, dir] = b.dataset.move.split('-');
          syncFromDom();
          const arr = state[kind === 'filter' ? 'filters' : 'formatters'];
          const j = +i + (dir === 'up' ? -1 : 1);
          if (j < 0 || j >= arr.length) return;
          [arr[+i], arr[j]] = [arr[j], arr[+i]];
          paint();
        });
        $$('.step-head', host).forEach((h) => h.onclick = (e) => {
          if (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT') return;
          const body = h.nextElementSibling;
          if (body) body.style.display = body.style.display === 'none' ? '' : 'none';
        });
        $('#wf-sched-type', host).onchange = (e) => {
          $('#wf-sched-hint', host).textContent = schedHint(e.target.value);
        };
      };
      paint();

      $('#wf-test', root).onclick = promptToast(async () => {
        syncFromDom();
        const btn = $('#wf-test', root);
        btn.disabled = true; btn.textContent = '测试中…';
        try {
          const r = await POST('/api/plugins/test', {
            plugin: state.source_plugin, config: state.source_config, account_id: state.account_id,
          });
          const d = r.data;
          modal({
            title: '信息源测试结果', wide: true,
            body: `<p>取到 <strong>${d.count}</strong> 条内容${d.count ? '（最多展示 5 条）' : ''}</p>
              ${(d.items || []).map((it) => `<div class="preview-item">
                <div class="meta">${esc(it.uid || '')} ${it.media.length ? `· ${it.media.length} 个媒体` : ''}</div>
                ${esc((it.text || '').slice(0, 600) || '(无正文)')}</div>`).join('')
              || '<p class="faint">没有内容。增量模式首轮只记录游标不搬运，属正常现象。</p>'}`,
          });
        } finally { btn.disabled = false; btn.textContent = '测试信息源'; }
      });

      $('[data-save]', root).onclick = promptToast(async () => {
        syncFromDom();
        if (!state.name) return errToast('请填写工作流名称');
        if (!state.source_plugin) return errToast('请选择信息源');
        if (!state.targets.length) return errToast('请至少选择一个输出频道');
        const payload = { ...state };
        delete payload.id; delete payload.last_run_at; delete payload.last_status;
        delete payload.next_run_at; delete payload.total_sent; delete payload.running;
        if (wf) await PATCH(`/api/workflows/${wf.id}`, payload);
        else await POST('/api/workflows', payload);
        close(); okToast('已保存'); render();
      });
    },
  });
}

const schedHint = (t) => t === 'cron' ? '五段式 cron，例如 0 */2 * * *（每两小时整点）、30 9,18 * * *（每天 9:30 和 18:30）'
  : t === 'manual' ? '不自动运行，只能手动点「立即运行」'
  : '支持 600（秒）、10m、2h、1d；填区间则随机，例如 5m-15m 表示每隔 5～15 分钟随机跑一次';

function wfFormHtml(s) {
  const srcPlugin = pluginByName(s.source_plugin);
  const sinkPlugin = pluginByName(s.sink_plugin);
  return `
  <fieldset><legend>基本信息</legend>
    <div id="wf-base">
      <div class="form-grid">
        <div class="field"><label>名称 <span class="req">*</span></label><input data-f="name" data-t="string" value="${esc(s.name)}"></div>
        <div class="field"><label>使用账号</label><select data-f="account_id" data-t="int">
          <option value="">— 不指定 —</option>
          ${store.accounts.map((a) => `<option value="${a.id}" ${a.id === s.account_id ? 'selected' : ''}>${esc(a.name)}${a.status === 'online' ? '' : '（未登录）'}</option>`).join('')}
        </select><div class="help">采集和发送默认都用这个账号</div></div>
      </div>
      <div class="field"><label>说明</label><input data-f="description" data-t="string" value="${esc(s.description)}"></div>
      <div class="form-grid">
        <div class="field inline"><input type="checkbox" data-f="enabled" data-t="bool" ${s.enabled ? 'checked' : ''}><label>启用（加入定时调度）</label></div>
        <div class="field inline"><input type="checkbox" data-f="dedup" data-t="bool" ${s.dedup ? 'checked' : ''}><label>自动去重</label></div>
        <div class="field inline"><input type="checkbox" data-f="dry_run" data-t="bool" ${s.dry_run ? 'checked' : ''}><label>试运行模式（只记录不发送）</label></div>
      </div>
    </div>
  </fieldset>

  <fieldset><legend>① 信息源</legend>
    <div class="field"><label>采集插件 <span class="req">*</span></label>
      <select id="wf-source-plugin">${pluginsOf('source').map((p) =>
        `<option value="${p.name}" ${p.name === s.source_plugin ? 'selected' : ''}>${esc(p.display_name)}${p.enabled ? '' : '（已禁用）'}</option>`).join('')}</select>
      ${srcPlugin ? `<div class="help">${esc(srcPlugin.description)}</div>` : ''}</div>
    <div id="wf-source-config">${srcPlugin ? schemaForm(srcPlugin.config_schema, s.source_config) : '<p class="faint">没有可用的信息源插件</p>'}</div>
  </fieldset>

  ${chainFieldset('filter', '② 过滤规则', s.filters, '所有规则依次生效，任何一条不通过就丢弃该条内容。')}
  ${chainFieldset('formatter', '③ 格式化', s.formatters, '按顺序改写正文，例如先清洗再套模板。')}

  <fieldset><legend>④ 输出端</legend>
    <div class="field"><label>输出插件</label>
      <select id="wf-sink-plugin">${pluginsOf('sink').map((p) =>
        `<option value="${p.name}" ${p.name === s.sink_plugin ? 'selected' : ''}>${esc(p.display_name)}</option>`).join('')}</select></div>
    <div id="wf-sink-config">${sinkPlugin ? schemaForm(sinkPlugin.config_schema, s.sink_config) : ''}</div>
    <div class="field"><label>目标频道 <span class="req">*</span>（可多选）</label>
      <div id="wf-targets" style="max-height:190px;overflow-y:auto;border:1px solid var(--border);border-radius:7px;padding:8px">
        ${store.channels.length ? store.channels.map((c) => `
          <label class="field inline" style="margin-bottom:4px">
            <input type="checkbox" value="${c.id}" ${s.targets.some((t) => t.channel_id === c.id) ? 'checked' : ''}>
            <span>${esc(c.title)} <span class="mono faint">${esc(c.peer)}</span>${c.enabled ? '' : ' <span class="tag">停用</span>'}</span>
          </label>`).join('') : '<p class="faint">还没有频道，请先到「频道管理」添加</p>'}
      </div></div>
  </fieldset>

  <fieldset><legend>⑤ 什么时候搬运</legend>
    <p class="faint" style="margin-top:0;font-size:12px">
      启用后工作流会一直按这里的设置<strong>反复运行</strong>，直到你手动停用。</p>
    <div id="wf-sched">
      <div class="form-grid">
        <div class="field"><label>运行频率</label><select id="wf-sched-type" data-f="schedule_type" data-t="string">
          <option value="interval" ${s.schedule_type === 'interval' ? 'selected' : ''}>每隔一段时间</option>
          <option value="cron" ${s.schedule_type === 'cron' ? 'selected' : ''}>Cron 表达式（精确到点）</option>
          <option value="manual" ${s.schedule_type === 'manual' ? 'selected' : ''}>仅手动触发</option>
        </select></div>
        <div class="field"><label>间隔 / 表达式</label><input data-f="schedule_value" data-t="string" value="${esc(s.schedule_value)}">
          <div class="help" id="wf-sched-hint">${esc(schedHint(s.schedule_type))}</div></div>
        <div class="field"><label>额外随机抖动（秒）</label><input type="number" data-f="jitter" data-t="int" value="${s.jitter}">
          <div class="help">让触发时刻不那么规律，降低机器人特征</div></div>
      </div>
      <div class="form-grid">
        <div class="field"><label>每天允许搬运的时段</label>
          <input data-f="active_hours" data-t="string" value="${esc(s.active_hours || '')}" placeholder="09:00-23:00">
          <div class="help">留空＝全天。时段外的那几轮会自动跳过，不发内容。<br>
            支持跨夜，例如 <code>22:00-06:00</code></div></div>
        <div class="field"><label>每天最多发布</label>
          <input type="number" data-f="daily_limit" data-t="int" value="${s.daily_limit || 0}">
          <div class="help">0＝不限。发够了当天就不再发，次日零点自动重置</div></div>
        <div class="field"><label>时区</label>
          <input data-f="timezone" data-t="string" value="${esc(s.timezone || '')}" placeholder="Asia/Shanghai">
          <div class="help">留空＝服务器本地时区。影响上面的时段和每日重置</div></div>
      </div>
      <div class="field"><label>只在这几天运行</label>
        <div style="display:flex;gap:14px;flex-wrap:wrap;padding:6px 0">
          ${[1, 2, 3, 4, 5, 6, 7].map((d, i) => `
            <label style="display:flex;align-items:center;gap:5px;cursor:pointer">
              <input type="checkbox" class="wf-day" value="${d}" style="width:auto"
                ${(s.active_days || []).map(Number).includes(d) ? 'checked' : ''}>
              <span>周${'一二三四五六日'[i]}</span></label>`).join('')}
        </div>
        <div class="help">一个都不勾＝每天都跑</div></div>
      <div class="form-grid">
        <div class="field"><label>每轮最多发布</label><input type="number" data-f="max_items_per_run" data-t="int" value="${s.max_items_per_run}">
          <div class="help">控制单轮节奏，避免一次刷屏</div></div>
        <div class="field"><label>发送间隔（秒）</label><input type="number" step="0.5" data-f="send_interval" data-t="float" value="${s.send_interval}">
          <div class="help">同一轮内两条之间的停顿，防限流</div></div>
        <div class="field"><label>本地缓存清理</label>
          <select data-f="media_cleanup" data-t="string">
            ${[['', '跟随全局设置'], ['on_success', '发送成功后立即删除'], ['always', '发完就删（不管成败）'],
               ['never', '保留（按全局天数清理）']].map(([v, label]) =>
              `<option value="${v}" ${(s.media_cleanup || '') === v ? 'selected' : ''}>${label}</option>`).join('')}
          </select>
          <div class="help">下载模式的图片/视频会先落盘。发成功后立刻删掉，磁盘不会越用越满；<br>
            发到哪个目标成功过都记在数据库里，补发时不会重复发</div></div>
        <div class="field"><label>本地缓存上限（MB）</label>
          <input type="number" min="0" data-f="media_quota_mb" data-t="int" value="${s.media_quota_mb || 0}">
          <div class="help">0＝不限。只管本工作流的缓存目录，超了先删最旧的文件；<br>
            本轮正要发的素材不会被删，真腾不出空间时这条会自动改走复制模式</div></div>
      </div>
    </div>
  </fieldset>`;
}

function chainFieldset(kind, title, steps, hint) {
  const pool = pluginsOf(kind);
  return `<fieldset><legend>${title}</legend>
    <p class="faint" style="margin-top:0;font-size:12px">${esc(hint)}</p>
    <div class="steps">${(steps || []).map((st, i) => {
      const p = pluginByName(st.plugin);
      return `<div class="step" data-step="${kind}-${i}">
        <div class="step-head">
          <span class="tag info">${i + 1}</span>
          <span class="name">${esc(p ? p.display_name : st.plugin)}</span>
          ${p ? '' : '<span class="tag err">插件缺失</span>'}
          <span class="spacer"></span>
          <button type="button" class="sm ghost" data-move="${kind}-${i}-up">↑</button>
          <button type="button" class="sm ghost" data-move="${kind}-${i}-down">↓</button>
          <button type="button" class="sm danger" data-rm-step="${kind}-${i}">移除</button>
        </div>
        <div class="step-body">${p ? schemaForm(p.config_schema, st.config || {}) : '<p class="faint">该插件已不存在，请移除。</p>'}</div>
      </div>`;
    }).join('')}</div>
    <div class="field mt"><select data-add-step="${kind}">
      <option value="">+ 添加${kind === 'filter' ? '过滤规则' : '格式化步骤'}…</option>
      ${pool.map((p) => `<option value="${p.name}">${esc(p.display_name)}</option>`).join('')}
    </select></div>
  </fieldset>`;
}

// ─────────────────────────────────────────────────────────── 页面：插件
async function pagePlugins(view) {
  const res = await api('/api/plugins');
  const list = res.data;
  store.plugins = list;
  const byType = { source: [], filter: [], formatter: [], sink: [] };
  list.forEach((p) => (byType[p.type] || []).push(p));
  const TITLES = { source: '信息源', filter: '过滤规则', formatter: '格式化', sink: '输出端' };

  view.innerHTML = `
    <header class="page">
      <div><h1>插件管理</h1><p>把 .py 文件放进 <code>${esc((res.dirs || [])[0] || 'plugins')}</code> 即可热插拔，无需重启。</p></div>
      <div class="spacer"></div>
      <div class="btn-row">
        <button id="upload">上传插件</button>
        <button id="reload">全部重载</button>
      </div>
    </header>
    ${(res.errors || []).length ? `<div class="card" style="border-color:var(--err);margin-bottom:14px">
      <h3 style="color:var(--err)">加载失败的插件</h3>
      ${res.errors.map((e) => `<div class="mono" style="margin-bottom:6px">${esc(e.file)}<br><span class="faint">${esc(e.message)}</span></div>`).join('')}
    </div>` : ''}
    ${Object.keys(byType).map((t) => `
      <div class="card mt">
        <h3>${TITLES[t]} <span class="faint">(${byType[t].length})</span></h3>
        ${byType[t].length ? `<div class="t-wrap"><table>
          <thead><tr><th>名称</th><th>标识</th><th>版本</th><th>说明</th><th>被使用</th><th>状态</th><th></th></tr></thead>
          <tbody>${byType[t].map((p) => `<tr>
            <td>${esc(p.display_name)}${p.requires_account ? ' <span class="tag" title="需要 Telegram 账号">TG</span>' : ''}</td>
            <td class="mono faint">${esc(p.name)}</td>
            <td class="faint">${esc(p.version)}</td>
            <td class="faint" style="max-width:340px">${esc((p.description || '').split('\n')[0])}</td>
            <td>${p.used_by ? `<span class="tag info">${p.used_by} 个工作流</span>` : '<span class="faint">—</span>'}</td>
            <td>${p.enabled ? '<span class="tag ok">启用</span>' : '<span class="tag">禁用</span>'}</td>
            <td class="actions">
              <button class="sm" data-view="${p.name}">源码</button>
              <button class="sm" data-toggle="${p.name}" data-en="${p.enabled}">${p.enabled ? '禁用' : '启用'}</button>
              <button class="sm danger" data-del="${p.name}">删除</button>
            </td></tr>`).join('')}</tbody></table></div>` : '<p class="faint">暂无</p>'}
      </div>`).join('')}`;

  $('#reload').onclick = promptToast(async () => {
    const r = await POST('/api/plugins/reload');
    const d = r.data;
    okToast(`新增 ${d.added.length}，更新 ${d.updated.length}，移除 ${d.removed.length}`);
    if (d.errors.length) errToast(d.errors[0].message);
    await refreshStore(); render();
  });
  $('#upload').onclick = uploadDialog;
  $$('[data-toggle]', view).forEach((b) => b.onclick = promptToast(async () => {
    await POST(`/api/plugins/${b.dataset.toggle}/toggle`, { enabled: b.dataset.en !== 'true' });
    await refreshStore(); render();
  }));
  $$('[data-view]', view).forEach((b) => b.onclick = promptToast(() => sourceEditor(b.dataset.view)));
  $$('[data-del]', view).forEach((b) => b.onclick = () =>
    confirmBox(`删除插件 ${b.dataset.del} 的源文件？此操作不可恢复。`, promptToast(async () => {
      await DEL(`/api/plugins/${b.dataset.del}`); okToast('已删除'); await refreshStore(); render();
    })));
}

async function sourceEditor(name) {
  const d = await GET(`/api/plugins/${name}/source`);
  modal({
    title: `插件源码：${d.file}`, wide: true,
    body: `<textarea id="src" style="min-height:56vh;font-size:12.5px">${esc(d.code)}</textarea>
           <p class="faint">保存后会立即热重载；若有语法错误会自动回滚。</p>`,
    footer: '<button data-close>关闭</button><button class="primary" data-save>保存并重载</button>',
    onMount: (root, close) => {
      $('[data-save]', root).onclick = promptToast(async () => {
        await PUT(`/api/plugins/${name}/source`, { code: $('#src', root).value });
        close(); okToast('已保存并重载'); await refreshStore(); render();
      });
    },
  });
}

function uploadDialog() {
  modal({
    title: '上传插件',
    body: `<div class="field"><label>插件类型（决定放到哪个子目录）</label><select id="up-type">
        <option value="source">信息源 sources/</option>
        <option value="filter">过滤规则 filters/</option>
        <option value="formatter">格式化 formatters/</option>
        <option value="sink">输出端 sinks/</option>
      </select></div>
      <div class="field"><label>选择 .py 文件</label><input type="file" id="up-file" accept=".py"></div>
      <p class="faint">上传后会立即校验并加载；有语法错误会自动删除并提示。</p>`,
    footer: '<button data-close>取消</button><button class="primary" data-save>上传</button>',
    onMount: (root, close) => {
      $('[data-save]', root).onclick = promptToast(async () => {
        const f = $('#up-file', root).files[0];
        if (!f) return errToast('请选择文件');
        const fd = new FormData();
        fd.append('file', f);
        await api(`/api/plugins/upload?type=${$('#up-type', root).value}`, { method: 'POST', body: fd });
        close(); okToast('已上传并加载'); await refreshStore(); render();
      });
    },
  });
}

// ─────────────────────────────────────────────────────────── 页面：账号
async function pageAccounts(view) {
  const list = store.accounts;
  view.innerHTML = `
    <header class="page">
      <div><h1>账号</h1><p>用于采集和发送的 Telegram 账号。api_id / api_hash 在 <a href="https://my.telegram.org" target="_blank">my.telegram.org</a> 申请。</p></div>
      <div class="spacer"></div>
      <div class="btn-row">
        <button id="import">导入 session</button>
        <button class="primary" id="add">+ 添加账号</button>
      </div>
    </header>
    ${apiWarning()}
    <div class="card">
      <div class="t-wrap"><table>
        <thead><tr><th>名称</th><th>类型</th><th>身份</th><th>状态</th><th>代理</th><th></th></tr></thead>
        <tbody>${list.length ? list.map((a) => `<tr>
          <td>${esc(a.name)}<br><span class="faint mono">${esc(a.phone || '')}</span></td>
          <td><span class="tag">${a.is_bot ? 'Bot' : '用户'}</span></td>
          <td class="faint">${a.me && a.me.id ? esc(`${a.me.first_name || ''} @${a.me.username || ''}`) : '—'}</td>
          <td>${statusTag(a)}</td>
          <td class="faint mono">${esc(a.proxy || '—')}</td>
          <td class="actions">
            ${a.status === 'online' && !a.is_bot
              ? `<button class="sm primary" data-code="${a.id}">获取验证码${
                  a.code_watch && a.code_watch.active ? ' ⏳' : ''}</button>` : ''}
            ${a.status === 'online' ? `<button class="sm" data-logout="${a.id}">退出登录</button>`
              : `<button class="sm primary" data-login="${a.id}">登录</button>`}
            <button class="sm" data-edit="${a.id}">编辑</button>
            <button class="sm danger" data-del="${a.id}">删除</button>
          </td></tr>`).join('')
          : `<tr><td colspan="6"><div class="empty"><div class="big">👤</div>还没有账号。<br>可以「+ 添加账号」走验证码登录，也可以直接「导入 session」。</div></td></tr>`}
        </tbody>
      </table></div>
    </div>`;

  bindApiWarning(view);
  $('#add').onclick = () => accountForm(null);
  $('#import').onclick = () => accountForm(null, { tab: 'import' });
  $$('[data-edit]', view).forEach((b) => b.onclick = () => accountForm(list.find((a) => a.id === +b.dataset.edit)));
  $$('[data-login]', view).forEach((b) => b.onclick = () => loginFlow(+b.dataset.login));
  $$('[data-code]', view).forEach((b) => b.onclick = () =>
    codeWatchDialog(list.find((a) => a.id === +b.dataset.code)));
  $$('[data-logout]', view).forEach((b) => b.onclick = () =>
    confirmBox('退出登录会删除本地会话文件，下次需要重新验证。继续？', promptToast(async () => {
      await POST(`/api/accounts/${b.dataset.logout}/logout`); okToast('已退出'); await refreshStore(); render();
    })));
  $$('[data-del]', view).forEach((b) => b.onclick = () =>
    confirmBox('删除该账号？', promptToast(async () => {
      await DEL(`/api/accounts/${b.dataset.del}`); okToast('已删除'); await refreshStore(); render();
    })));
}

function statusTag(a) {
  if (a.status === 'online') return `<span class="tag ok">在线</span>${a.connected ? '' : ' <span class="tag">未连接</span>'}`;
  if (a.status && a.status.startsWith('pending')) return '<span class="tag warn">等待验证</span>';
  if (a.status === 'error') return '<span class="tag err">异常</span>';
  return '<span class="tag">未登录</span>';
}

// 手动填写 / 验证码登录 的表单片段
function manualAccountBody(a, acc) {
  return `<div class="field faint" style="font-size:12px;border:1px solid var(--border);border-radius:7px;padding:8px 11px">
        🔑 使用「设置」里的全局 api_id
        <strong class="mono">${tgApi().configured ? esc(tgApi().api_id) : '（未配置）'}</strong>
        ，所有账号共用一套，这里不用再填。
      </div>
      <div class="form-grid">
        <div class="field"><label>名称 <span class="req">*</span></label><input data-f="name" data-t="string" value="${esc(a.name)}" placeholder="主号"></div>
        <div class="field"><label>手机号</label><input data-f="phone" data-t="string" value="${esc(a.phone)}" placeholder="+8613800138000"></div>
        <div class="field"><label>Bot Token</label><input data-f="bot_token" data-t="string" placeholder="仅 Bot 账号需要"></div>
        <div class="field"><label>代理</label><input data-f="proxy" data-t="string" value="${esc(a.proxy)}" placeholder="socks5://127.0.0.1:1080"></div>
      </div>
      <div class="form-grid">
        <div class="field inline"><input type="checkbox" data-f="is_bot" data-t="bool" ${a.is_bot ? 'checked' : ''}><label>这是一个 Bot</label></div>
        <div class="field inline"><input type="checkbox" data-f="enabled" data-t="bool" ${a.enabled ? 'checked' : ''}><label>启用</label></div>
      </div>
      <p class="faint">保存后回到列表点「登录」，Telegram 会发来验证码。</p>`;
}

// 导入已有 session 的表单片段
function importAccountBody() {
  return `
    <div class="field"><label>来源</label>
      <select id="imp-kind">
        <option value="file">.session 文件</option>
        <option value="string">Session String（粘贴文本）</option>
      </select>
    </div>

    <div id="imp-file-box">
      <div class="field"><label>选择 .session 文件 <span class="req">*</span></label>
        <input type="file" id="imp-file" accept=".session,application/octet-stream">
        <div class="help" id="imp-file-hint">Telethon 生成的 .session（SQLite 格式）。Pyrogram 的不通用。</div></div>
      <div class="field"><label>附带的 .json（可选）</label>
        <input type="file" id="imp-meta" accept=".json,application/json">
        <div class="help">很多账号包会附一个同名 json，里面有 app_id / app_hash，选上就自动填。</div></div>
    </div>

    <div id="imp-string-box" style="display:none">
      <div class="field"><label>Session String <span class="req">*</span></label>
        <textarea id="imp-string" placeholder="1BQAN..." style="min-height:88px"></textarea>
        <div class="help">Telethon 的 StringSession，以 1 开头。</div></div>
    </div>

    <div class="form-grid">
      <div class="field"><label>账号名称 <span class="req">*</span></label>
        <input id="imp-name" placeholder="备用号 1"><div class="help">只是给你自己看的标识</div></div>
      <div class="field"><label>代理</label>
        <input id="imp-proxy" placeholder="socks5://127.0.0.1:1080"></div>
    </div>

    <p class="faint" style="font-size:12px">
      🔑 使用「设置」里的全局 api_id <strong class="mono">${tgApi().configured ? esc(tgApi().api_id) : '（未配置）'}</strong>。
      <strong>这份 session 必须是用同一个 api_id 生成的</strong>，否则 Telegram 会判定密钥无效。<br>
      点下面的按钮会真的连一次 Telegram 验证登录状态，失败不留任何记录。
    </p>
    <div id="imp-result"></div>`;
}

function bindImportForm(root) {
  const kind = $('#imp-kind', root);
  const sync = () => {
    const isFile = kind.value === 'file';
    $('#imp-file-box', root).style.display = isFile ? '' : 'none';
    $('#imp-string-box', root).style.display = isFile ? 'none' : '';
  };
  kind.onchange = sync;
  sync();

  // 选完文件立刻本地体检一次，不用等提交才知道格式不对
  $('#imp-file', root).onchange = async (e) => {
    const f = e.target.files[0];
    const hint = $('#imp-file-hint', root);
    if (!f) return;
    hint.textContent = '正在识别…';
    try {
      const fd = new FormData();
      fd.append('file', f);
      const r = await api('/api/accounts/inspect-session', { method: 'POST', body: fd });
      hint.innerHTML = r.data.usable
        ? `<span class="tag ok">${esc(r.data.detail)}</span>`
        : `<span class="tag err">${esc(r.data.detail)}</span>`;
    } catch (err) {
      hint.innerHTML = `<span class="tag err">${esc(err.message)}</span>`;
    }
  };

  // 选了 json 就把 api_id / api_hash 填进去，省得手抄
  $('#imp-meta', root).onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const d = JSON.parse(await f.text());
      const id = d.app_id ?? d.api_id ?? d.appId ?? d.apiId;
      if (d.phone && !$('#imp-name', root).value) $('#imp-name', root).value = String(d.phone);
      if (id && tgApi().configured && Number(id) !== Number(tgApi().api_id)) {
        errToast(`这份 session 用的是 api_id ${id}，和设置里的 ${tgApi().api_id} 不一致，导入会失败`);
      } else if (id) {
        okToast('json 里的 api_id 与全局设置一致 ✓');
      }
    } catch (_) {
      errToast('这个 json 读不出来，请手动填 api_id / api_hash');
    }
  };
}

async function submitImport(root, close, btn) {
  const kind = $('#imp-kind', root);
  const name = $('#imp-name', root).value.trim();
  if (!name) return errToast('请填写账号名称');
  if (!tgApi().configured) return errToast('请先到「设置」里配置 api_id / api_hash');

  const fd = new FormData();
  fd.append('name', name);
  fd.append('proxy', $('#imp-proxy', root).value.trim());

  if (kind.value === 'file') {
    const f = $('#imp-file', root).files[0];
    if (!f) return errToast('请选择 .session 文件');
    fd.append('file', f);
    const m = $('#imp-meta', root).files[0];
    if (m) fd.append('meta', m);
  } else {
    const str = $('#imp-string', root).value.trim();
    if (!str) return errToast('请粘贴 session string');
    fd.append('session_string', str);
  }

  const label = btn.textContent;
  btn.disabled = true; btn.textContent = '正在连接 Telegram…';
  $('#imp-result', root).innerHTML = '';
  try {
    const r = await api('/api/accounts/import', { method: 'POST', body: fd });
    const me = r.data.me || {};
    close();
    okToast(`已导入：${me.first_name || ''} ${me.username ? '@' + me.username : ''} ${me.phone ? '(' + me.phone + ')' : ''}`.trim());
    await refreshStore(); render();
  } catch (e) {
    $('#imp-result', root).innerHTML =
      `<div class="preview-item"><span class="tag err">导入失败</span><br>${esc(e.message)}</div>`;
    throw e;
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

// 新建账号：手动填写 / 导入 session 两个页签
function accountForm(acc, opts) {
  const a = acc || { name: '', api_id: '', api_hash: '', phone: '', is_bot: false, bot_token: '', proxy: '', enabled: true };

  // 编辑已有账号时没有页签，就是一张表
  if (acc) {
    modal({
      title: '编辑账号',
      body: manualAccountBody(a, acc),
      footer: '<button data-close>取消</button><button class="primary" data-save>保存</button>',
      onMount: (root, close) => {
        $('[data-save]', root).onclick = promptToast(async () => {
          const v = readForm(root);
          if (!v.api_hash) delete v.api_hash;
          if (!v.bot_token) delete v.bot_token;
          await PATCH(`/api/accounts/${acc.id}`, v);
          close(); okToast('已保存'); await refreshStore(); render();
        });
      },
    });
    return;
  }

  let tab = (opts && opts.tab) || 'manual';
  modal({
    title: '添加账号', wide: true,
    body: `
      <div class="btn-row" id="acc-tabs" style="margin-bottom:14px">
        <button data-tab="manual">① 手机号 + 验证码登录</button>
        <button data-tab="import">② 导入已登录的 session</button>
      </div>
      <div id="acc-manual">${manualAccountBody(a, null)}</div>
      <div id="acc-import">${importAccountBody()}</div>`,
    footer: '<button data-close>取消</button><button class="primary" data-save></button>',
    onMount: (root, close) => {
      const save = $('[data-save]', root);
      const paint = () => {
        $$('#acc-tabs button', root).forEach((b) =>
          b.className = b.dataset.tab === tab ? 'primary' : '');
        $('#acc-manual', root).style.display = tab === 'manual' ? '' : 'none';
        $('#acc-import', root).style.display = tab === 'import' ? '' : 'none';
        save.textContent = tab === 'manual' ? '保存' : '验证并导入';
      };
      $$('#acc-tabs button', root).forEach((b) => b.onclick = () => { tab = b.dataset.tab; paint(); });
      bindImportForm(root);
      paint();

      save.onclick = promptToast(async () => {
        if (tab === 'import') return submitImport(root, close, save);
        const v = readForm($('#acc-manual', root));
        if (!v.name) return errToast('请填写账号名称');
        if (!tgApi().configured) return errToast('请先到「设置」里配置 api_id / api_hash');
        await POST('/api/accounts', v);
        close(); okToast('已保存，回到列表点「登录」收验证码'); await refreshStore(); render();
      });
    },
  });
}

// 监听该账号收到的登录验证码（在别处登录这个号时用）
function codeWatchDialog(acc) {
  if (!acc) return;
  let timer = null;
  let stopped = false;

  const paint = (d) => {
    const box = $('#cw-body');
    if (!box) return;
    const codes = d.codes || [];
    box.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        ${d.active
          ? `<span class="tag ok">监听中</span><span class="faint">剩余 ${d.remaining} 秒</span>`
          : `<span class="tag">${codes.length ? '监听已结束' : '监听已结束，没等到验证码'}</span>`}
      </div>
      ${codes.length ? codes.map((c, i) => `
        <div class="preview-item" style="display:flex;align-items:center;gap:12px">
          <span style="font-size:26px;font-weight:600;letter-spacing:3px;font-family:var(--mono)">${esc(c.code)}</span>
          <button class="sm" data-copy="${esc(c.code)}">复制</button>
          <span class="spacer" style="flex:1"></span>
          <span class="faint" style="font-size:11px;text-align:right">
            ${i === 0 ? '<span class="tag ok">最新</span><br>' : ''}
            ${esc(c.source === 'history' ? '开监听前已收到' : '刚刚收到')} · ${fmtTime(c.at)}
          </span>
        </div>`).join('')
        : `<div class="empty" style="padding:26px">
             <div class="big">${d.active ? '📡' : '🕐'}</div>
             ${d.active
               ? '正在等待验证码…<br><span class="faint">现在去另一台设备/程序上登录这个号，码到了会直接显示在这里</span>'
               : '这段时间内没有收到验证码。<br><span class="faint">确认一下是否真的触发了登录，或者再监听一次。</span>'}
           </div>`}
      ${d.error ? `<div class="preview-item"><span class="tag err">读取失败</span><br>${esc(d.error)}</div>` : ''}
      ${(d.messages || []).length ? `
        <details ${codes.length ? '' : 'open'} style="margin-top:10px">
          <summary class="faint" style="cursor:pointer;font-size:12px">
            服务号最近 ${d.messages.length} 条消息${codes.length ? '' : '（没抽出验证码时可以自己看）'}
          </summary>
          ${d.messages.map((m) => `
            <div class="preview-item" style="font-size:12px">
              <div class="meta">${fmtTime(m.at)} · ${esc(m.source === 'history' ? '历史' : '实时')}</div>
              ${esc(m.text || '(空消息)')}
            </div>`).join('')}
        </details>` : ''}`;

    $$('[data-copy]', box).forEach((b) => b.onclick = async () => {
      try {
        await navigator.clipboard.writeText(b.dataset.copy);
        okToast('已复制 ' + b.dataset.copy);
      } catch (_) {
        errToast('复制失败，请手动选中');
      }
    });

    const restart = $('#cw-restart');
    if (restart) restart.style.display = d.active ? 'none' : '';
  };

  const poll = async () => {
    if (stopped) return;
    try {
      paint(await GET(`/api/accounts/${acc.id}/code-watch`));
    } catch (_) { /* 弹窗可能已经关了 */ }
  };

  const begin = async () => {
    paint({ active: true, remaining: 0, codes: [] });
    try {
      paint(await POST(`/api/accounts/${acc.id}/code-watch`, { seconds: 180 })
        .then((r) => r.data));
    } catch (e) {
      errToast(e.message);
      paint({ active: false, codes: [], error: e.message });
      return;
    }
    clearInterval(timer);
    timer = setInterval(poll, 2000);
  };

  modal({
    title: `获取登录验证码 — ${acc.name}`,
    body: `
      <p class="faint" style="margin-top:0">
        这个号已经登录在 TeleOps 里，所以它收到的登录验证码可以直接读出来。<br>
        <strong>先点开始监听，再去另一台设备/程序上登录这个号。</strong>
      </p>
      <div id="cw-body"></div>`,
    footer: `<button id="cw-restart" style="display:none">再监听 3 分钟</button>
             <button data-close>关闭</button>`,
    onMount: (root, close) => {
      $('#cw-restart', root).onclick = begin;

      // 标题栏的 ✕ 和页脚的「关闭」都要接管——两个都带 data-close，
      // 只改第一个的话，从另一个关闭就会把监听留在后台跑。
      const teardown = async (origClose) => {
        stopped = true;
        clearInterval(timer);
        try { await DEL(`/api/accounts/${acc.id}/code-watch`); } catch (_) { /* 无所谓 */ }
        if (origClose) origClose();
        await refreshStore();
        if ((location.hash || '#/') === '#/accounts') render();   // 刷掉行上的监听中标记
      };
      $$('[data-close]', root).forEach((b) => {
        const orig = b.onclick;
        b.onclick = () => teardown(orig);
      });
      // 点遮罩关闭也走同一套。这里直接调 close()，不能转发给原处理器——
      // 它靠 event.currentTarget 判断点的是不是遮罩本身，而事件派发结束后
      // currentTarget 会被置空，转发过去会判断失败、弹窗关不掉。
      const mask = $('.mask', root);
      mask.onclick = (ev) => {
        if (ev.target !== ev.currentTarget) return;
        teardown(close);
      };
      begin();
    },
  });
}

function loginFlow(id) {
  const step = (stage, hint) => {
    if (stage === 'done') { closeModal(); okToast('登录成功'); refreshStore().then(render); return; }
    modal({
      title: stage === 'code' ? '输入验证码' : '输入两步验证密码',
      body: `<p class="faint">${esc(hint || '')}</p>
             <div class="field"><label>${stage === 'code' ? '验证码（发送到你的 Telegram 客户端）' : '两步验证密码'}</label>
             <input id="lg-val" type="${stage === 'code' ? 'text' : 'password'}" autocomplete="off"></div>`,
      footer: '<button data-close>取消</button><button class="primary" data-ok>提交</button>',
      onMount: (root) => {
        const input = $('#lg-val', root);
        input.focus();
        const submit = promptToast(async () => {
          const url = stage === 'code' ? `/api/accounts/${id}/code` : `/api/accounts/${id}/password`;
          const body = stage === 'code' ? { code: input.value } : { password: input.value };
          const r = await POST(url, body);
          step(r.data.stage, r.data.hint);
        });
        $('[data-ok]', root).onclick = submit;
        input.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
      },
    });
  };
  promptToast(async () => {
    const r = await POST(`/api/accounts/${id}/login`);
    step(r.data.stage, r.data.hint);
  })();
}

// ─────────────────────────────────────────────────────────── 页面：设置
async function pageSettings(view) {
  const d = await GET('/api/settings');
  store.settings = d;
  const t = d.telegram, sys = d.system;

  view.innerHTML = `
    <header class="page">
      <div><h1>设置</h1><p>全局配置。api_id / api_hash 由所有账号共用。</p></div>
    </header>

    <div class="card">
      <h3>Telegram API</h3>
      <p class="faint" style="margin-top:0">
        在 <a href="https://my.telegram.org" target="_blank">my.telegram.org</a> →
        API development tools 申请，填一次即可，所有账号共用。
      </p>
      <div class="form-grid">
        <div class="field"><label>api_id <span class="req">*</span></label>
          <input type="number" id="set-apiid" value="${t.api_id || ''}" placeholder="1234567"></div>
        <div class="field"><label>api_hash ${t.has_hash ? '' : '<span class="req">*</span>'}</label>
          <input id="set-apihash" placeholder="${t.has_hash ? '已保存，留空则不修改' : '32 位十六进制'}">
          <div class="help">${t.has_hash ? `当前：<span class="mono">${esc(t.api_hash_masked)}</span>` : '尚未设置'}</div></div>
      </div>
      <div class="btn-row">
        <button class="primary" id="set-save">保存</button>
        <span class="faint" style="align-self:center">
          ${t.configured ? '<span class="tag ok">已配置</span>' : '<span class="tag err">未配置</span>'}
          · 当前 ${d.accounts.online}/${d.accounts.total} 个账号在线
        </span>
      </div>
      <p class="faint mt" style="font-size:12px;margin-bottom:0">
        ⚠️ 账号的登录凭证（session）绑定在生成它的那个 api_id 上。
        <strong>改动 api_id 会让已登录的账号失效</strong>，需要重新登录或重新导入。
        没有特殊原因不要改。
      </p>
    </div>

    <div class="card mt">
      <h3>系统信息</h3>
      <div class="t-wrap"><table><tbody>
        ${[
          ['版本', sys.version],
          ['已运行', fmtDuration(sys.uptime)],
          ['后台口令', sys.auth_enabled ? '已开启' : '未开启（仅本机访问）'],
          ['数据目录', sys.data_dir],
          ['会话目录', sys.sessions_dir],
          ['媒体目录', sys.media_dir],
          ['插件目录', sys.plugin_dirs.join('、')],
          ['插件热重载', sys.auto_reload ? '开启' : '关闭'],
          ['全局发送间隔', sys.send_interval + ' 秒'],
          ['单轮最多处理', sys.max_items_per_run + ' 条'],
          ['最大可等限流', sys.max_flood_wait + ' 秒'],
          ['媒体保留', sys.media_retention_days > 0 ? sys.media_retention_days + ' 天' : '用完即删'],
          ['发完删缓存', sys.cleanup_after_send ? '开启（工作流可单独覆盖）' : '关闭'],
          ['媒体占用上限', sys.media_max_mb > 0 ? sys.media_max_mb + ' MB（超出先删最旧的）' : '不限'],
          ['媒体当前占用', sys.media_usage_label || '0 B'],
          ['投递记录保留', sys.delivery_keep_days > 0 ? sys.delivery_keep_days + ' 天' : '永久'],
        ].map(([k, v]) => `<tr><td class="faint" style="width:130px">${esc(k)}</td>
             <td class="mono" style="word-break:break-all">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div>
      <p class="faint" style="font-size:12px;margin-bottom:0">
        这些来自 <code>config.yaml</code>，改动后需重启服务生效。
      </p>
    </div>`;

  $('#set-save').onclick = promptToast(async () => {
    const btn = $('#set-save');
    const apiId = $('#set-apiid').value.trim();
    const apiHash = $('#set-apihash').value.trim();
    if (!apiId) return errToast('请填写 api_id');
    if (!t.has_hash && !apiHash) return errToast('请填写 api_hash');

    const doSave = async () => {
      btn.disabled = true;
      try {
        const r = await PUT('/api/settings/telegram', { api_id: +apiId, api_hash: apiHash });
        if (r.data.warning) errToast(r.data.warning); else okToast('已保存');
        await refreshStore(); render();
      } finally { btn.disabled = false; }
    };

    // 改动会踢掉已登录的账号，先问一句
    const changing = t.configured && (+apiId !== +t.api_id || !!apiHash);
    if (changing && d.accounts.online > 0) {
      confirmBox(
        `当前有 ${d.accounts.online} 个账号处于登录状态。改动 api_id / api_hash 会让它们的 session 失效，` +
        '需要重新登录或重新导入。确定要改吗？',
        doSave);
    } else {
      await doSave();
    }
  });
}

// ─────────────────────────────────────────────────────────── 页面：日志
let logTimer = null;
async function pageLogs(view) {
  view.innerHTML = `
    <header class="page">
      <div><h1>运行日志</h1><p>实时输出，每 3 秒自动刷新。</p></div>
      <div class="spacer"></div>
      <div class="btn-row">
        <select id="lv" style="width:auto">
          <option value="">全部级别</option><option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option><option value="ERROR">ERROR</option>
        </select>
        <button id="pause">暂停刷新</button>
      </div>
    </header>
    <div class="card"><div class="log-view" id="logbox">加载中…</div></div>`;

  let paused = false;
  const load = async () => {
    if (paused) return;
    try {
      const rows = await GET(`/api/logs?limit=300${$('#lv').value ? `&level=${$('#lv').value}` : ''}`);
      $('#logbox').innerHTML = rows.length ? rows.map((r) =>
        `<div class="log-line"><span class="ts">${esc(r.ts)}</span> <span class="lv-${r.level}">${r.level.padEnd(7)}</span> <span class="nm">${esc(r.name)}</span> ${esc(r.msg)}</div>`
      ).join('') : '<span class="faint">暂无日志</span>';
    } catch (e) { /* 页面可能已切换 */ }
  };
  $('#lv').onchange = load;
  $('#pause').onclick = (e) => { paused = !paused; e.target.textContent = paused ? '继续刷新' : '暂停刷新'; if (!paused) load(); };
  await load();
  clearInterval(logTimer);
  logTimer = setInterval(() => { if (location.hash === '#/logs') load(); else clearInterval(logTimer); }, 3000);
}

// ─────────────────────────────────────────────────────────── 路由
const ROUTES = [
  { hash: '#/', icon: '📊', name: '概览', page: pageOverview },
  { hash: '#/channels', icon: '📢', name: '频道管理', page: pageChannels, count: () => store.channels.length },
  { hash: '#/workflows', icon: '⚙️', name: '工作流', page: pageWorkflows, count: () => store.workflows.length },
  { hash: '#/plugins', icon: '🧩', name: '插件管理', page: pagePlugins, count: () => store.plugins.length },
  { hash: '#/accounts', icon: '👤', name: '账号', page: pageAccounts, count: () => store.accounts.length },
  { hash: '#/logs', icon: '📜', name: '日志', page: pageLogs },
  { hash: '#/settings', icon: '🔧', name: '设置', page: pageSettings },
];

function paintNav() {
  const cur = location.hash || '#/';
  $('#nav').innerHTML = ROUTES.map((r) => `
    <a href="${r.hash}" class="${r.hash === cur ? 'active' : ''}">
      <span>${r.icon}</span><span>${r.name}</span>
      ${r.count ? `<span class="badge">${r.count()}</span>` : ''}
    </a>`).join('');
}

async function render() {
  const route = ROUTES.find((r) => r.hash === (location.hash || '#/')) || ROUTES[0];
  const view = $('#view');
  paintNav();
  try {
    await route.page(view);
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty"><div class="big">⚠️</div>${esc(e.message)}</div></div>`;
  }
  paintNav();
}

async function boot() {
  try {
    const ping = await fetch('/api/ping').then((r) => r.json());
    if (ping.auth && !token()) promptToken();
    await refreshStore();
    $('#foot').innerHTML = `TeleOps v0.1.0<br>插件 ${store.plugins.length} 个已加载`;
  } catch (e) {
    $('#health').style.background = 'var(--err)';
    $('#foot').textContent = '连接后端失败';
  }
  window.addEventListener('hashchange', render);
  await render();
  setInterval(() => { if ((location.hash || '#/') === '#/') render(); }, 20000);
}

boot();
})();
