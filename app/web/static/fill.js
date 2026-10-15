import {$, $$, api, user, escape, date, types, busy, answerText} from './api.js';

export async function fillSurvey(shareId, isCurrent = () => true) {
  const share = `/survey/${encodeURIComponent(shareId)}`;
  const storageKey = `survey_submission:${user.id}:${shareId}`;
  let survey, answers = {}, path = [], current = null, editingValue = null, pending = null;
  let anonymous = false;
  let navigationBusy = false;
  let cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); } catch { sessionStorage.removeItem(storageKey); }

  function remember(value) {
    sessionStorage.setItem(storageKey, JSON.stringify(value));
  }

  // Preserve the exact in-flight payload across reloads. In particular, a
  // network failure after insertion must not create a second response on retry.
  if (cached?.pending) {
    pending = cached.pending;
    $('#main').innerHTML = `<div class="fill-wrap surface"><div class="eyebrow">提交确认</div><h1>确认上次的提交结果</h1><p class="muted">上次提交尚未收到确认。请使用同一份内容重新确认，避免重复填写。</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><button class="button" id="retry-pending">确认提交结果</button></div>`;
    $('#retry-pending').onclick = () => busy($('#retry-pending'), () => sendPending(cached.title));
    return;
  }
  if (cached?.receipt) {
    success(cached.receipt, cached.title, cached.allowRepeat);
    return;
  }

  survey = await api(share);
  const initial = await api(share + '/navigate', {method:'POST',body:{survey_revision:survey.revision, answers:{}}});
  current = initial.next_question_id;
  renderQuestion();

  function heading() {
    return `<div class="fill-heading"><div class="eyebrow">YOUR VOICE MATTERS</div><h1>${escape(survey.title)}</h1>${survey.description ? `<p>${escape(survey.description)}</p>` : ''}<div class="fill-meta"><span>共 ${survey.questions.length} 道题，按回答自动跳转</span><span>截止：${date(survey.deadline)}</span></div></div>`;
  }

  function renderQuestion() {
    if (!isCurrent()) return;
    const question = survey.questions.find(q => q.id === current);
    const value = editingValue;
    let input;
    if (question.options) {
      input = `<div class="answer-options">${question.options.map(option => `<label class="answer-option"><input type="${question.type === 'single_choice' ? 'radio' : 'checkbox'}" name="answer" value="${option.id}" ${(Array.isArray(value) ? value.includes(option.id) : value === option.id) ? 'checked' : ''}><span>${escape(option.label)}</span></label>`).join('')}</div>`;
    } else if (question.type === 'text') {
      input = `<div class="field"><label for="answer" class="sr-label">你的回答</label><textarea id="answer" name="answer" placeholder="请填写你的回答">${escape(value ?? '')}</textarea></div>`;
    } else {
      input = `<div class="field"><label for="answer" class="sr-label">你的回答</label><input id="answer" name="answer" type="number" step="${question.integer_only ? '1' : 'any'}" ${question.min_value !== null ? `min="${question.min_value}"` : ''} ${question.max_value !== null ? `max="${question.max_value}"` : ''} value="${value ?? ''}" placeholder="请输入数字"></div>`;
    }
    const hint = question.type === 'multiple_choice' ? `请选择 ${Math.max(question.min_selections, Number(question.required))}–${question.max_selections ?? question.options.length} 项` : question.type === 'text' ? `${question.min_length}–${question.max_length} 字` : question.type === 'number' ? `${question.integer_only ? '请输入整数' : '请输入数字'}${question.min_value !== null ? `，最小 ${question.min_value}` : ''}${question.max_value !== null ? `，最大 ${question.max_value}` : ''}` : '请选择一个选项';
    $('#main').innerHTML = `<div class="fill-wrap">${heading()}<form id="answer-form" class="surface question-body"><div class="step-label">正在回答第 ${path.length + 1} 道题 · ${types[question.type]}</div><h2>${escape(question.title)}${question.required ? '<span class="required-mark" aria-hidden="true">*</span>' : ''}</h2><p class="form-note">${hint} · ${question.required ? '必答' : '选答，可以留空'}</p>${input}<p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="fill-actions"><button class="button secondary" type="button" id="back-question" ${path.length ? '' : 'disabled'}>← 上一题</button><button class="button" type="submit">下一步 →</button></div></form><p class="auth-footer">回答会在最后确认提交后保存。请勿在填写过程中刷新页面。</p></div>`;
    $('#answer-form').onsubmit = event => {
      event.preventDefault();
      busy($('#answer-form button[type=submit]'), async () => {
        navigationBusy = true;
        $('#back-question').disabled = true;
        try {
          let value;
          if (question.type === 'single_choice') value = $('[name=answer]:checked')?.value ?? null;
          else if (question.type === 'multiple_choice') value = $$('[name=answer]:checked').map(input => input.value);
          else if (question.type === 'text') value = $('#answer').value;
          else value = $('#answer').value === '' ? null : $('#answer').valueAsNumber;
          const proposed = {...answers, [current]:value};
          const next = await api(share + '/navigate', {method:'POST',body:{survey_revision:survey.revision,answers:proposed}});
          answers = Object.fromEntries(next.answers.map(answer => [answer.question_id,answer.value]));
          path = next.path; current = next.next_question_id; editingValue = null;
          if (next.completed) review(); else renderQuestion();
          window.scrollTo({top:0,behavior:'smooth'});
        } finally { navigationBusy = false; if ($('#back-question')) $('#back-question').disabled = !path.length; }
      });
    };
    $('#back-question').onclick = () => goBack(path.at(-1));
  }

  function goBack(target) {
    if (!target || pending || navigationBusy) return;
    const index = path.indexOf(target);
    editingValue = answers[target];
    path = path.slice(0,index);
    answers = Object.fromEntries(path.map(id => [id, answers[id]]));
    current = target;
    renderQuestion();
  }

  function review() {
    if (!isCurrent()) return;
    $('#main').innerHTML = `<div class="fill-wrap">${heading()}<section class="surface question-body"><div class="step-label">最后一步 · 核对并提交</div><h2>谢谢，每一份回答都很重要。</h2><p class="muted form-note">请确认以下内容。修改前面的回答后，后续题目会重新判断。</p><div>${path.map(id => { const question = survey.questions.find(q => q.id === id); return `<div class="answer-review"><button class="link-button" data-revise="${id}">修改</button><strong>${escape(question.title)}</strong><p>${escape(answerText(question,answers[id]))}</p></div>`; }).join('')}</div>${survey.allow_anonymous ? `<div class="field check-field"><label class="check-label"><input id="anonymous" type="checkbox" ${anonymous ? 'checked' : ''}>匿名提交</label><small>创建者查看答卷时不会看到你的账号。</small></div>` : '<p class="form-note">本问卷采用实名填写，创建者可以看到你的用户名。</p>'}<p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="fill-actions"><button class="button secondary" id="review-back">← 返回修改</button><button class="button" id="submit-response">确认提交</button></div></section></div>`;
    $$('[data-revise]').forEach(button => button.onclick = () => goBack(button.dataset.revise));
    $('#review-back').onclick = () => goBack(path.at(-1));
    if ($('#anonymous')) $('#anonymous').onchange = event => { anonymous = event.target.checked; };
    $('#submit-response').onclick = () => busy($('#submit-response'), async () => {
      if (!pending) {
        pending = {survey_revision:survey.revision,request_id:crypto.randomUUID(),anonymous,answers};
        remember({pending,title:survey.title,allowRepeat:survey.allow_repeat});
      }
      await sendPending(survey.title);
    });
  }

  async function sendPending(title) {
    let receipt;
    try { receipt = await api(share + '/responses',{method:'POST',body:pending}); }
    catch (exception) {
      if (exception.code === 0 || exception.code >= 500) {
        $$('[data-revise], #review-back, #anonymous').forEach(control => { control.disabled = true; });
        if ($('#submit-response')) $('#submit-response').textContent = '重试确认提交';
        throw new Error('暂时未收到保存确认。请保持内容不变，点击重试确认提交；刷新页面也可继续确认。');
      }
      if (exception.code !== 401) { pending = null; sessionStorage.removeItem(storageKey); }
      throw exception;
    }
    const allowRepeat = survey?.allow_repeat ?? cached?.allowRepeat ?? false;
    pending = null;
    remember({receipt,title,allowRepeat});
    success(receipt,title,allowRepeat);
  }

  function success(receipt, title, allowRepeat) {
    if (!isCurrent()) return;
    $('#main').innerHTML = `<div class="fill-wrap"><section class="surface success-card"><div class="success-symbol" aria-hidden="true">✓</div><h2>回答已成功提交</h2><p class="muted">感谢你为“${escape(title)}”留下真实的声音。</p><p class="receipt">${date(receipt.submitted_at)} · ${receipt.anonymous ? '匿名填写' : '实名填写'}<br>回执编号：${escape(receipt.response_id)}</p><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="actions"><a class="button secondary" href="/">返回我的问卷</a>${allowRepeat ? '<button class="button" id="fill-again">再填写一次</button>' : ''}</div></section></div>`;
    if ($('#fill-again')) $('#fill-again').onclick = () => busy($('#fill-again'), async () => {
      await api(share); // A closed survey cannot start a new fill.
      sessionStorage.removeItem(storageKey);
      await fillSurvey(shareId, isCurrent);
    });
  }
}
