import {$, $$, api, user, setUser, beginPage, escape, date, status, toast, busy, openDialog, pager} from './api.js';
import {editor} from './editor.js';
import {fillSurvey} from './fill.js';
import {results} from './results.js';

let renderVersion = 0;
let listOffset = 0;
const listLimit = 12;

function account() {
  $('#account').innerHTML = user ? `<a class="account-name" href="/#/surveys"><span class="avatar">${escape(user.username[0].toUpperCase())}</span>${escape(user.username)}</a><button id="logout" class="link-button">退出</button>` : '<span class="muted">让每一份回答，都有价值</span>';
  if (user) $('#logout').onclick = () => busy($('#logout'), async () => {
    await api('/api/auth/logout', {method:'POST'});
    sessionStorage.removeItem('survey_token'); setUser(null); account(); await render();
  });
}

function auth(mode = 'login') {
  const share = location.pathname.startsWith('/survey/');
  $('#main').innerHTML = `<div class="auth-layout"><section class="auth-intro"><div><div class="eyebrow">一个问题，一次连接</div><h1>好问题，<br>值得被听见。</h1><p>创建一份问卷，收集真实的声音。<br>把每一次回答，变成更清晰的认识。</p><div class="auth-art" aria-hidden="true"><span></span><span></span><span></span></div></div><small>创建 · 分享 · 收集 · 了解</small></section><section class="auth-form"><h2>${share ? '登录后继续填写' : '欢迎来到工作台'}</h2><p class="muted form-note">${share ? '填写问卷需要登录，匿名设置不影响登录要求。' : '从这里开始，管理属于你的问卷。'}</p><div class="tabs" role="tablist" aria-label="登录或注册"><button class="tab ${mode === 'login' ? 'active' : ''}" role="tab" aria-selected="${mode === 'login'}" data-mode="login">登录账号</button><button class="tab ${mode === 'register' ? 'active' : ''}" role="tab" aria-selected="${mode === 'register'}" data-mode="register">注册账号</button></div><form id="auth-form"><div class="field"><label for="username">用户名</label><input id="username" name="username" required minlength="3" maxlength="32" autocomplete="username" pattern="[A-Za-z0-9_]{3,32}" placeholder="3–32 位字母、数字或下划线"></div><div class="field"><label for="password">密码</label><input id="password" name="password" type="password" required minlength="8" maxlength="128" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" placeholder="8–128 位字符"></div><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p><button class="button" type="submit">${mode === 'login' ? '登录' : '注册并登录'}</button></form><p class="auth-footer">${share ? '登录后将返回当前问卷。' : '用户名不区分大小写，请妥善保管密码。'}</p></section></div>`;
  $$('[data-mode]').forEach(button => button.onclick = () => auth(button.dataset.mode));
  $('#auth-form').onsubmit = event => {
    event.preventDefault();
    busy($('#auth-form button[type=submit]'), async () => {
      const body = {username:$('#username').value, password:$('#password').value};
      if (mode === 'register') await api('/api/auth/register', {method:'POST', body, auth:false});
      const result = await api('/api/auth/login', {method:'POST', body, auth:false});
      sessionStorage.setItem('survey_token', result.access_token); setUser(result.user); account();
      await render();
    });
  };
}

