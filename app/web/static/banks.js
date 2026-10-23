import {$, $$, api, escape, date, types, busy, openDialog, pager, toast} from './api.js';
import {chooseVersion} from './selection-picker.js';
import {mountGrants} from './question-access.js';

const pageSize = 12;
const bankFields = bank => `<div class="field"><label for="bank-title">题库标题</label><input id="bank-title" name="title" required maxlength="200" value="${escape(bank?.title || '')}"></div><div class="field"><label for="bank-description">题库说明</label><textarea id="bank-description" name="description" maxlength="5000">${escape(bank?.description || '')}</textarea></div>`;

export async function banksPage(isCurrent = () => true) {
  let view = 'owned', offset = 0, request = 0;
  async function render() {
    const requested = ++request, requestedView = view, requestedOffset = offset;
    const data = await api(`/api/question-banks?view=${requestedView}&offset=${requestedOffset}&limit=${pageSize}`);
    if (!isCurrent() || requested !== request) return;
    $('#main').innerHTML = `<section class="page-heading"><div><div class="eyebrow">COLLECT & SHARE</div><h1>题库</h1><p>把可复用题目整理成题库，按账号共享。</p></div><button class="button" id="create-bank">新建题库</button></section><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="tabs" role="tablist" aria-label="题库视图"><button class="tab ${view === 'owned' ? 'active' : ''}" role="tab" aria-selected="${view === 'owned'}" data-bank-view="owned">我的题库</button><button class="tab ${view === 'shared' ? 'active' : ''}" role="tab" aria-selected="${view === 'shared'}" data-bank-view="shared">共享题库</button></div><div class="survey-grid">${data.items.map(bank => `<article class="surface survey-card"><div class="card-top"><h2>${escape(bank.title)}</h2><span class="pill-info">${bank.can_manage ? '可管理' : '只读共享'}</span></div><p class="description">${escape(bank.description || '暂无说明')}</p><div class="meta-row"><span>更新于 ${date(bank.updated_at)}</span></div><div class="card-footer"><a class="button secondary small" href="/#/banks/${escape(bank.id)}">打开题库</a></div></article>`).join('')}</div>${data.items.length ? '' : `<div class="empty"><h2>${view === 'owned' ? '还没有题库' : '暂无共享题库'}</h2><p>${view === 'owned' ? '先创建题库，再添加你保存的题目版本。' : '其他用户授权后，可以在这里查看题库中的当前版本。'}</p></div>`}<div id="banks-pagination">${pager(data.total,offset,pageSize)}</div>`;
    $$('[data-bank-view]').forEach(button => button.onclick = () => busy(button, async () => {
      view = button.dataset.bankView; offset = 0; await render();
    }));
    const navigation = $('#banks-pagination');
    $('[data-previous]', navigation).onclick = event => busy(event.currentTarget, async () => { offset = Math.max(0, offset-pageSize); await render(); });
    $('[data-next]', navigation).onclick = event => busy(event.currentTarget, async () => { offset += pageSize; await render(); });
    $('#create-bank').onclick = () => openDialog('新建题库', bankFields(), async (form, dialog) => {
      if (!isCurrent()) return;
      const bank = await api('/api/question-banks', {method:'POST', body:{title:form.elements.title.value, description:form.elements.description.value}});
      if (!isCurrent() || !form.isConnected || $('#modal-form',dialog) !== form) return;
      dialog.close(); toast('题库已创建。'); location.hash = `/banks/${bank.id}`;
    });
  }
  await render();
}

