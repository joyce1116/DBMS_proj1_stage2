import {$, api, escape, date, types, busy, pager} from './api.js';

const pageSize = 20;
const number = value => value === null ? '—' : new Intl.NumberFormat('zh-CN',{maximumFractionDigits:4}).format(value);

export async function questionStatistics(id, isCurrent = () => true) {
  const base = `/api/questions/${encodeURIComponent(id)}/statistics`;
  let stats = await api(`${base}?offset=0&limit=${pageSize}`);
  let distributionOffset = 0, contentsOffset = 0, familyOffset = 0;
  let distributionRequest = 0, contentsRequest = 0, familyRequest = 0;
  if (!isCurrent()) return;

  function summary() {
    const target = $('#cross-summary');
    target.innerHTML = `<div class="stat-summary"><section class="surface"><strong data-metric="surveys">${stats.survey_count}</strong><small>纳入统计的问卷</small></section><section class="surface"><strong data-metric="occurrences">${stats.question_occurrence_count}</strong><small>题目引用次数</small></section><section class="surface"><strong data-metric="responses">${stats.total_responses}</strong><small>已提交答卷</small></section></div><article class="surface stat-card"><div class="card-top"><div><h2>${escape(stats.title)}</h2><span class="pill-info">${escape(types[stats.type])} · v${stats.version}</span></div></div><div class="stat-meta"><span>经过 <strong data-metric="visits">${stats.visited_count}</strong> 次</span><span>有效回答 <strong data-metric="answers">${stats.answer_count}</strong> 次</span></div>${stats.options.length ? stats.options.map(option => `<div class="bar-row"><span>${escape(option.label)}</span><progress max="${Math.max(1,stats.answer_count)}" value="${option.count}" aria-label="${escape(option.label)}：${option.count} 次"></progress><strong>${option.count}</strong></div>`).join('') : stats.type === 'number' ? `<div class="average">${number(stats.average)}<small>平均值</small></div><div class="meta-row"><span>最小值 ${number(stats.min_value)}</span><span>最大值 ${number(stats.max_value)}</span></div>` : '<p class="form-note">有效文本回答见下方填写内容。</p>'}</article>`;
  }

  function distributionPanel(data) {
    const section = $('#cross-distribution');
    section.innerHTML = `<section class="surface stat-card"><h2>数值分布</h2><p class="form-note">相同数值合并计数，按数值从小到大分页。</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p>${data.distribution.length ? `<div class="table-scroll"><table><thead><tr><th>数值</th><th>回答次数</th></tr></thead><tbody>${data.distribution.map(item => `<tr><td>${escape(item.value)}</td><td>${item.count}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">暂时没有有效数值回答。</p>'}${pager(data.distribution_total,distributionOffset,pageSize)}</section>`;
    $('[data-previous]',section).onclick = event => busy(event.currentTarget,async () => { distributionOffset = Math.max(0,distributionOffset-pageSize); await distribution(); },section);
    $('[data-next]',section).onclick = event => busy(event.currentTarget,async () => { distributionOffset += pageSize; await distribution(); },section);
  }

  async function distribution() {
    const requested = ++distributionRequest, requestedOffset = distributionOffset;
    const data = await api(`${base}?offset=${requestedOffset}&limit=${pageSize}`);
    if (!isCurrent() || requested !== distributionRequest) return;
    stats = data; summary(); distributionPanel(data);
  }

  async function contents() {
    const requested = ++contentsRequest, requestedOffset = contentsOffset;
    const data = await api(`${base}/contents?offset=${requestedOffset}&limit=${pageSize}`);
    if (!isCurrent() || requested !== contentsRequest) return;
    const section = $('#cross-contents');
    section.innerHTML = `<section class="surface stat-card"><h2>填写内容</h2><p class="form-note">仅显示自己问卷的有效回答，不包含填写者身份。</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p>${data.items.length ? `<ul class="content-list">${data.items.map(item => `<li><p>${escape(item.value)}</p><small>提交于 ${date(item.submitted_at)} · <a class="link-button" href="/#/surveys/${escape(item.survey_id)}/results">查看所属问卷统计</a></small></li>`).join('')}</ul>` : '<p class="muted">暂时没有有效填写内容。</p>'}${pager(data.total,contentsOffset,pageSize)}</section>`;
    $('[data-previous]',section).onclick = event => busy(event.currentTarget,async () => { contentsOffset = Math.max(0,contentsOffset-pageSize); await contents(); },section);
    $('[data-next]',section).onclick = event => busy(event.currentTarget,async () => { contentsOffset += pageSize; await contents(); },section);
  }

  async function family() {
    const requested = ++familyRequest, requestedOffset = familyOffset;
    let data;
    try { data = await api(`${base}/family?offset=${requestedOffset}&limit=${pageSize}`); }
    catch (exception) {
      if (!isCurrent() || requested !== familyRequest) return;
      if (exception.code !== 403 && exception.code !== 404) throw exception;
      $('#cross-family').innerHTML = '<section class="surface stat-card"><h2>各版本汇总</h2><p class="notice">只有题目原作者可以查看整个题目系列。你仍可查看当前版本在自己问卷中的统计。</p></section>';
      return;
    }
    if (!isCurrent() || requested !== familyRequest) return;
    const section = $('#cross-family');
    section.innerHTML = `<section class="surface stat-card"><h2>各版本汇总</h2><p class="form-note">每个版本分别统计，题型、选项或题意变化不会合并。未被使用的版本也会显示。</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="table-scroll"><table><thead><tr><th>版本</th><th>题目</th><th>题型</th><th>问卷数</th><th>引用数</th><th>答卷数</th><th>经过次数</th><th>有效回答</th><th>平均值</th><th>范围</th></tr></thead><tbody>${data.versions.map(item => `<tr><td><a class="link-button" href="/#/questions/${escape(item.version_id)}/statistics">v${item.version}</a></td><td>${escape(item.title)}</td><td>${escape(types[item.type])}</td><td>${item.survey_count}</td><td>${item.question_occurrence_count}</td><td>${item.total_responses}</td><td>${item.visited_count}</td><td>${item.answer_count}</td><td>${number(item.average)}</td><td>${item.type === 'number' ? `${number(item.min_value)}～${number(item.max_value)}` : '—'}</td></tr>`).join('')}</tbody></table></div>${pager(data.total_versions,familyOffset,pageSize)}</section>`;
    $('[data-previous]',section).onclick = event => busy(event.currentTarget,async () => { familyOffset = Math.max(0,familyOffset-pageSize); await family(); },section);
    $('[data-next]',section).onclick = event => busy(event.currentTarget,async () => { familyOffset += pageSize; await family(); },section);
  }

  $('#main').innerHTML = `<a class="back-link" href="/#/questions">← 返回题目库</a><section class="page-heading"><div><div class="eyebrow">CROSS-SURVEY STATISTICS</div><h1>跨问卷统计</h1><p>查看同一题目版本在自己问卷中的回答。</p></div><button class="button secondary" id="refresh-cross-statistics">刷新统计</button></section><p class="notice">范围：我的已发布或已关闭问卷（包含已截止问卷），草稿不参与统计。同一答卷只计一份；重复引用同一版本时，经过与有效回答按每道题分别计数。跳过不计经过，未填写不计有效回答或平均值。</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div id="cross-summary"></div><div id="cross-distribution"></div><div id="cross-contents"></div><div class="actions"><button class="button secondary" id="load-family-statistics">查看各版本汇总</button></div><div id="cross-family"></div>`;
  summary();
  $('#refresh-cross-statistics').onclick = () => busy($('#refresh-cross-statistics'),async () => {
    // Re-enter through the router, which also invalidates pending section reads.
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  $('#load-family-statistics').onclick = () => busy($('#load-family-statistics'),async () => { familyOffset = 0; await family(); });
  if (stats.type === 'number') distributionPanel(stats);
  if (stats.type === 'number' || stats.type === 'text') await contents();
}
