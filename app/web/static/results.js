import {$, $$, api, escape, date, types, status, busy, pager, answerText} from './api.js';

export async function results(id, isCurrent = () => true) {
  const base = `/api/surveys/${id}`;
  let survey = await api(base), stats = await api(base + '/statistics');
  let tab = 'overview', offset = 0, questionOffset = 0, selected = '';
  const limit = 20;

  function statisticCard(question) {
    return `<article class="surface stat-card"><div class="card-top"><h3>${escape(question.title)}</h3><span class="pill-info">${types[question.type]}</span></div><div class="stat-meta"><span>经过 ${question.visited_count} 份</span><span>有效回答 ${question.answer_count} 份</span></div>${question.options.length ? question.options.map(option => `<div class="bar-row"><span>${escape(option.label)}</span><progress max="${Math.max(1,question.answer_count)}" value="${option.count}" aria-label="${escape(option.label)}：${option.count} 次"></progress><strong>${option.count}</strong></div>`).join('') : question.type === 'number' ? `<div class="average">${question.average === null ? '—' : new Intl.NumberFormat('zh-CN',{maximumFractionDigits:4}).format(question.average)}<small>平均值</small></div>` : '<p class="muted form-note">选择本题，查看填写者提交的文本内容。</p>'}${!question.options.length && !selected ? `<button class="link-button" data-contents="${question.question_id}">查看全部填写内容 →</button>` : ''}</article>`;
  }

  async function shell() {
    if (!isCurrent()) return;
    $('#main').innerHTML = `<a class="back-link" href="/#/surveys/${id}/edit">← 返回问卷</a><section class="page-heading"><div><div class="eyebrow">LISTEN & UNDERSTAND</div><h1>${escape(survey.title)}</h1><p>每一份回答，汇成更完整的认识。</p></div><button class="button secondary" id="refresh-results">刷新数据 ↻</button></section><div class="stat-summary"><section class="surface"><strong>${stats.total_responses}</strong><small>已提交答卷</small></section><section class="surface"><strong>${survey.question_count}</strong><small>问卷题目</small></section><section class="surface">${status(survey)}<small>截止：${date(survey.deadline)}</small></section></div><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="tabs" role="tablist" aria-label="统计视图"><button class="tab ${tab === 'overview' ? 'active' : ''}" role="tab" aria-selected="${tab === 'overview'}" data-results-tab="overview">统计概览</button><button class="tab ${tab === 'responses' ? 'active' : ''}" role="tab" aria-selected="${tab === 'responses'}" data-results-tab="responses">答卷明细</button></div><div id="results-content"></div>`;
    $('#refresh-results').onclick = () => busy($('#refresh-results'), async () => {
      [survey,stats] = await Promise.all([api(base),api(base + '/statistics')]); await shell();
    });
    $$('[data-results-tab]').forEach(button => button.onclick = () => busy(button, async () => { tab = button.dataset.resultsTab; await shell(); }));
    if (tab === 'overview') await overview(); else await responses();
  }

  async function overview() {
    if (!isCurrent()) return;
    $('#results-content').innerHTML = `<div class="field stat-filter"><label for="question-filter">查看题目</label><select id="question-filter"><option value="">全部题目</option>${survey.questions.map((q,index) => `<option value="${q.id}" ${q.id === selected ? 'selected' : ''}>${index+1}. ${escape(q.title)}</option>`).join('')}</select></div><p class="form-note">统计按答卷份数计算；同一账号重复填写会分别计数。跳过的题目不计入回答，未填写不计入平均值。</p><div id="statistics">${stats.questions.length ? stats.questions.filter(q => !selected || q.question_id === selected).map(statisticCard).join('') : '<div class="empty"><h2>问卷中还没有题目</h2><p>添加题目并发布问卷后，即可收集和查看回答。</p></div>'}</div><div id="question-contents"></div>`;
    $('#question-filter').onchange = () => busy(null, async () => { selected = $('#question-filter').value; questionOffset = 0; await overview(); });
    $$('[data-contents]').forEach(button => button.onclick = () => busy(button, async () => { selected = button.dataset.contents; questionOffset = 0; await overview(); }));
    if (selected) {
      const question = survey.questions.find(q => q.id === selected);
      if (question.type === 'text' || question.type === 'number') await contents();
    }
  }

  async function contents() {
    const question = selected, requestedOffset = questionOffset;
    const detail = await api(`${base}/statistics/${question}?offset=${requestedOffset}&limit=${limit}`);
    if (!isCurrent() || tab !== 'overview' || question !== selected || requestedOffset !== questionOffset) return;
    $('#question-contents').innerHTML = `<section class="surface"><h3>全部填写内容</h3>${detail.contents.length ? `<ul class="content-list">${detail.contents.map(item => `<li>${escape(item.value)}</li>`).join('')}</ul>` : '<p class="muted">暂时没有填写内容。</p>'}${pager(detail.contents_total,questionOffset,limit)}</section>`;
    $('[data-previous]',$('#question-contents')).onclick = () => busy(null, async () => { questionOffset = Math.max(0,questionOffset-limit); await contents(); });
    $('[data-next]',$('#question-contents')).onclick = () => busy(null, async () => { questionOffset += limit; await contents(); });
  }

  async function responses() {
    const requestedOffset = offset;
    const page = await api(`${base}/responses?offset=${requestedOffset}&limit=${limit}`);
    if (!isCurrent() || tab !== 'responses' || requestedOffset !== offset) return;
    $('#results-content').innerHTML = `${page.items.length ? page.items.map((response,index) => `<article class="surface response-card"><div class="card-top"><div><h3>答卷 ${page.total-offset-index} · ${response.anonymous ? '匿名填写' : escape(response.respondent?.username || '账号不可用')}</h3><small>提交于 ${date(response.submitted_at)}</small></div><span class="pill-info">${response.answers.length} 道经过的题目</span></div>${response.answers.map(answer => { const question = survey.questions.find(q => q.id === answer.question_id); return `<div class="answer-review"><strong>${escape(question.title)}</strong><p>${escape(answerText(question,answer.value))}</p></div>`; }).join('')}</article>`).join('') : '<div class="empty"><h2>还没有收到答卷</h2><p>发布问卷并分享链接后，填写者的提交会显示在这里。</p></div>'}${pager(page.total,offset,limit)}`;
    $('[data-previous]').onclick = () => busy(null, async () => { offset = Math.max(0,offset-limit); await responses(); });
    $('[data-next]').onclick = () => busy(null, async () => { offset += limit; await responses(); });
  }

  await shell();
}
