import {$, $$, api, user, escape, date, types, busy, toast, pager} from './api.js';
import {questionForm} from './question-form.js';
import {mountGrants, mountUsage} from './question-access.js';

const operations = {create:'首次创建',revise:'修改后另存',restore:'从旧版本恢复'};

export function questionPreview(question) {
  return `<div class="question-preview">${question.options ? question.options.map(option => `<p>${question.type === 'single_choice' ? '○' : '□'}　${escape(option.label)}</p>`).join('') : question.type === 'text' ? `<p>${question.min_length}–${question.max_length} 字</p>` : `<p>${question.integer_only ? '整数' : '数字'} · ${escape(question.min_value ?? '不限下限')} 至 ${escape(question.max_value ?? '不限上限')}</p>`}${question.type === 'multiple_choice' ? `<small>选择 ${question.min_selections}–${question.max_selections ?? question.options.length} 项</small>` : ''}<p class="muted">${question.required ? '必须回答' : '允许不回答'}</p></div>`;
}

export async function questionsPage(isCurrent = () => true) {
  let view = 'owned', offset = 0, request = 0;
  const limit = 12;
  async function load() {
    const epoch = ++request;
    const page = await api(`${view === 'owned' ? '/api/questions' : '/api/shared-questions'}?offset=${offset}&limit=${limit}`);
    if (!isCurrent() || epoch !== request) return;
    $('#main').innerHTML = `<section class="page-heading"><div><div class="eyebrow">QUESTION LIBRARY</div><h1>题目库</h1><p class="muted">独立保存题目版本，在多份问卷中重复选用。</p></div><a class="button secondary" href="/#/banks">管理题库 →</a></section><div class="tabs" role="tablist" aria-label="题目来源"><button class="tab ${view === 'owned' ? 'active' : ''}" role="tab" aria-selected="${view === 'owned'}" data-view="owned">我的题目</button><button class="tab ${view === 'shared' ? 'active' : ''}" role="tab" aria-selected="${view === 'shared'}" data-view="shared">共享给我</button></div><div data-questions-page><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p>${view === 'owned' ? `<div class="question-add"><span>新建可复用题目</span>${Object.entries(types).map(([kind,label]) => `<button class="button secondary small" data-create="${kind}">＋ ${label}</button>`).join('')}</div><p class="notice">已保存的版本会一直保留。修改题目时另存为新版本，已选用该题的问卷不会自动改变。下方列出全部版本。</p>` : '<p class="notice">可以读取和选用已授权的版本。共享不会赋予修改、查看历史或再次授权的权限；题库共享的可选版本会随题库成员更新。</p>'}<div class="survey-grid">${page.items.length ? page.items.map(version => `<article class="surface survey-card" data-question-card="${escape(version.id)}"><div class="card-top"><span class="badge">${types[version.content.type]}</span><span class="muted">v${version.version}</span></div><h2>${escape(version.content.title)}</h2><p class="muted">${operations[version.operation]} · ${date(version.created_at)}</p><div class="actions"><a class="button secondary small" href="/#/questions/${escape(version.id)}">查看版本 →</a><a class="link-button" href="/#/questions/${escape(version.id)}/statistics">跨问卷统计</a></div></article>`).join('') : `<div class="empty"><h2>${view === 'owned' ? '还没有保存可复用题目' : '暂无共享题目'}</h2><p>${view === 'owned' ? '从上方选择题型创建第一道题。原有问卷内的自定义题目仍在原问卷中。' : '其他用户对你授权后，可在这里选用题目。'}</p></div>`}</div>${pager(page.total,offset,limit)}</div>`;
    const root = $('[data-questions-page]');
    $$('[data-view]').forEach(button => button.onclick = () => busy(button,async () => { view = button.dataset.view; offset = 0; await load(); },root));
    $$('[data-create]',root).forEach(button => button.onclick = () => questionForm(button.dataset.create,null,async (body,modal) => {
      const form = $('form',modal);
      const saved = await api('/api/questions',{method:'POST',body});
      if (!isCurrent() || !modal.open || $('form',modal) !== form) return;
      modal.close(); toast('题目已保存为版本 v1'); location.hash = `/questions/${saved.id}`;
    },{title:`新建${types[button.dataset.create]}`,notice:'保存后成为不可覆盖的题目版本，可以在问卷中选用。',isCurrent}));
    $('[data-previous]',root).onclick = event => busy(event.currentTarget,async () => { offset -= limit; await load(); },root);
    $('[data-next]',root).onclick = event => busy(event.currentTarget,async () => { offset += limit; await load(); },root);
  }
  await load();
}

