export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
export const date = value => value ? new Intl.DateTimeFormat('zh-CN', {dateStyle:'medium', timeStyle:'short'}).format(new Date(value)) : '不限时间';
export const types = {single_choice:'单选题', multiple_choice:'多选题', text:'文本填空', number:'数字填空'};
export const statuses = {draft:'草稿', open:'收集中', expired:'已截止', closed:'已关闭'};
export const status = survey => `<span class="badge ${escape(survey.availability)}">${statuses[survey.availability]}</span>`;
export let user = null;
export function setUser(value) { user = value; }
let pageEpoch = 0;
export function beginPage() { const epoch = ++pageEpoch; return () => epoch === pageEpoch; }

const messages = {
  'Invalid credentials or expired session':'账号、密码不正确，或登录已过期。请重新登录。',
  'Username is already registered':'这个用户名已被注册，请换一个。',
  'Survey or question not found':'没有找到这份问卷，或你没有查看权限。',
  'Survey is closed or expired':'这份问卷已关闭或截止，暂时无法填写。',
  'Survey version or state has changed; reload before retrying':'问卷已在其他页面被修改，请刷新后再操作。',
  'This account has already submitted a response':'你已经填写过这份问卷，创建者未允许重复填写。',
  'This request_id was already used for different content':'这次提交的内容发生变化，请先确认原提交结果。',
  'Anonymous responses are not enabled for this survey':'创建者没有开启匿名填写。',
  'Database unavailable':'暂时无法连接数据库，请稍后重试。',
  'Add at least one question before publishing':'请至少添加一道题目再发布。',
  'Deadline must be in the future when publishing':'截止时间已过，请修改后再发布。',
  'Jump targets must be after the source question':'只能跳到后面的题目或结束问卷。',
  'Rule source and target must belong to this survey':'跳转规则引用的题目已不存在，请先修改规则。',
  'Rule options must belong to its source question':'被修改的选项正用于跳转，请先修改规则。',
  'Choice rules require a choice source question':'该题目用于选择跳转，请先移除规则再更改题型。',
  'Numeric rules require a numeric source question':'该题目用于数字跳转，请先移除规则再更改题型。',
  'Deadline must be in the future':'截止时间必须晚于现在。',
  'Option labels must be unique, including Unicode/case equivalents':'选项文字不能重复，大小写或全角差异也会视为相同。',
  'Selection bounds must satisfy 0 <= min <= max <= option count':'选择数量应满足：0 ≤ 最少选择数 ≤ 最多选择数 ≤ 选项数。',
  'A required multiple-choice question must allow at least one selection':'必答多选题必须允许至少选择一个选项。',
  'Minimum text length cannot exceed maximum text length':'最少字数不能大于最多字数。',
  'Minimum numeric value cannot exceed maximum numeric value':'最小值不能大于最大值。',
  'The numeric interval must contain at least one integer':'这个范围内没有整数，请调整上下限。',
  'Integer question bounds must be integers':'只允许整数时，最小值和最大值也必须是整数。',
  'Question configuration is invalid; repair the draft first':'题目配置不符合要求，请检查整数题上下限等设置，修正后再发布。',
};

export class APIError extends Error {
  constructor(code, detail) {
    const raw = typeof detail === 'string' ? detail : detail?.message;
    let message = messages[raw] || raw;
    if (Array.isArray(detail)) {
      const location = detail[0]?.loc?.at(-1);
      const names = {username:'用户名', password:'密码', title:'标题', deadline:'截止时间', options:'选项', min_selections:'最少选择数', max_selections:'最多选择数', min_length:'最少字数', max_length:'最多字数', min_value:'最小值', max_value:'最大值'};
      message = messages[detail[0]?.msg?.replace(/^Value error, /,'')] || `${names[location] || '填写内容'}不符合要求，请检查类型、长度和上下限。`;
    }
    super(message || '操作失败，请检查填写内容后重试。');
    this.code = code; this.detail = detail;
  }
}

export async function api(path, {method = 'GET', body, auth = true} = {}) {
  const headers = {Accept:'application/json'};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const token = sessionStorage.getItem('survey_token');
  if (auth && token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache:'no-store'});
  } catch {
    throw new APIError(0, '网络连接失败，请确认服务正在运行后重试。');
  }
  let data;
  try { data = response.status === 204 ? null : await response.json(); }
  catch { throw new APIError(0, '未能确认服务器返回的结果，请重试。'); }
  if (!response.ok) {
    if (response.status === 401 && auth) {
      sessionStorage.removeItem('survey_token'); setUser(null);
      window.dispatchEvent(new Event('session-expired'));
    }
    throw new APIError(response.status, data.detail);
  }
  return data;
}

export function error(message, root = document) {
  const target = $('[data-error]', root);
  if (target) { target.textContent = message; target.hidden = !message; if (message) target.focus(); }
}

let toastTimer;
export function toast(message) {
  const target = $('#toast'); target.textContent = message; target.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { target.hidden = true; }, 3500);
}

export async function busy(button, task, root = document) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  error('', root);
  try { return await task(); } catch (exception) { error(exception.message, root); }
  finally { if (button?.isConnected) button.disabled = false; }
}

export function openDialog(title, content, onSubmit) {
  const dialog = $('#dialog');
  dialog.innerHTML = `<form id="modal-form"><div class="dialog-heading"><h2 id="dialog-title">${escape(title)}</h2><button type="button" class="icon-button" data-close aria-label="关闭对话框">×</button></div>${content}<p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><div class="actions end"><button type="button" class="button secondary" data-close>取消</button><button class="button" type="submit">保存</button></div></form>`;
  $$('[data-close]', dialog).forEach(button => button.onclick = () => dialog.close());
  $('#modal-form', dialog).onsubmit = event => {
    event.preventDefault(); busy($('button[type=submit]', dialog), () => onSubmit($('#modal-form', dialog), dialog), dialog);
  };
  dialog.showModal();
  return dialog;
}

export function answerText(question, value) {
  if (value === null || value === undefined) return '未填写';
  if (question.options) {
    const ids = Array.isArray(value) ? value : [value];
    return ids.map(id => question.options.find(o => o.id === id)?.label || '未知选项').join('、');
  }
  return String(value);
}

export function pager(total, offset, limit) {
  return `<div class="pagination"><span>共 ${total} 条${total ? ` · ${offset + 1}–${Math.min(offset + limit, total)}` : ''}</span><div class="actions"><button class="button secondary small" data-previous ${offset === 0 ? 'disabled' : ''}>上一页</button><button class="button secondary small" data-next ${offset + limit >= total ? 'disabled' : ''}>下一页</button></div></div>`;
}
