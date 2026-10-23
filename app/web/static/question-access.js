import {$, $$, api, escape, date, busy, toast, pager} from './api.js';

// Shared by an individual saved version and an entire bank.
export async function mountGrants(container, base, isCurrent = () => true, {kind = 'version'} = {}) {
  let offset = 0, request = 0;
  const limit = 20;
  const current = () => isCurrent() && container.isConnected;
  async function load() {
    const epoch = ++request;
    const page = await api(`${base}/grants?offset=${offset}&limit=${limit}`);
    if (!current() || epoch !== request) return;
    container.innerHTML = `<p class="notice">${kind === 'bank' ? '题库授权允许读取和选用题库的当前成员。调整成员或替换版本后，共享范围会随之变化。' : '此处只授权当前这个已保存版本。新版本需要另行授权；对方不能修改原题、查看版本历史或再次授权。'}撤销授权不会删除已经选入问卷的题目副本。</p><form data-grant-form class="surface"><h2>添加只读授权</h2><div class="field"><label for="grant-username">授权用户名</label><input id="grant-username" name="username" required maxlength="256" autocomplete="off" placeholder="填写已注册的用户名"></div><button type="submit" class="button">添加授权</button><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p></form><section class="surface" data-grant-list><h2>已授权用户</h2><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p>${page.items.length ? page.items.map(grant => `<article class="question-card" data-grant-user="${escape(grant.grantee_id)}"><div class="card-top"><div><h3>${escape(grant.username)}</h3><p class="muted">授权于 ${date(grant.created_at)}</p></div><button class="button secondary small" data-revoke="${escape(grant.grantee_id)}">撤销授权</button></div></article>`).join('') : '<p class="muted">尚未授予其他用户访问权限。</p>'}${pager(page.total,offset,limit)}</section>`;
    const form = $('[data-grant-form]', container);
    form.onsubmit = event => {
      event.preventDefault();
      busy($('button[type=submit]',form), async () => {
        await api(`${base}/grants`, {method:'POST', body:{username:form.elements.username.value}});
        if (!current() || !form.isConnected) return;
        offset = 0; await load(); if (current()) toast('授权已保存');
      },form);
    };
    $$('[data-revoke]',container).forEach(button => button.onclick = () => busy(button,async () => {
      if (!confirm('确认撤销这个用户的授权？已有问卷副本会保留，其他有效授权仍可提供访问。')) return;
      await api(`${base}/grants/${button.dataset.revoke}`,{method:'DELETE'});
      if (!current() || !button.isConnected) return;
      if (page.items.length === 1 && offset > 0) offset -= limit;
      await load(); if (current()) toast('授权已撤销');
    },$('[data-grant-list]',container)));
    $('[data-previous]',container).onclick = event => busy(event.currentTarget,async () => { offset -= limit; await load(); },container);
    $('[data-next]',container).onclick = event => busy(event.currentTarget,async () => { offset += limit; await load(); },container);
  }
  await load();
}

export async function mountUsage(container, versionId, isCurrent = () => true) {
  let scope = 'version', offset = 0, request = 0;
  const limit = 20;
  const current = () => isCurrent() && container.isConnected;
  async function load() {
    const epoch = ++request;
    const page = await api(`/api/questions/${versionId}/usage?scope=${scope}&offset=${offset}&limit=${limit}`);
    if (!current() || epoch !== request) return;
    container.innerHTML = `<section class="surface"><h2>问卷中的使用位置</h2><p class="muted">包含草稿、已发布和已关闭的问卷。他人的问卷仅显示使用次数，不显示标题、状态、内容或答卷。</p><div class="field"><label for="usage-scope">使用范围</label><select id="usage-scope"><option value="version" ${scope === 'version' ? 'selected' : ''}>当前版本</option><option value="family" ${scope === 'family' ? 'selected' : ''}>同一题目的全部版本</option></select></div><p data-usage-totals>${page.total_surveys} 份问卷 · ${page.total_occurrences} 处使用</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div data-usage-items>${page.items.length ? page.items.map(item => `<article class="question-card"><h3>${item.owned ? `<a href="/#/surveys/${escape(item.survey_id)}/edit">${escape(item.title)}</a>` : '他人的问卷'}</h3><p class="muted">${item.questions.length} 处使用${item.owned ? ` · ${{draft:'草稿',published:'已发布',closed:'已关闭'}[item.status] || ''}` : ''}</p><p>${item.questions.map(q => `版本 v${q.version}`).join(' · ')}</p></article>`).join('') : '<p class="muted">暂无问卷选用这个范围内的版本。</p>'}</div>${pager(page.total_surveys,offset,limit)}</section>`;
    $('#usage-scope',container).onchange = event => busy(null,async () => { scope = event.target.value; offset = 0; await load(); },container);
    $('[data-previous]',container).onclick = event => busy(event.currentTarget,async () => { offset -= limit; await load(); },container);
    $('[data-next]',container).onclick = event => busy(event.currentTarget,async () => { offset += limit; await load(); },container);
  }
  await load();
}
