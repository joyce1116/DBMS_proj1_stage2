import {$, $$, api, escape, types, date, openDialog, busy, pager} from './api.js';

// One bounded picker serves both survey snapshots and bank membership. Nothing
// is selected implicitly: the user confirms an immutable, concrete version.
export function chooseVersion({title = '从题库选题', isCurrent = () => true, onChoose, ownedOnly = false}) {
  let view = 'owned', offset = 0, bank = null, selected = null, request = 0;
  const limit = 8;
  const dialog = openDialog(title, '<div id="selection-body"></div>', async (form, modal) => {
    if (!selected) throw new Error('请先选择一个具体版本。');
    if (!active()) return;
    await onChoose(selected, modal);
  });
  $('button[type=submit]', dialog).textContent = '选用此版本';
  const container = $('#selection-body',dialog);
  const form = container.closest('form');
  const active = () => isCurrent() && dialog.open && container.isConnected;

  async function load() {
    const current = ++request;
    selected = null;
    const area = $('#selection-body',dialog);
    if (!active() || !area) return;
    $('button[type=submit]',dialog).disabled = true;
    area.innerHTML = '<div class="loading" role="status">正在读取可用题目…</div>';
    let path;
    if (bank) path = `/api/question-banks/${bank.id}/entries?offset=${offset}&limit=${limit}`;
    else if (view.endsWith('banks')) path = `/api/question-banks?view=${view === 'owned-banks' ? 'owned' : 'shared'}&offset=${offset}&limit=${limit}`;
    else path = `${view === 'owned' ? '/api/questions' : '/api/shared-questions'}?offset=${offset}&limit=${limit}`;
    const data = await api(path);
    if (!active() || current !== request) return;
    const tabs = [['owned','我的题目'],['shared','共享给我的题目'],['owned-banks','我的题库'],['shared-banks','共享题库']];
    const versions = bank ? data.items.map(entry => entry.question) : data.items;
    const selectingBank = !bank && view.endsWith('banks');
    area.innerHTML = `${ownedOnly ? '' : `<div class="tabs" role="tablist" aria-label="选题范围">${tabs.map(([key,label]) => `<button type="button" class="tab ${key === view ? 'active' : ''}" role="tab" aria-selected="${key === view}" data-source-view="${key}">${label}</button>`).join('')}</div>`}${bank ? `<button type="button" class="link-button" id="selection-back">← 返回题库列表</button><h3>${escape(bank.title)}</h3>` : ''}<p class="form-note">${selectingBank ? '打开一个题库，再选择其中保存的具体版本。' : '选用的是当前展示的具体版本；以后改题或换库内版本不会自动修改本卷。'}</p><div class="selection-list">${data.items.length ? selectingBank ? data.items.map(item => `<button type="button" class="surface selection-bank" data-pick-bank="${item.id}"><strong>${escape(item.title)}</strong><small>${escape(item.description)}</small></button>`).join('') : versions.map((item,index) => `<label class="selection-option"><input type="radio" name="selected-version" value="${index}"><span><strong>${escape(item.content.title)}</strong><small>${types[item.content.type]} · v${item.version} · ${date(item.created_at)}</small>${item.content.options ? `<span class="selection-preview">${item.content.options.map(o => escape(o.label)).join(' / ')}</span>` : ''}</span></label>`).join('') : '<p class="notice">这里还没有可选内容。</p>'}</div>${pager(data.total,offset,limit)}`;
    $$('[data-source-view]',area).forEach(button => button.onclick = () => busy(button, async () => {view = button.dataset.sourceView; bank = null; offset = 0; await load();},form));
    $$('[data-pick-bank]',area).forEach(button => button.onclick = () => busy(button, async () => {bank = data.items.find(item => item.id === button.dataset.pickBank); offset = 0; await load();},form));
    if ($('#selection-back',area)) $('#selection-back',area).onclick = () => busy($('#selection-back',area), async () => {bank = null; offset = 0; await load();},form);
    $$('[name=selected-version]',area).forEach(input => input.onchange = () => {selected = versions[Number(input.value)]; $('button[type=submit]',dialog).disabled = false;});
    $('[data-previous]',area).onclick = () => busy($('[data-previous]',area), async () => {offset -= limit; await load();},form);
    $('[data-next]',area).onclick = () => busy($('[data-next]',area), async () => {offset += limit; await load();},form);
  }
  busy(null,load,form);
  return dialog;
}