async function dashboard(isCurrent = () => true) {
  const data = await api(`/api/surveys?offset=${listOffset}&limit=${listLimit}`);
  if (!isCurrent()) return;
  // Only the first metric is global; page-level counts are labelled explicitly.
  const active = data.items.filter(s => s.availability === 'open').length;
  $('#main').innerHTML = `<section class="page-heading"><div><div class="eyebrow">YOUR QUESTIONNAIRES</div><h1>我的问卷</h1><p>让想法成为问题，让回答带来方向。</p></div><button class="button" id="new-survey">＋ 创建问卷</button></section><div class="dashboard-summary"><section class="intro-card"><div class="eyebrow">每一份好奇，都可以开始</div><h2>从一个问题出发。</h2><p>添加题目与跳转规则，发布后即可分享给填写者。</p></section><section class="surface metric"><span class="muted">全部问卷</span><strong>${data.total}</strong><small>仅显示你创建的问卷</small></section><section class="surface metric"><span class="muted">本页收集中</span><strong>${active}</strong><small>已发布且仍在有效期内</small></section></div><div class="section-heading"><h2>问卷列表</h2><button class="link-button" id="refresh-list">刷新列表 ↻</button></div><p data-error class="notice danger" role="alert" tabindex="-1" hidden></p>${data.total ? `<div class="survey-grid">${data.items.map(s => `<article class="surface survey-card"><div class="card-top"><h3><a href="/#/surveys/${s.id}/edit">${escape(s.title)}</a></h3>${status(s)}</div><p class="description">${escape(s.description || '还没有填写问卷说明')}</p><div class="meta-row"><span>${s.question_count} 道题目</span><span>更新于 ${date(s.updated_at)}</span></div><div class="card-footer"><a class="button secondary small" href="/#/surveys/${s.id}/edit">${s.status === 'draft' ? '编辑问卷' : '查看问卷'}</a><a class="link-button" href="/#/surveys/${s.id}/results">查看统计 →</a></div></article>`).join('')}</div>${pager(data.total,listOffset,listLimit)}` : '<section class="empty"><div class="empty-symbol" aria-hidden="true">☷</div><h2>你的第一份问卷，从这里开始</h2><p>先写下标题，再添加题目。未发布的草稿只有你能看到。</p><button class="button" id="empty-create">创建第一份问卷</button></section>'}`;
  $('#new-survey').onclick = createSurvey;
  if ($('#empty-create')) $('#empty-create').onclick = createSurvey;
  $('#refresh-list').onclick = () => busy($('#refresh-list'), () => dashboard(isCurrent));
  if ($('[data-previous]')) $('[data-previous]').onclick = () => { listOffset = Math.max(0,listOffset-listLimit); render(); };
  if ($('[data-next]')) $('[data-next]').onclick = () => { listOffset += listLimit; render(); };
}

function createSurvey() {
  openDialog('创建一份问卷', `<div class="field"><label for="survey-title">问卷标题</label><input id="survey-title" name="title" required maxlength="200" placeholder="例如：课程体验调查"></div><div class="field"><label for="survey-description">问卷说明</label><textarea id="survey-description" name="description" maxlength="5000" placeholder="向填写者介绍这份问卷的目的"></textarea></div><div class="field check-field"><label class="check-label"><input name="allow_anonymous" type="checkbox">允许匿名填写</label></div><div class="field check-field"><label class="check-label"><input name="allow_repeat" type="checkbox">允许同一账号多次填写</label></div>`, async (form,dialog) => {
    const survey = await api('/api/surveys', {method:'POST', body:{title:form.elements.title.value, description:form.elements.description.value, allow_anonymous:form.elements.allow_anonymous.checked, allow_repeat:form.elements.allow_repeat.checked}});
    dialog.close(); toast('问卷草稿已创建'); location.href = `/#/surveys/${survey.id}/edit`;
  });
}

async function render() {
  const version = ++renderVersion;
  const isCurrent = beginPage();
  account();
  if (!user) { auth(); return; }
  $('#main').innerHTML = '<div class="loading" role="status">正在读取问卷…</div>';
  try {
    const share = location.pathname.match(/^\/survey\/([^/]+)$/);
    if (share) return await fillSurvey(share[1], isCurrent);
    const route = location.hash.slice(1) || '/surveys';
    const match = route.match(/^\/surveys\/([a-f0-9]{24})\/(edit|results)$/);
    if (match) {
      if (match[2] === 'edit') await editor(match[1], isCurrent);
      else await results(match[1], isCurrent);
    } else await dashboard(isCurrent);
  } catch (exception) {
    if (version !== renderVersion || !user) return;
    $('#main').innerHTML = `<section class="fill-wrap surface"><h1>暂时无法打开</h1><p class="notice danger" role="alert">${escape(exception.message)}</p><div class="actions"><button class="button" id="retry-page">重新加载</button><a class="button secondary" href="/">返回我的问卷</a></div></section>`;
    $('#retry-page').onclick = render;
  }
}

window.addEventListener('hashchange', render);
$('#dialog').addEventListener('close', () => $('#dialog').replaceChildren());
window.addEventListener('session-expired', () => { beginPage(); $('#dialog').close(); account(); auth(); });
document.addEventListener('click', event => {
  if (event.target.closest('.skip-link')) { event.preventDefault(); $('#main').focus(); return; }
  if (event.target.closest('a') && $('#settings-form')?.dataset.dirty && !confirm('设置尚未保存。确认放弃这些修改？')) event.preventDefault();
});
window.addEventListener('beforeunload', event => { if ($('#settings-form')?.dataset.dirty) { event.preventDefault(); event.returnValue = ''; } });

async function start() {
  if (sessionStorage.getItem('survey_token')) {
    try { setUser(await api('/api/auth/me')); }
    catch (exception) {
      if (exception.code !== 401) {
        $('#main').innerHTML = `<p class="notice danger" role="alert">${escape(exception.message)} 请刷新页面重试。</p>`;
        return;
      }
    }
  }
  await render();
}
start();
