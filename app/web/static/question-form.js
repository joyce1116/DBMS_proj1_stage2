import {$, $$, escape, openDialog, toast, types} from './api.js';

const numeric = value => value === '' ? null : Number(value);
const numberInput = (id, label, value, {min = 0, max = '', step = 1, required = false} = {}) => `<div class="field"><label for="${id}">${label}</label><input id="${id}" name="${id}" type="number" step="${step}" min="${min}" ${max !== '' ? `max="${max}"` : ''} value="${escape(value ?? '')}" ${required ? 'required' : ''}></div>`;

// The survey editor and reusable versions share the phase-one form and validation.
export function questionForm(kind, previous, onSave, {title, notice = '', isCurrent = () => true} = {}) {
  const dialog = openDialog(title || (previous ? '编辑题目' : `添加${types[kind]}`), `${notice ? `<p class="notice">${escape(notice)}</p>` : ''}<div class="field"><label for="question-title">题目标题</label><input id="question-title" name="title" required maxlength="500" value="${escape(previous?.title || '')}" placeholder="写下你的问题"></div><div class="field check-field"><label class="check-label"><input name="required" type="checkbox" ${previous?.required ? 'checked' : ''}>此题必须回答</label></div><div id="question-config"></div>`, async (form, modal) => {
    if (!isCurrent()) return;
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
    await onSave(body, modal);
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
  return dialog;
}
