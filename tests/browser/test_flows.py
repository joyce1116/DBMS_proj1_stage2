"""User actions go through real pages. Seed helpers only shorten repeat setups."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from uuid import uuid4

from bson import ObjectId
import httpx
from playwright.sync_api import expect
import pytest


pytestmark = [pytest.mark.browser, pytest.mark.integration]
PASSWORD = "Browser_Password_123"


def register(page, url, name=None, share=False):
    name = name or "browser_" + uuid4().hex[:12]
    page.goto(url)
    page.get_by_role("tab", name="注册账号").click()
    page.get_by_label("用户名", exact=True).fill(name)
    page.get_by_label("密码", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="注册并登录", exact=True).click()
    if not share:
        expect(page.get_by_role("heading", name="我的问卷", exact=True)).to_be_visible()
    return name


def login(page, url, name):
    page.goto(url)
    page.get_by_label("用户名", exact=True).fill(name)
    page.get_by_label("密码", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="登录", exact=True).click()


def add_question(page, kind, title, *, options=None, required=True):
    page.locator(f'[data-add="{kind}"]').click()
    modal = page.get_by_role("dialog")
    modal.get_by_label("题目标题").fill(title)
    modal.get_by_label("此题必须回答").set_checked(required)
    if options:
        while modal.get_by_label("选项文字", exact=True).count() < len(options):
            modal.get_by_role("button", name="添加选项").click()
        for index,label in enumerate(options):
            modal.get_by_label("选项文字", exact=True).nth(index).fill(label)
    if kind == "multiple_choice":
        modal.get_by_label("最少选择数").fill("2")
        modal.get_by_label("最多选择数").fill("2")
    if kind == "number":
        modal.get_by_label("最小值").fill("0")
        modal.get_by_label("最大值").fill("120")
        modal.get_by_label("只允许整数").check()
    if kind == "text":
        modal.get_by_label("最少字数").fill("2")
        modal.get_by_label("最多字数").fill("100")
    modal.get_by_role("button", name="保存", exact=True).click()
    expect(modal).not_to_be_visible()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()


def seed(url, *, repeat=True, title="浏览器问卷", responses=0):
    client = httpx.Client(base_url=url, trust_env=False)
    name = "browser_" + uuid4().hex[:12]
    assert client.post("/api/auth/register", json={"username":name,"password":PASSWORD}).status_code == 201
    token = client.post("/api/auth/login",json={"username":name,"password":PASSWORD}).json()["access_token"]
    client.headers["Authorization"] = "Bearer " + token
    survey = client.post("/api/surveys",json={"title":title,"allow_anonymous":True,"allow_repeat":repeat}).json()
    base = "/api/surveys/" + survey["id"]
    for question in [
        {"type":"single_choice","title":"是否参与","required":True,"options":[{"label":"参与"},{"label":"不参与"}]},
        {"type":"multiple_choice","title":"喜欢的水果","required":True,"options":[{"label":"苹果"},{"label":"香蕉"},{"label":"西瓜"}],"min_selections":2,"max_selections":2},
        {"type":"number","title":"年龄","required":True,"min_value":0,"max_value":120,"integer_only":True},
        {"type":"text","title":"建议","required":False,"min_length":2,"max_length":100},
    ]:
        result = client.post(base + "/questions",params={"expected_revision":survey["revision"]},json=question)
        assert result.status_code == 201, result.text
        survey = result.json()
    q = survey["questions"]
    rules = [
        {"kind":"choice","source_id":q[0]["id"],"target_id":q[2]["id"],"operator":"eq","option_ids":[q[0]["options"][1]["id"]]},
        {"kind":"number","source_id":q[2]["id"],"target_id":"end","operator":"lt","value":18},
    ]
    survey = client.put(base+"/rules",json={"expected_revision":survey["revision"],"rules":rules}).json()
    survey = client.post(base+"/publish",json={"expected_revision":survey["revision"]}).json()
    for i in range(responses):
        result = client.post(survey["share_url"]+"/responses",json={"request_id":uuid4().hex,"survey_revision":survey["revision"],"answers":{q[0]["id"]:q[0]["options"][1]["id"],q[2]["id"]:20,q[3]["id"]:f"建议内容{i:02}"}})
        assert result.status_code == 201, result.text
    client.close()
    return name,survey


def next_step(page):
    page.get_by_role("button",name="下一步 →",exact=True).click()


def short_fill(page, age=17):
    expect(page.get_by_role("heading",name="是否参与",exact=True)).to_be_visible()
    page.get_by_role("radio",name="不参与",exact=True).check()
    next_step(page)
    expect(page.get_by_role("heading",name="年龄",exact=True)).to_be_visible()
    page.get_by_label("你的回答",exact=True).fill(str(age))
    next_step(page)
    if age >= 18:
        expect(page.get_by_role("heading",name="建议",exact=True)).to_be_visible()
        page.get_by_label("你的回答",exact=True).fill("希望增加实验")
        next_step(page)
    expect(page.get_by_role("heading",name="谢谢，每一份回答都很重要。",exact=True)).to_be_visible()


def test_full_ui_creation_three_branch_types_anonymity_statistics_and_closing(page, browser, live_url, database):
    register(page,live_url)
    page.get_by_role("button",name="＋ 创建问卷",exact=True).click()
    modal = page.get_by_role("dialog")
    modal.get_by_label("问卷标题",exact=True).fill("课程体验问卷")
    modal.get_by_label("问卷说明",exact=True).fill("邀请你分享真实的学习感受。")
    modal.get_by_label("允许匿名填写",exact=True).check()
    modal.get_by_label("允许同一账号多次填写",exact=True).check()
    modal.get_by_role("button",name="保存",exact=True).click()
    expect(page.get_by_role("heading",name="课程体验问卷",exact=True)).to_be_visible()
    add_question(page,"single_choice","是否参与",options=["参与","不参与"])
    add_question(page,"multiple_choice","喜欢的水果",options=["苹果","香蕉","西瓜"])
    add_question(page,"number","年龄")
    add_question(page,"text","建议",required=False)
    page.get_by_role("tab",name=re.compile("跳转规则")).click()
    page.get_by_label("判断哪道题").select_option(label="1. 是否参与")
    page.get_by_role("radio",name="不参与",exact=True).check()
    page.get_by_label("满足条件时跳到").select_option(label="3. 年龄")
    page.get_by_role("button",name="保存规则",exact=True).click()
    expect(page.locator(".rule-index")).to_have_count(1)
    page.get_by_label("判断哪道题").select_option(label="2. 喜欢的水果")
    page.get_by_label("判断条件").select_option("contains_all")
    page.get_by_role("checkbox",name="苹果",exact=True).check()
    page.get_by_role("checkbox",name="香蕉",exact=True).check()
    page.get_by_label("满足条件时跳到").select_option(label="4. 建议")
    page.get_by_role("button",name="保存规则",exact=True).click()
    expect(page.locator(".rule-index")).to_have_count(2)
    page.get_by_label("判断哪道题").select_option(label="3. 年龄")
    page.get_by_label("判断条件").select_option("lt")
    page.get_by_label("比较的数字").fill("18")
    page.get_by_label("满足条件时跳到").select_option("end")
    page.get_by_role("button",name="保存规则",exact=True).click()
    expect(page.locator(".rule-index")).to_have_count(3)
    page.get_by_role("button",name="发布问卷",exact=True).click()
    expect(page.get_by_label("分享链接",exact=True)).to_be_visible()
    share_url = page.get_by_label("分享链接",exact=True).input_value()
    artifacts = Path('.runtime/browser'); artifacts.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(artifacts/'editor.png'),full_page=True)
    reader_context = browser.new_context(viewport={"width":1280,"height":900},locale="zh-CN")
    reader = reader_context.new_page()
    reader_errors=[]
    reader.on("pageerror",lambda error:reader_errors.append(str(error)))
    try:
        register(reader,share_url,share=True)
        expect(reader.get_by_role("heading",name="是否参与",exact=True)).to_be_visible()
        reader.get_by_role("radio",name="参与",exact=True).check(); next_step(reader)
        reader.get_by_role("checkbox",name="苹果",exact=True).check(); next_step(reader)
        expect(reader.locator("[data-error]")).to_be_visible()
        reader.get_by_role("checkbox",name="香蕉",exact=True).check(); next_step(reader)
        expect(reader.get_by_role("heading",name="建议",exact=True)).to_be_visible()
        reader.get_by_label("你的回答",exact=True).fill("建议增加实践"); next_step(reader)
        reader.get_by_role("checkbox",name="匿名提交",exact=True).check()
        reader.get_by_role("button",name="确认提交",exact=True).click()
        expect(reader.get_by_role("heading",name="回答已成功提交",exact=True)).to_be_visible()
        reader.reload()
        expect(reader.get_by_role("heading",name="回答已成功提交",exact=True)).to_be_visible()
        assert database.submissions.count_documents({}) == 1
        reader.get_by_role("button",name="再填写一次",exact=True).click(); short_fill(reader,20)
        reader.get_by_role("button",name="确认提交",exact=True).click()
        expect(reader.get_by_role("heading",name="回答已成功提交",exact=True)).to_be_visible()
        reader.get_by_role("button",name="再填写一次",exact=True).click(); short_fill(reader,16)
        reader.get_by_role("button",name="确认提交",exact=True).click()
        expect(reader.get_by_role("heading",name="回答已成功提交",exact=True)).to_be_visible()
        page.get_by_role("link",name="查看统计 →",exact=True).click()
        expect(page.locator(".stat-summary strong").first).to_have_text("3")
        expect(page.locator(".average")).to_contain_text("18")
        page.get_by_label("查看题目").select_option(label="4. 建议")
        expect(page.locator(".content-list li")).to_have_count(2)
        page.get_by_role("tab",name="答卷明细",exact=True).click()
        expect(page.locator(".response-card")).to_have_count(3)
        expect(page.get_by_role("heading",name=re.compile("匿名填写"))).to_have_count(1)
        page.screenshot(path=str(artifacts/'results.png'),full_page=True)
        page.get_by_role("link",name="← 返回问卷",exact=True).click()
        page.get_by_role("button",name="关闭问卷",exact=True).click()
        expect(page.get_by_text("已结束收集",exact=True)).to_be_visible()
        reader.get_by_role("button",name="再填写一次",exact=True).click()
        expect(reader.locator("[data-error]")).to_contain_text("已关闭或截止")
        assert database.submissions.count_documents({}) == 3
        assert not reader_errors
    finally:
        reader_context.close()


def test_backtracking_discards_old_branch_and_required_errors(page,live_url,database):
    name,survey=seed(live_url)
    login(page,live_url+survey["share_url"],name)
    expect(page.get_by_role("heading",name="是否参与",exact=True)).to_be_visible()
    next_step(page)
    expect(page.locator("[data-error]")).to_contain_text("必须回答")
    page.get_by_role("radio",name="参与",exact=True).check(); next_step(page)
    page.get_by_role("checkbox",name="苹果",exact=True).check()
    page.get_by_role("checkbox",name="香蕉",exact=True).check(); next_step(page)
    page.get_by_label("你的回答",exact=True).fill("20"); next_step(page)
    expect(page.get_by_role("heading",name="建议",exact=True)).to_be_visible()
    page.get_by_label("你的回答",exact=True).fill("原分支答案"); next_step(page)
    page.get_by_role("button",name="修改",exact=True).first.click()
    short_fill(page,17)
    expect(page.locator(".answer-review")).to_have_count(2)
    page.get_by_role("button",name="确认提交",exact=True).click()
    expect(page.get_by_role("heading",name="回答已成功提交",exact=True)).to_be_visible()
    stored=database.submissions.find_one()
    assert stored["path"] == [survey["questions"][i]["id"] for i in (0,2)]
    assert len(stored["answers"]) == 2


def test_lost_submission_acknowledgement_retries_after_reload_without_duplicate(page,live_url,database):
    name,survey=seed(live_url,repeat=True)
    login(page,live_url+survey["share_url"],name)
    short_fill(page)
    intercepted=[]
    def lose_acknowledgement(route):
        response=route.fetch()
        intercepted.append(response.status)
        route.abort("failed")
    page.route("**/survey/*/responses",lose_acknowledgement)
    page.get_by_role("button",name="确认提交",exact=True).click()
    expect(page.get_by_role("button",name="重试确认提交",exact=True)).to_be_visible()
    assert intercepted == [201]
    assert database.submissions.count_documents({}) == 1
    expect(page.get_by_role("button",name="返回修改",exact=False)).to_be_disabled()
    page.unroute("**/survey/*/responses")
    page.reload()
    expect(page.get_by_role("heading",name="确认上次的提交结果",exact=True)).to_be_visible()
    page.get_by_role("button",name="确认提交结果",exact=True).click()
    expect(page.get_by_role("heading",name="回答已成功提交",exact=True)).to_be_visible()
    assert database.submissions.count_documents({}) == 1


def test_expired_session_returns_to_login_and_preserves_share_address(page,live_url,database):
    name,survey=seed(live_url)
    url=live_url+survey["share_url"]
    login(page,url,name)
    expect(page.get_by_role("heading",name="是否参与",exact=True)).to_be_visible()
    database.sessions.delete_many({})
    page.get_by_role("radio",name="不参与",exact=True).check(); next_step(page)
    expect(page.get_by_role("heading",name="登录后继续填写",exact=True)).to_be_visible()
    assert page.url == url
    page.get_by_label("用户名",exact=True).fill(name)
    page.get_by_label("密码",exact=True).fill(PASSWORD)
    page.get_by_role("button",name="登录",exact=True).click()
    expect(page.get_by_role("heading",name="是否参与",exact=True)).to_be_visible()


def test_mobile_layout_and_untrusted_title_are_safe(page,live_url):
    title='<img src=x onerror="window.injected=1">问卷'
    name,survey=seed(live_url,title=title)
    page.set_viewport_size({"width":390,"height":844})
    page.goto(live_url)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    login(page,live_url,name)
    expect(page.get_by_role("heading",name="我的问卷",exact=True)).to_be_visible()
    expect(page.locator(".survey-card h3")).to_have_text(title)
    assert page.evaluate("window.injected === undefined")
    page.goto(live_url+survey["share_url"])
    expect(page.get_by_role("heading",name="是否参与",exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("img").count() == 1
    assert page.evaluate("window.injected === undefined")
    Path('.runtime/browser').mkdir(parents=True,exist_ok=True)
    page.screenshot(path='.runtime/browser/mobile-fill.png',full_page=True)


def test_statistics_and_response_pages_show_all_saved_contents(page,live_url):
    name,survey=seed(live_url,responses=22)
    login(page,live_url,name)
    expect(page.get_by_role("heading",name="我的问卷",exact=True)).to_be_visible()
    page.get_by_role("link",name="查看统计 →",exact=True).click()
    page.get_by_label("查看题目").select_option(label="4. 建议")
    expect(page.locator(".content-list li")).to_have_count(20)
    page.get_by_role("button",name="下一页",exact=True).click()
    expect(page.locator(".content-list li")).to_have_count(2)
    page.get_by_role("button",name="上一页",exact=True).click()
    expect(page.locator(".content-list li")).to_have_count(20)
    page.get_by_role("tab",name="答卷明细",exact=True).click()
    expect(page.locator(".response-card")).to_have_count(20)
    page.get_by_role("button",name="下一页",exact=True).click()
    expect(page.locator(".response-card")).to_have_count(2)


def test_question_edits_keep_option_ids_order_and_settings_require_save(page,live_url,database):
    register(page,live_url)
    page.get_by_role("button",name="＋ 创建问卷",exact=True).click()
    modal=page.get_by_role("dialog")
    modal.get_by_label("问卷标题",exact=True).fill("编辑测试")
    modal.get_by_role("button",name="保存",exact=True).click()
    expect(page.get_by_role("heading",name="编辑测试",exact=True)).to_be_visible()
    add_question(page,"single_choice","选择题",options=["甲","乙"])
    add_question(page,"number","分数")
    original=database.surveys.find_one()
    option_ids=[o["id"] for o in original["questions"][0]["options"]]
    page.locator('[data-edit="0"]').click()
    modal.get_by_label("题目标题").fill("新的选择题")
    modal.get_by_label("选项文字",exact=True).first.fill("第一项")
    modal.get_by_role("button",name="保存",exact=True).click()
    expect(page.get_by_role("heading",name="新的选择题",exact=True)).to_be_visible()
    assert [o["id"] for o in database.surveys.find_one()["questions"][0]["options"]] == option_ids
    page.get_by_role("button",name="上移第2题",exact=True).click()
    expect(page.locator(".question-card h3").first).to_contain_text("分数")
    page.get_by_role("button",name="删除第1题",exact=True).click()
    expect(page.locator(".question-card")).to_have_count(1)
    page.get_by_role("tab",name="问卷设置",exact=True).click()
    page.get_by_label("问卷标题",exact=True).fill("修改后的问卷")
    local_deadline=(datetime.now()+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S.123')
    page.get_by_label("截止时间",exact=True).fill(local_deadline)
    page.get_by_role("button",name="发布问卷",exact=True).click()
    expect(page.locator("#main [data-error]")).to_contain_text("请先保存")
    assert database.surveys.find_one()["title"] == "编辑测试"
    page.get_by_role("button",name="保存设置",exact=True).click()
    expect(page.get_by_role("heading",name="修改后的问卷",exact=True)).to_be_visible()
    expected=datetime.fromisoformat(local_deadline).replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    assert database.surveys.find_one()["deadline"] == expected
    page.get_by_role("button",name="发布问卷",exact=True).click()
    expect(page.get_by_label("分享链接",exact=True)).to_be_visible()
    expect(page.get_by_label("问卷标题",exact=True)).to_be_disabled()


def test_dashboard_pagination_registration_errors_and_logout(page,live_url):
    name,survey=seed(live_url)
    with httpx.Client(base_url=live_url,trust_env=False) as client:
        token=client.post('/api/auth/login',json={"username":name,"password":PASSWORD}).json()["access_token"]
        for i in range(12):
            assert client.post('/api/surveys',headers={"Authorization":"Bearer "+token},json={"title":f"分页问卷{i}"}).status_code == 201
    page.goto(live_url)
    page.get_by_role("tab",name="注册账号",exact=True).click()
    page.get_by_label("用户名",exact=True).fill(name)
    page.get_by_label("密码",exact=True).fill(PASSWORD)
    page.get_by_role("button",name="注册并登录",exact=True).click()
    expect(page.locator("[data-error]")).to_contain_text("已被注册")
    page.get_by_role("tab",name="登录账号",exact=True).click()
    page.get_by_label("用户名",exact=True).fill(name)
    page.get_by_label("密码",exact=True).fill("Wrong_password")
    page.get_by_role("button",name="登录",exact=True).click()
    expect(page.locator("[data-error]")).to_contain_text("密码不正确")
    page.get_by_label("密码",exact=True).fill(PASSWORD)
    page.get_by_role("button",name="登录",exact=True).click()
    expect(page.locator(".survey-card")).to_have_count(12)
    page.get_by_role("button",name="下一页",exact=True).click()
    expect(page.locator(".survey-card")).to_have_count(1)
    page.get_by_role("button",name="上一页",exact=True).click()
    expect(page.locator(".survey-card")).to_have_count(12)
    page.get_by_role("button",name="退出",exact=True).click()
    expect(page.get_by_role("heading",name="欢迎来到工作台",exact=True)).to_be_visible()
    assert page.evaluate("sessionStorage.getItem('survey_token')") is None


def test_late_statistics_response_cannot_replace_login_after_logout(page,live_url):
    name,survey=seed(live_url)
    login(page,live_url,name)
    expect(page.get_by_role("heading",name="我的问卷",exact=True)).to_be_visible()
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]).endsWith('/statistics')) {
          return new Promise(resolve => {
            window.releaseStats = () => resolve(response);
            document.body.dataset.statsHeld = 'true';
          });
        }
        return response;
      };
    }""")
    page.get_by_role("link",name="查看统计 →",exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-stats-held","true")
    page.get_by_role("button",name="退出",exact=True).click()
    expect(page.get_by_role("heading",name="欢迎来到工作台",exact=True)).to_be_visible()
    page.evaluate("async () => { window.releaseStats(); await new Promise(resolve => setTimeout(resolve,100)); }")
    expect(page.get_by_role("heading",name="欢迎来到工作台",exact=True)).to_be_visible()
    expect(page.locator(".stat-summary")).to_have_count(0)