export async function questionPage(id, isCurrent = () => true) {
  const version = await api(`/api/questions/${id}`);
  if (!isCurrent()) return;
  const owned = version.owner_id === user?.id;
  let tab = 'content', historyOffset = 0, request = 0;
  const limit = 20;
  $('#main').innerHTML = `<a class="back-link" href="/#/questions">← 返回题目库</a><section class="page-heading"><div><div class="eyebrow">SAVED QUESTION · v${version.version}</div><h1>${escape(version.content.title)}</h1><p class="muted">${types[version.content.type]} · ${operations[version.operation]} · ${date(version.created_at)}</p></div><a class="button secondary" href="/#/questions/${escape(id)}/statistics">跨问卷统计 →</a></section><p class="notice">${owned ? '这是已保存的固定版本。创建新版本或从旧版恢复都会保留所有历史，已选用该题的问卷需要单独替换版本。' : '你正在查看共享的只读版本，可以在自己的草稿问卷中选用。无法修改原题、查看版本历史或再次授权。'}</p><div class="tabs" role="tablist" aria-label="题目版本">${[['content','题目内容'],...(owned ? [['history','版本历史'],['grants','授权管理'],['usage','使用位置']] : [])].map(([key,label]) => `<button class="tab ${key === tab ? 'active' : ''}" role="tab" aria-selected="${key === tab}" data-question-tab="${key}">${label}</button>`).join('')}</div><div data-question-panel></div>`;
  const panel = $('[data-question-panel]');
  async function show() {
    const epoch = ++request;
    const current = () => isCurrent() && panel.isConnected && epoch === request;
    if (!current()) return;
    $$('[data-question-tab]').forEach(button => { const active = button.dataset.questionTab === tab; button.classList.toggle('active',active); button.setAttribute('aria-selected',String(active)); });
    panel.innerHTML = '<p data-error class="notice danger" role="alert" tabindex="-1" hidden></p>';
    if (tab === 'content') {
      panel.innerHTML += `<article class="surface question-card"><h2>${escape(version.content.title)}</h2>${questionPreview(version.content)}</article>${owned ? `<section class="surface"><h2>继续修改这道题</h2><p class="muted">以当前版本为基础保存新版本，可以修改题型、内容和限制。保留的选项会继续沿用原选项标识。</p><div class="field"><label for="version-type">新版本题型</label><select id="version-type">${Object.entries(types).map(([kind,label]) => `<option value="${kind}" ${kind === version.content.type ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="actions"><button class="button" data-revise>创建新版本</button><button class="button secondary" data-restore>从此版本恢复</button></div></section>` : ''}`;
      if (owned) {
        $('[data-revise]',panel).onclick = () => {
          const kind = $('#version-type',panel).value;
          questionForm(kind,version.content,async (body,modal) => {
            const form = $('form',modal);
            const saved = await api(`/api/questions/${id}/versions`,{method:'POST',body});
            if (!current() || !modal.open || $('form',modal) !== form) return;
            modal.close(); toast(`已保存为新版本 v${saved.version}`); location.hash = `/questions/${saved.id}`;
          },{title:'创建题目新版本',notice:`基于版本 v${version.version} 另存，当前版本及问卷中的副本会保留。`,isCurrent:current});
        };
        $('[data-restore]',panel).onclick = event => busy(event.currentTarget,async () => {
          if (!confirm(`确认用版本 v${version.version} 的内容创建一个新版本？全部历史和已有问卷会保留。`)) return;
          const saved = await api(`/api/questions/${id}/restore`,{method:'POST'});
          if (!current()) return;
          toast(`已恢复内容并另存为版本 v${saved.version}`); location.hash = `/questions/${saved.id}`;
        },panel);
      }
    } else if (tab === 'history') {
      const page = await api(`/api/questions/${id}/history?offset=${historyOffset}&limit=${limit}`);
      if (!current()) return;
      panel.innerHTML += `<section class="surface"><h2>同一题目的版本历史</h2><p class="muted">按版本号从旧到新显示。打开任意旧版后，可以从其内容创建新版本或恢复。</p>${page.items.map(item => `<article class="question-card" data-history-version="${escape(item.id)}"><div class="card-top"><div><h3>v${item.version} · ${escape(item.content.title)}</h3><p class="muted">${types[item.content.type]} · ${operations[item.operation]} · ${date(item.created_at)}${item.parent_id ? ` · <a href="/#/questions/${escape(item.parent_id)}">查看来源版本</a>` : ''}</p></div><a class="button secondary small" href="/#/questions/${escape(item.id)}">打开 v${item.version}</a></div></article>`).join('')}${pager(page.total,historyOffset,limit)}</section>`;
      $('[data-previous]',panel).onclick = event => busy(event.currentTarget,async () => { historyOffset -= limit; await show(); },panel);
      $('[data-next]',panel).onclick = event => busy(event.currentTarget,async () => { historyOffset += limit; await show(); },panel);
    } else {
      const target = document.createElement('div'); panel.append(target);
      if (tab === 'grants') await mountGrants(target,`/api/questions/${id}`,current);
      if (tab === 'usage') await mountUsage(target,id,current);
    }
  }
  $$('[data-question-tab]').forEach(button => button.onclick = () => busy(button,async () => { tab = button.dataset.questionTab; await show(); },panel));
  await show();
}