export async function bankPage(id, isCurrent = () => true) {
  const base = `/api/question-banks/${encodeURIComponent(id)}`;
  let bank = await api(base), offset = 0, entryRequest = 0, shellRequest = 0;
  if (!isCurrent()) return;

  async function entries() {
    const requested = ++entryRequest, requestedOffset = offset;
    const data = await api(`${base}/entries?offset=${requestedOffset}&limit=${pageSize}`);
    if (!isCurrent() || requested !== entryRequest) return;
    if (!data.items.length && offset > 0) { offset = Math.max(0,offset-pageSize); return entries(); }
    const section = $('#bank-entries');
    if (!section) return;
    section.innerHTML = `<div class="section-heading"><h2>题库题目</h2>${bank.can_manage ? '<button class="button secondary small" id="add-bank-entry">添加题目</button>' : '<span class="pill-info">只读共享</span>'}</div><p class="form-note">每个题目系列只保留一个当前版本。更换题库版本只影响以后的选题，已选入问卷的内容保持原样。</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p>${data.items.map(entry => `<article class="surface question-card" data-bank-entry="${escape(entry.family_id)}"><div class="card-top"><div><h3>${escape(entry.question.content.title)}</h3><div class="meta-row"><span>${escape(types[entry.question.content.type])}</span><span>版本 v${entry.question.version}</span><span>更新于 ${date(entry.updated_at)}</span></div></div></div><div class="actions"><a class="button secondary small" href="/#/questions/${escape(entry.version_id)}">查看题目</a><a class="button secondary small" href="/#/questions/${escape(entry.version_id)}/statistics">跨问卷统计</a>${bank.can_manage ? `<button class="button secondary small" data-replace-entry="${escape(entry.family_id)}">更新版本</button><button class="button danger-button small" data-remove-entry="${escape(entry.family_id)}">移出题库</button>` : ''}</div></article>`).join('') || '<div class="empty"><h2>题库中还没有题目</h2><p>题库所有者可以添加自己的题目版本。</p></div>'}${pager(data.total,offset,pageSize)}`;
    if (bank.can_manage) {
      $('#add-bank-entry').onclick = () => chooseVersion({title:'添加题库题目', ownedOnly:true, isCurrent, onChoose: async (version, dialog) => {
        if (!isCurrent()) return;
        const form = $('#modal-form',dialog);
        await api(base + '/entries', {method:'POST', body:{version_id:version.id}});
        if (!isCurrent() || !form?.isConnected || $('#modal-form',dialog) !== form) return;
        dialog.close(); toast('已添加题目。'); await entries();
      }});
      $$('[data-replace-entry]',section).forEach(button => button.onclick = () => {
        const entry = data.items.find(item => item.family_id === button.dataset.replaceEntry);
        chooseVersion({title:'更新题库中的题目版本', ownedOnly:true, isCurrent, onChoose: async (version, dialog) => {
          if (!isCurrent()) return;
          const form = $('#modal-form',dialog);
          if (version.family_id !== entry.family_id) throw new Error('请选择同一题目系列的版本；其他题目请用“添加题目”。');
          await api(`${base}/entries/${entry.family_id}`, {method:'PUT', body:{version_id:version.id, expected_version_id:entry.version_id}});
          if (!isCurrent() || !form?.isConnected || $('#modal-form',dialog) !== form) return;
          dialog.close(); toast('题库版本已更新。'); await entries();
        }});
      });
      $$('[data-remove-entry]',section).forEach(button => button.onclick = () => busy(button, async () => {
        const entry = data.items.find(item => item.family_id === button.dataset.removeEntry);
        if (!confirm('将这道题目移出题库？已选入问卷的题目不会变化。')) return;
        await api(`${base}/entries/${entry.family_id}?expected_version_id=${encodeURIComponent(entry.version_id)}`, {method:'DELETE'});
        if (!isCurrent()) return;
        toast('已移出题库。'); await entries();
      }, section));
    }
    $('[data-previous]',section).onclick = event => busy(event.currentTarget, async () => { offset = Math.max(0,offset-pageSize); await entries(); },section);
    $('[data-next]',section).onclick = event => busy(event.currentTarget, async () => { offset += pageSize; await entries(); },section);
  }

  async function shell() {
    if (!isCurrent()) return;
    const requested = ++shellRequest;
    // Invalidate any response for a DOM section that this shell replaces.
    ++entryRequest;
    $('#main').innerHTML = `<a class="back-link" href="/#/banks">← 返回题库</a><section class="page-heading"><div><div class="eyebrow">QUESTION BANK</div><h1>${escape(bank.title)}</h1><p class="line-break">${escape(bank.description || '暂无说明')}</p><small>更新于 ${date(bank.updated_at)} · ${bank.can_manage ? '我的题库' : '只读共享题库'}</small></div>${bank.can_manage ? '<div class="actions"><button class="button secondary" id="edit-bank">编辑题库</button><button class="button danger-button" id="archive-bank">归档题库</button></div>' : ''}</section><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><section id="bank-entries"></section>${bank.can_manage ? '<section id="bank-grants" class="surface"></section>' : '<p class="notice">可以查看并选用题库中的题目。授权与内容管理由题库所有者操作。</p>'}`;
    if (bank.can_manage) {
      $('#edit-bank').onclick = () => {
        const revision = bank.revision;
        openDialog('编辑题库',bankFields(bank),async (form,dialog) => {
          if (!isCurrent()) return;
          const updated = await api(base,{method:'PATCH',body:{expected_revision:revision,title:form.elements.title.value,description:form.elements.description.value}});
          if (!isCurrent() || !form.isConnected || $('#modal-form',dialog) !== form) return;
          bank = updated; dialog.close(); toast('题库信息已更新。'); await shell();
        });
      };
      $('#archive-bank').onclick = () => busy($('#archive-bank'), async () => {
        if (!confirm('归档这份题库？归档后将停止共享，已选入问卷的题目仍可正常使用。')) return;
        await api(`${base}?expected_revision=${bank.revision}`,{method:'DELETE'});
        if (!isCurrent()) return;
        toast('题库已归档。'); location.hash = '/banks';
      });
    }
    const tasks = [entries()];
    if (bank.can_manage) tasks.push(mountGrants($('#bank-grants'),base,() => isCurrent() && requested === shellRequest,{kind:'bank'}));
    await Promise.all(tasks);
  }

  await shell();
}
