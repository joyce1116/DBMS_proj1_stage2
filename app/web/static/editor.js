import {$, $$, api, escape, date, types, status, busy, toast, openDialog} from './api.js';

const operations = {eq:'等于 / 完全一致', contains_any:'包含任意一个', contains_all:'包含全部', ne:'不等于', gt:'大于', gte:'大于或等于', lt:'小于', lte:'小于或等于'};
const numberInput = (id, label, value, {min = 0, max = '', step = 1, required = false} = {}) => `<div class="field"><label for="${id}">${label}</label><input id="${id}" name="${id}" type="number" step="${step}" min="${min}" ${max !== '' ? `max="${max}"` : ''} value="${value ?? ''}" ${required ? 'required' : ''}></div>`;
const numeric = value => value === '' ? null : Number(value);

export async function editor(id, isCurrent = () => true) {
  let survey = await api(`/api/surveys/${id}`);
  let tab = 'questions';
  let editingRule = null;
  const base = `/api/surveys/${id}`;
  const readonly = () => survey.status !== 'draft';

  function shell() {
    if (!isCurrent()) return;
    $('#main').innerHTML = `<a class="back-link" href="/#/surveys">← 返回我的问卷</a><section class="page-heading"><div><div class="eyebrow">QUESTIONNAIRE STUDIO</div><h1>${escape(survey.title)}</h1><p>${status(survey)} <span class="muted">　${survey.question_count} 道题目 · 更新于 ${date(survey.updated_at)}</span></p></div><a class="button secondary" href="/#/surveys/${id}/results">查看统计 →</a></section><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="editor-layout"><section><div class="tabs" role="tablist" aria-label="问卷编辑"><button class="tab ${tab === 'questions' ? 'active' : ''}" role="tab" aria-selected="${tab === 'questions'}" data-tab="questions">题目编辑</button><button class="tab ${tab === 'rules' ? 'active' : ''}" role="tab" aria-selected="${tab === 'rules'}" data-tab="rules">跳转规则 <small>${survey.jump_rules.length}</small></button><button class="tab ${tab === 'settings' ? 'active' : ''}" role="tab" aria-selected="${tab === 'settings'}" data-tab="settings">问卷设置</button></div><div id="editor-content"></div></section><aside class="surface editor-sidebar"><section class="sidebar-section"><h3>${readonly() ? '问卷状态' : '准备好收集回答了吗？'}</h3><p class="muted">${readonly() ? '发布后的内容已固定，所有答卷都对应同一份问卷。' : '题目和规则会分别保存。发布前，请检查标题、填写限制和跳转。'}</p>${survey.status === 'draft' ? `<button class="button" id="publish" ${survey.questions.length ? '' : 'disabled'}>发布问卷</button>${survey.questions.length ? '' : '<small>至少添加一道题目后才能发布。</small>'}` : survey.status === 'published' ? '<button class="button danger-button" id="close-survey">关闭问卷</button>' : '<span class="badge closed">已结束收集</span>'}</section>${survey.share_id ? `<section class="sidebar-section"><h3>问卷分享链接</h3><label class="sr-label" for="share-url">分享链接</label><input id="share-url" class="copy-field" value="${escape(location.origin + survey.share_url)}" readonly><button class="button secondary small" id="copy-link">复制链接</button><a class="button secondary small" href="${survey.share_url}" target="_blank" rel="noopener">打开填写页面 ↗</a><small>填写者需要登录账号。</small></section>` : '<section class="sidebar-section"><h3>草稿只有你可见</h3><p class="muted">发布后会生成独立的填写链接，可发送给其他人。</p></section>'}</aside></div>`;
    $$('[data-tab]').forEach(button => button.onclick = () => {
      if ($('#settings-form')?.dataset.dirty && !confirm('设置尚未保存。确认放弃这些修改？')) return;
      tab = button.dataset.tab; editingRule = null; shell();
    });
    if (tab === 'questions') questions();
    if (tab === 'settings') settings();
    if (tab === 'rules') rules();
    if ($('#publish')) $('#publish').onclick = () => busy($('#publish'), async () => {
      if ($('#settings-form')?.dataset.dirty) throw new Error('请先保存问卷设置，再发布。');
      if (!confirm('发布后将固定题目、规则和填写设置。确认发布这份问卷？')) return;
      survey = await api(base + '/publish', {method:'POST', body:{expected_revision:survey.revision}});
      shell(); toast('已发布，可以分享填写链接了');
    });
    if ($('#close-survey')) $('#close-survey').onclick = () => busy($('#close-survey'), async () => {
      if (!confirm('关闭后不再接受新的填写，已有答卷会保留。确认关闭？')) return;
      survey = await api(base + '/close', {method:'POST', body:{expected_revision:survey.revision}});
      shell(); toast('问卷已关闭，已有答卷仍可查看');
    });
    if ($('#copy-link')) $('#copy-link').onclick = async () => {
      try { await navigator.clipboard.writeText($('#share-url').value); toast('填写链接已复制'); }
      catch { $('#share-url').select(); toast('请复制已选中的链接'); }
    };
  }

  function questions() {
    $('#editor-content').innerHTML = `${readonly() ? '<p class="notice">问卷已发布，题目和规则已固定。你可以查看统计或关闭收集。</p>' : ''}${survey.questions.length ? survey.questions.map((q,index) => `<article class="surface question-card"><div class="question-number">QUESTION ${String(index+1).padStart(2,'0')} · ${types[q.type]}</div><div class="card-top"><h3>${escape(q.title)}${q.required ? '<span class="required-mark" aria-hidden="true">*</span>' : ''}</h3>${readonly() ? '' : `<div class="actions"><button class="icon-button" data-up="${index}" ${index === 0 ? 'disabled' : ''} aria-label="上移第${index+1}题">↑</button><button class="icon-button" data-down="${index}" ${index === survey.questions.length-1 ? 'disabled' : ''} aria-label="下移第${index+1}题">↓</button><button class="button secondary small" data-edit="${index}">编辑</button><button class="icon-button" data-delete="${index}" aria-label="删除第${index+1}题">×</button></div>`}</div><div class="question-preview">${q.options ? q.options.map(o => `<p>${q.type === 'single_choice' ? '○' : '□'}　${escape(o.label)}</p>`).join('') : `<p>${q.type === 'text' ? `${q.min_length}–${q.max_length} 字` : `${q.integer_only ? '整数' : '数字'} · ${q.min_value ?? '不限下限'} 至 ${q.max_value ?? '不限上限'}`}</p>`}${q.type === 'multiple_choice' ? `<small>选择 ${q.min_selections}–${q.max_selections ?? q.options.length} 项</small>` : ''}</div></article>`).join('') : '<div class="empty"><div class="empty-symbol" aria-hidden="true">＋</div><h2>先添加第一道题目</h2><p>选择一种题型，写下你想了解的问题。</p></div>'}${readonly() ? '' : `<div class="question-add"><span>添加题目</span>${Object.entries(types).map(([kind,label]) => `<button class="button secondary small" data-add="${kind}">＋ ${label}</button>`).join('')}</div>`}`;
    $$('[data-add]').forEach(button => button.onclick = () => questionDialog(button.dataset.add));
    $$('[data-edit]').forEach(button => button.onclick = () => questionDialog(survey.questions[button.dataset.edit].type, survey.questions[button.dataset.edit]));
    $$('[data-delete]').forEach(button => button.onclick = () => busy(button, async () => {
      const question = survey.questions[button.dataset.delete];
      if (!confirm(`确认删除“${question.title}”？`)) return;
      survey = await api(`${base}/questions/${question.id}?expected_revision=${survey.revision}`, {method:'DELETE'});
      shell(); toast('题目已删除');
    }));
    $$('[data-up], [data-down]').forEach(button => button.onclick = () => busy(button, async () => {
      const from = Number(button.dataset.up ?? button.dataset.down), to = from + (button.dataset.up !== undefined ? -1 : 1);
      const order = survey.questions.map(q => q.id); [order[from],order[to]] = [order[to],order[from]];
      survey = await api(base + `/question-order?expected_revision=${survey.revision}`, {method:'PUT', body:{question_ids:order}});
      shell(); toast('题目顺序已保存');
    }));
  }

  function questionDialog(kind, previous = null) {
    const dialog = openDialog(previous ? '编辑题目' : `添加${types[kind]}`, `<div class="field"><label for="question-title">题目标题</label><input id="question-title" name="title" required maxlength="500" value="${escape(previous?.title || '')}" placeholder="写下你的问题"></div><div class="field check-field"><label class="check-label"><input name="required" type="checkbox" ${previous?.required ? 'checked' : ''}>此题必须回答</label></div><div id="question-config"></div>`, async (form, modal) => {
      const body = {type:kind, title:form.elements.title.value, required:form.elements.required.checked};
      if (kind.endsWith('choice')) {
        body.options = $$('.option-input', form).map(input => ({...(input.dataset.id ? {id:input.dataset.id} : {}), label:input.value}));
        if (kind === 'multiple_choice') {
          body.min_selections = numeric(form.elements.min_selections.value) ?? 0;
          body.max_selections = numeric(form.elements.max_selections.value);
        }
      } else if (kind === 'text') {
        body.min_length = numeric(form.elements.min_length.value) ?? 0;
        body.max_length = numeric(form.elements.max_length.value) ?? 1000;
      } else {
        body.min_value = numeric(form.elements.min_value.value);
        body.max_value = numeric(form.elements.max_value.value);
        body.integer_only = form.elements.integer_only.checked;
      }
      survey = await api(previous ? `${base}/questions/${previous.id}?expected_revision=${survey.revision}` : `${base}/questions?expected_revision=${survey.revision}`, {method:previous ? 'PUT' : 'POST', body});
      modal.close(); shell(); toast('题目已保存');
    });
    const config = $('#question-config', dialog);
    if (kind.endsWith('choice')) {
      config.innerHTML = `<div class="form-section-title">选项</div><div id="options"></div><button class="link-button" type="button" id="add-option">＋ 添加选项</button><p class="form-note">2–50 个选项，选项文字不能重复。</p>${kind === 'multiple_choice' ? `<div class="field-row">${numberInput('min_selections','最少选择数',previous?.min_selections ?? 0,{max:50})}${numberInput('max_selections','最多选择数',previous?.max_selections ?? '',{max:50})}</div><p class="form-note">最多选择数留空表示不限；上下限相同表示恰好选择这些项。</p>` : ''}`;
      function optionRow(option = {}) {
        const row = document.createElement('div'); row.className = 'option-row';
        row.innerHTML = `<span class="option-marker">${kind === 'single_choice' ? '○' : '□'}</span><input class="option-input" ${option.id ? `data-id="${escape(option.id)}"` : ''} value="${escape(option.label || '')}" required maxlength="200" aria-label="选项文字" placeholder="选项文字"><button class="icon-button" type="button" aria-label="移除此选项">×</button>`;
        $('button',row).onclick = () => { if ($$('.option-row',dialog).length <= 2) return toast('至少保留两个选项'); row.remove(); };
        $('#options',dialog).append(row);
      }
      (previous?.options || [{label:'选项 1'},{label:'选项 2'}]).forEach(optionRow);
      $('#add-option',dialog).onclick = () => { if ($$('.option-row',dialog).length >= 50) return toast('最多添加 50 个选项'); optionRow(); };
    } else if (kind === 'text') {
      config.innerHTML = `<div class="field-row">${numberInput('min_length','最少字数',previous?.min_length ?? 0,{max:10000})}${numberInput('max_length','最多字数',previous?.max_length ?? 1000,{min:1,max:10000,required:true})}</div>`;
    } else {
      config.innerHTML = `<div class="field-row">${numberInput('min_value','最小值',previous?.min_value,{min:-1e15,max:1e15,step:'any'})}${numberInput('max_value','最大值',previous?.max_value,{min:-1e15,max:1e15,step:'any'})}</div><div class="field check-field"><label class="check-label"><input name="integer_only" type="checkbox" ${previous?.integer_only ? 'checked' : ''}>只允许整数</label></div><p class="notice danger" data-number-bound-error role="alert" hidden>只允许整数时，最小值和最大值也必须是整数。</p><p class="form-note">上下限留空表示不限；勾选“只允许整数”后，上下限也须为整数。有效数值范围为 ±10¹⁵。</p>`;
      function validateBounds() {
        const integerOnly = $('[name=integer_only]',config).checked;
        let invalid = false;
        for (const input of $$('input[type=number]',config)) {
          input.step = integerOnly ? '1' : 'any';
          const fractional = integerOnly && input.value !== '' && !Number.isInteger(input.valueAsNumber);
          input.setCustomValidity(fractional ? '只允许整数时，最小值和最大值也必须是整数。' : '');
          input.setAttribute('aria-invalid',String(fractional));
          invalid ||= fractional;
        }
        $('[data-number-bound-error]',config).hidden = !invalid;
      }
      $$('input',config).forEach(input => input.addEventListener('input',validateBounds));
      validateBounds();
    }
  }

  function settings() {
    const localDeadline = survey.deadline ? new Date(new Date(survey.deadline) - new Date(survey.deadline).getTimezoneOffset()*60000).toISOString().slice(0,23) : '';
    $('#editor-content').innerHTML = `<form id="settings-form" class="surface"><h2>问卷设置</h2><fieldset ${readonly() ? 'disabled' : ''}><div class="field"><label for="edit-title">问卷标题</label><input id="edit-title" name="title" required maxlength="200" value="${escape(survey.title)}"></div><div class="field"><label for="edit-description">问卷说明</label><textarea id="edit-description" name="description" maxlength="5000">${escape(survey.description)}</textarea></div><div class="field"><label for="deadline">截止时间</label><input id="deadline" name="deadline" type="datetime-local" step="0.001" value="${localDeadline}"><small>按本机当地时间设置；留空表示不限时间。</small></div><div class="field check-field"><label class="check-label"><input name="allow_anonymous" type="checkbox" ${survey.allow_anonymous ? 'checked' : ''}>允许匿名填写</label><small>匿名时，创建者查看答卷不会看到填写者账号；填写仍需登录。</small></div><div class="field check-field"><label class="check-label"><input name="allow_repeat" type="checkbox" ${survey.allow_repeat ? 'checked' : ''}>允许同一账号多次填写</label></div>${readonly() ? '<p class="notice">发布后的设置已固定。</p>' : '<button class="button" type="submit">保存设置</button>'}</fieldset></form>`;
    $('#settings-form').onsubmit = event => {
      event.preventDefault(); const form = event.currentTarget;
      busy($('button[type=submit]',form), async () => {
        const deadline = form.elements.deadline.value ? new Date(form.elements.deadline.value).toISOString() : null;
        survey = await api(base,{method:'PATCH',body:{expected_revision:survey.revision,title:form.elements.title.value,description:form.elements.description.value,deadline,allow_anonymous:form.elements.allow_anonymous.checked,allow_repeat:form.elements.allow_repeat.checked}});
        shell(); toast('问卷设置已保存');
      });
    };
    $('#settings-form').oninput = () => { $('#settings-form').dataset.dirty = 'true'; };
  }

  function describeRule(rule) {
    const source = survey.questions.find(q => q.id === rule.source_id);
    const target = survey.questions.find(q => q.id === rule.target_id);
    const value = rule.kind === 'number' ? rule.value : rule.option_ids.map(id => source?.options.find(o => o.id === id)?.label || '未知选项').join('、');
    return `当 <strong>${escape(source?.title || '未知题目')}</strong> ${operations[rule.operator]} <strong>${escape(value)}</strong> → <strong>${target ? escape(target.title) : '结束问卷'}</strong>`;
  }

  async function persistRules(value) {
    survey = await api(base + '/rules',{method:'PUT',body:{expected_revision:survey.revision,rules:value}});
    editingRule = null; shell(); toast('跳转规则已保存');
  }

  function rules() {
    const sources = survey.questions.filter(q => q.type !== 'text');
    $('#editor-content').innerHTML = `<p class="notice">按列表顺序判断，命中第一条就跳转；未命中继续下一题。只能向后跳转或结束问卷。</p>${survey.jump_rules.map((rule,index) => `<article class="surface question-card"><div class="rule-index">规则 ${String(index+1).padStart(2,'0')}</div><p class="rule-line">${describeRule(rule)}</p>${readonly() ? '' : `<div class="actions"><button class="button secondary small" data-edit-rule="${index}">编辑规则</button><button class="icon-button" data-up-rule="${index}" ${index === 0 ? 'disabled' : ''} aria-label="提高规则${index+1}优先级">↑</button><button class="icon-button" data-down-rule="${index}" ${index === survey.jump_rules.length-1 ? 'disabled' : ''} aria-label="降低规则${index+1}优先级">↓</button><button class="link-button" data-remove-rule="${index}">删除规则</button></div>`}</article>`).join('')}${readonly() ? '' : sources.length ? `<form id="rule-form" class="surface"><h2>${editingRule === null ? '添加跳转规则' : `编辑规则 ${editingRule+1}`}</h2><div class="field"><label for="rule-source">判断哪道题</label><select id="rule-source" name="source_id">${sources.map(q => `<option value="${q.id}">${survey.questions.indexOf(q)+1}. ${escape(q.title)}</option>`).join('')}</select></div><div id="rule-condition"></div><div class="field"><label for="rule-target">满足条件时跳到</label><select id="rule-target" name="target_id"></select></div><div class="actions"><button class="button" type="submit">保存规则</button>${editingRule !== null ? '<button type="button" id="cancel-rule" class="button secondary">取消编辑</button>' : ''}</div></form>` : '<div class="empty"><h2>先添加题目，再设置跳转</h2><p>单选、多选和数字题都可以作为判断条件。</p></div>'}`;
    $$('[data-remove-rule]').forEach(button => button.onclick = () => busy(button, () => persistRules(survey.jump_rules.filter((_,i) => i !== Number(button.dataset.removeRule)))));
    $$('[data-edit-rule]').forEach(button => button.onclick = () => { editingRule = Number(button.dataset.editRule); rules(); $('#rule-form').scrollIntoView({behavior:'smooth',block:'center'}); });
    $$('[data-up-rule], [data-down-rule]').forEach(button => button.onclick = () => busy(button, async () => {
      const value = [...survey.jump_rules], from = Number(button.dataset.upRule ?? button.dataset.downRule), to = from + (button.dataset.upRule !== undefined ? -1 : 1);
      [value[from],value[to]] = [value[to],value[from]]; await persistRules(value);
    }));
    if (!$('#rule-form')) return;
    const form = $('#rule-form'), initial = editingRule === null ? null : survey.jump_rules[editingRule];
    if (initial) form.elements.source_id.value = initial.source_id;
    function condition(rule = null) {
      const source = sources.find(q => q.id === form.elements.source_id.value);
      const ops = source.type === 'number' ? ['eq','ne','gt','gte','lt','lte'] : source.type === 'single_choice' ? ['eq'] : ['eq','contains_any','contains_all'];
      $('#rule-condition').innerHTML = `<div class="field"><label for="rule-operator">判断条件</label><select id="rule-operator" name="operator">${ops.map(op => `<option value="${op}">${operations[op]}</option>`).join('')}</select></div>${source.type === 'number' ? numberInput('rule-value','比较的数字',rule?.value,{min:-1e15,max:1e15,step:'any',required:true}) : `<fieldset class="field"><legend>匹配的选项</legend><div class="rule-options">${source.options.map(o => `<label class="check-label"><input type="${source.type === 'single_choice' ? 'radio' : 'checkbox'}" name="rule-option" value="${o.id}" ${rule?.option_ids?.includes(o.id) ? 'checked' : ''}>${escape(o.label)}</label>`).join('')}</div></fieldset>`}`;
      const position = survey.questions.indexOf(source);
      form.elements.target_id.innerHTML = survey.questions.slice(position+1).map(q => `<option value="${q.id}">${survey.questions.indexOf(q)+1}. ${escape(q.title)}</option>`).join('') + '<option value="end">结束问卷</option>';
      if (rule) { form.elements.operator.value = rule.operator; form.elements.target_id.value = rule.target_id; }
    }
    condition(initial); form.elements.source_id.onchange = () => condition();
    if ($('#cancel-rule')) $('#cancel-rule').onclick = () => { editingRule = null; rules(); };
    form.onsubmit = event => {
      event.preventDefault();
      busy($('button[type=submit]',form), async () => {
        const source = sources.find(q => q.id === form.elements.source_id.value);
        const rule = {kind:source.type === 'number' ? 'number' : 'choice',source_id:source.id,target_id:form.elements.target_id.value,operator:form.elements.operator.value};
        if (rule.kind === 'number') rule.value = numeric(form.elements['rule-value'].value);
        else {
          rule.option_ids = $$('[name=rule-option]:checked',form).map(input => input.value);
          if (!rule.option_ids.length) throw new Error('请至少选择一个匹配选项。');
        }
        const value = [...survey.jump_rules];
        if (editingRule === null) value.push(rule); else value[editingRule] = rule;
        await persistRules(value);
      });
    };
  }

  shell();
}
