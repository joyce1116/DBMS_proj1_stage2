"""Stage-two workflows reuse the original UI against fresh, real databases.

Fixtures seed larger answer sets through public APIs; each workflow's changed
interaction is driven in Edge. Screenshots contain only disposable test data.
"""

from contextlib import contextmanager
from pathlib import Path
import re
from uuid import uuid4

from bson import ObjectId
import httpx
from playwright.sync_api import expect
import pytest


pytestmark = [pytest.mark.browser, pytest.mark.integration]
PASSWORD = "Browser_Password_123"
SCREENSHOTS = Path("figures/stage2")


def request(client, method, path, *, code=200, **kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code == code, response.text
    return response.json() if response.content else None


@contextmanager
def account(url):
    """Every client and database belongs to this test, including its users."""
    with httpx.Client(base_url=url, trust_env=False) as client:
        name = "stage2_" + uuid4().hex[:12]
        request(client, "POST", "/api/auth/register", code=201,
                json={"username": name, "password": PASSWORD})
        token = request(client, "POST", "/api/auth/login",
                        json={"username": name, "password": PASSWORD})["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        yield client, name


def login(page, url, name, route=""):
    page.goto(url + "/" + route)
    page.get_by_label("用户名", exact=True).fill(name)
    page.get_by_label("密码", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="登录", exact=True).click()


def logout(page):
    page.get_by_role("button", name="退出", exact=True).click()
    expect(page.get_by_role("button", name="登录", exact=True)).to_be_visible()


def visit(page, url, route):
    page.goto(url + "/#/" + route)


def screenshot(page, name):
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOTS / name), full_page=True)


def version(client, title, kind="number", **kwargs):
    return request(client, "POST", "/api/questions", code=201,
                   json={"type": kind, "title": title, "required": False, **kwargs})


def survey(client, title, versions, *, publish=False):
    saved = request(client, "POST", "/api/surveys", code=201,
                    json={"title": title, "allow_anonymous": True, "allow_repeat": True})
    for selected in versions:
        saved = request(client, "POST", f"/api/surveys/{saved['id']}/question-selections",
                        code=201, params={"expected_revision": saved["revision"]},
                        json={"version_id": selected["id"]})
    if publish:
        saved = request(client, "POST", f"/api/surveys/{saved['id']}/publish",
                        json={"expected_revision": saved["revision"]})
    return saved


def submit(client, saved, values):
    request(client, "POST", saved["share_url"] + "/responses", code=201,
            json={"request_id": uuid4().hex, "survey_revision": saved["revision"],
                  "answers": {question["id"]: value
                              for question, value in zip(saved["questions"], values)}})


def pick(page, title, *, tab=None):
    modal = page.get_by_role("dialog")
    if tab:
        modal.get_by_role("tab", name=tab, exact=True).click()
    modal.locator(".selection-option").filter(has=page.get_by_text(title, exact=True)).get_by_role("radio").check()
    modal.get_by_role("button", name="选用此版本", exact=True).click()
    expect(modal).not_to_be_visible()


def test_question_creation_revision_history_restore_and_stable_options(page, live_url, database):
    with account(live_url) as (owner, name):
        login(page, live_url, name, "#/questions")
        page.locator('[data-create="single_choice"]').click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("题目标题", exact=True).fill("实验课满意度")
        modal.get_by_label("选项文字", exact=True).nth(0).fill("满意")
        modal.get_by_label("选项文字", exact=True).nth(1).fill("需要改进")
        modal.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("heading", name="实验课满意度", exact=True, level=1)).to_be_visible()
        original = request(owner, "GET", "/api/questions")["items"][0]
        page.get_by_role("button", name="创建新版本", exact=True).click()
        modal.get_by_label("题目标题", exact=True).fill("实验课满意度（修订）")
        modal.get_by_label("选项文字", exact=True).nth(0).fill("非常满意")
        modal.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("heading", name="实验课满意度（修订）", exact=True, level=1)).to_be_visible()
        revised = request(owner, "GET", "/api/questions")["items"][0]
        assert revised["version"] == 2 and revised["parent_id"] == original["id"]
        assert revised["content"]["options"][0]["id"] == original["content"]["options"][0]["id"]
        assert request(owner, "GET", "/api/questions/" + original["id"]) == original
        page.get_by_role("tab", name="版本历史", exact=True).click()
        expect(page.locator("[data-history-version]")).to_have_count(2)
        visit(page, live_url, "questions/" + original["id"])
        with page.expect_response("**/api/questions/" + original["id"] + "/restore") as restored_response:
            page.get_by_role("button", name="从此版本恢复", exact=True).click()
        restored_id = restored_response.value.json()["id"]
        expect(page).to_have_url(re.compile(re.escape(restored_id) + "$"))
        expect(page.get_by_role("heading", name="实验课满意度", exact=True, level=1)).to_be_visible()
        page.get_by_role("tab", name="版本历史", exact=True).click()
        expect(page.locator("[data-history-version]")).to_have_count(3)
        restored = request(owner, "GET", "/api/questions")["items"][0]
        assert restored["version"] == 3 and restored["operation"] == "restore"
        assert restored["parent_id"] == original["id"] and restored["content"] == original["content"]
        assert database.question_versions.count_documents({}) == 3
        screenshot(page, "01-question-version-history.png")


def test_direct_grant_shared_picker_revocation_and_snapshot_privacy(page, live_url, database):
    with account(live_url) as (owner, owner_name), account(live_url) as (reader, reader_name):
        item = version(owner, "共享的课程建议", "text")
        own_draft = survey(owner, "作者自己的问卷", [item])
        private_revision = request(owner, "POST", f"/api/questions/{item['id']}/versions", code=201,
                                   json={**item["content"], "title": "未授权的新版课程建议"})
        survey(owner, "作者新版问卷", [private_revision])
        login(page, live_url, owner_name, "#/questions/" + item["id"])
        page.get_by_role("tab", name="授权管理", exact=True).click()
        page.get_by_label("授权用户名", exact=True).fill(reader_name)
        page.get_by_role("button", name="添加授权", exact=True).click()
        expect(page.get_by_role("button", name="撤销授权", exact=True)).to_be_visible()
        reader_draft = survey(reader, "收件人秘密问卷标题", [])
        logout(page)
        login(page, live_url, reader_name, "#/questions/" + item["id"])
        expect(page.get_by_role("heading", name="共享的课程建议", exact=True, level=1)).to_be_visible()
        expect(page.get_by_role("button", name="创建新版本", exact=True)).to_have_count(0)
        expect(page.get_by_role("tab", name="版本历史", exact=True)).to_have_count(0)
        assert reader.get("/api/questions/" + private_revision["id"]).status_code == 404
        visit(page, live_url, "surveys/" + reader_draft["id"] + "/edit")
        page.get_by_role("button", name="从题库选题", exact=True).click()
        pick(page, item["content"]["title"], tab="共享给我的题目")
        expect(page.get_by_text("题库来源 · v1", exact=False)).to_be_visible()
        assert request(reader, "GET", "/api/surveys/" + reader_draft["id"])["question_sources"][0]["version_id"] == item["id"]
        screenshot(page, "02-shared-question-survey-snapshot.png")
        page.get_by_role("button", name="发布问卷", exact=True).click()
        expect(page.get_by_label("分享链接", exact=True)).to_be_visible()
        published = request(reader, "GET", "/api/surveys/" + reader_draft["id"])
        logout(page)
        login(page, live_url, owner_name, "#/questions/" + item["id"])
        page.get_by_role("tab", name="使用位置", exact=True).click()
        expect(page.locator("[data-usage-totals]")).to_have_text("2 份问卷 · 2 处使用")
        expect(page.get_by_text("他人的问卷", exact=True)).to_be_visible()
        assert "收件人秘密问卷标题" not in page.locator("#main").inner_text()
        assert page.locator(f'a[href*="{reader_draft["id"]}"]').count() == 0
        page.get_by_label("使用范围", exact=True).select_option("family")
        expect(page.locator("[data-usage-totals]")).to_have_text("3 份问卷 · 3 处使用")
        page.get_by_role("tab", name="授权管理", exact=True).click()
        page.get_by_role("button", name="撤销授权", exact=True).click()
        expect(page.get_by_role("button", name="撤销授权", exact=True)).to_have_count(0)
        assert reader.get("/api/questions/" + item["id"]).status_code == 404
        logout(page)
        login(page, live_url, reader_name, "#/questions")
        page.get_by_role("tab", name="共享给我", exact=True).click()
        expect(page.locator("[data-question-card]")).to_have_count(0)
        page.goto(live_url + published["share_url"])
        expect(page.get_by_role("heading", name="共享的课程建议", exact=True)).to_be_visible()
        page.get_by_label("你的回答", exact=True).fill("撤销共享后仍保留既有问卷快照")
        page.get_by_role("button", name="下一步 →", exact=True).click()
        page.get_by_role("button", name="确认提交", exact=True).click()
        expect(page.get_by_role("heading", name="回答已成功提交", exact=True)).to_be_visible()
        assert database.submissions.count_documents({}) == 1
        assert request(owner, "GET", "/api/surveys/" + own_draft["id"])["status"] == "draft"
        visit(page, live_url, "questions/" + item["id"] + "/statistics")
        expect(page.locator('[data-metric="answers"]')).to_have_text("1")
        page.get_by_role("button", name="查看各版本汇总", exact=True).click()
        expect(page.locator("#cross-family")).to_contain_text("只有题目原作者")
        expect(page.locator("#cross-contents")).to_contain_text("撤销共享后仍保留既有问卷快照")


def test_bank_metadata_membership_version_grants_and_archival(page, live_url, database):
    with account(live_url) as (owner, owner_name), account(live_url) as (reader, reader_name):
        first = version(owner, "学习时长（原版）")
        second = request(owner, "POST", f"/api/questions/{first['id']}/versions", code=201,
                         json={**first["content"], "title": "学习时长（新版）"})
        login(page, live_url, owner_name, "#/banks")
        page.get_by_role("button", name="新建题库", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("题库标题", exact=True).fill("课程公共题库")
        modal.get_by_label("题库说明", exact=True).fill("保留版本与共享边界")
        modal.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("heading", name="课程公共题库", exact=True)).to_be_visible()
        bank = request(owner, "GET", "/api/question-banks")["items"][0]
        page.get_by_role("button", name="编辑题库", exact=True).click()
        modal.get_by_label("题库说明", exact=True).fill("已修改的题库说明")
        modal.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_text("已修改的题库说明", exact=True)).to_be_visible()
        page.get_by_role("button", name="添加题目", exact=True).click()
        pick(page, "学习时长（原版）")
        entry = page.locator("[data-bank-entry]")
        expect(entry).to_have_count(1)
        page.get_by_role("button", name="更新版本", exact=True).click()
        pick(page, "学习时长（新版）")
        expect(entry).to_contain_text("学习时长（新版）")
        stored = request(owner, "GET", f"/api/question-banks/{bank['id']}/entries")["items"][0]
        assert stored["version_id"] == second["id"] and stored["family_id"] == first["family_id"]
        page.get_by_label("授权用户名", exact=True).fill(reader_name)
        page.get_by_role("button", name="添加授权", exact=True).click()
        expect(page.get_by_role("button", name="撤销授权", exact=True)).to_be_visible()
        screenshot(page, "03-bank-version-and-grants.png")
        logout(page)
        login(page, live_url, reader_name, "#/banks")
        page.get_by_role("tab", name="共享题库", exact=True).click()
        page.get_by_role("link", name="打开题库", exact=True).click()
        expect(page.locator("[data-bank-entry]")).to_contain_text("学习时长（新版）")
        expect(page.get_by_role("button", name="编辑题库", exact=True)).to_have_count(0)
        assert reader.get("/api/questions/" + first["id"]).status_code == 404
        assert reader.get("/api/questions/" + second["id"]).status_code == 200
        reader_draft = survey(reader, "通过共享题库选题", [])
        visit(page, live_url, "surveys/" + reader_draft["id"] + "/edit")
        page.get_by_role("button", name="从题库选题", exact=True).click()
        modal.get_by_role("tab", name="共享题库", exact=True).click()
        modal.locator("[data-pick-bank]").filter(has_text="课程公共题库").click()
        pick(page, "学习时长（新版）")
        expect(page.get_by_text("题库来源 · v2", exact=False)).to_be_visible()
        logout(page)
        login(page, live_url, owner_name, "#/banks/" + bank["id"])
        page.get_by_role("button", name="移出题库", exact=True).click()
        expect(page.locator("[data-bank-entry]")).to_have_count(0)
        assert reader.get("/api/questions/" + second["id"]).status_code == 404
        assert request(reader, "GET", "/api/surveys/" + reader_draft["id"])["question_sources"][0]["version_id"] == second["id"]
        page.get_by_role("button", name="归档题库", exact=True).click()
        expect(page.get_by_role("heading", name="题库", exact=True)).to_be_visible()
        assert owner.get("/api/question-banks/" + bank["id"]).status_code == 404
        assert database.question_banks.find_one({"_id": ObjectId(bank["id"])})["archived"] is True


def test_editor_explicit_source_replacement_revision_and_local_detachment(page, live_url, database):
    with account(live_url) as (owner, name):
        first = version(owner, "原始课外时长")
        second = request(owner, "POST", f"/api/questions/{first['id']}/versions", code=201,
                         json={**first["content"], "title": "修订课外时长"})
        draft = survey(owner, "版本选择与本卷副本", [first])
        original_qid = draft["questions"][0]["id"]
        login(page, live_url, name, "#/surveys/" + draft["id"] + "/edit")
        page.get_by_role("button", name="替换来源", exact=True).click()
        pick(page, "修订课外时长")
        expect(page.get_by_role("heading", name="修订课外时长", exact=True)).to_be_visible()
        changed = request(owner, "GET", "/api/surveys/" + draft["id"])
        assert changed["questions"][0]["id"] == original_qid
        assert changed["question_sources"][0]["version_id"] == second["id"]
        page.get_by_role("button", name="修改方式", exact=True).click()
        page.get_by_role("button", name="创建题目新版本", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("题目标题", exact=True).fill("第三版课外时长")
        modal.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("heading", name="第三版课外时长", exact=True)).to_be_visible()
        changed = request(owner, "GET", "/api/surveys/" + draft["id"])
        assert changed["question_sources"][0]["version"] == 3
        page.get_by_role("button", name="修改方式", exact=True).click()
        page.get_by_role("button", name="只修改本卷副本", exact=True).click()
        modal.get_by_label("题目标题", exact=True).fill("本卷自定义时长")
        modal.get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("heading", name="本卷自定义时长", exact=True)).to_be_visible()
        changed = request(owner, "GET", "/api/surveys/" + draft["id"])
        assert changed["questions"][0]["id"] == original_qid and changed["question_sources"] == []
        assert database.question_versions.count_documents({}) == 3
        assert request(owner, "GET", "/api/questions/" + first["id"]) == first
        assert request(owner, "GET", "/api/questions/" + second["id"]) == second


def test_numeric_statistics_family_pagination_and_foreign_answer_privacy(page, live_url):
    with account(live_url) as (owner, name), account(live_url) as (other, other_name):
        first = version(owner, "学习投入小时数")
        second = request(owner, "POST", f"/api/questions/{first['id']}/versions", code=201,
                         json={**first["content"], "title": "学习投入小时数（新口径）"})
        repeated = survey(owner, "上午与下午的课程", [first, first], publish=True)
        for value in range(22):
            submit(owner, repeated, [value, value + 10])
        another = survey(owner, "另一门课程", [first], publish=True)
        submit(owner, another, [40]); submit(owner, another, [50])
        new_survey = survey(owner, "新口径课程", [second], publish=True)
        for value in [100, 200, 300]:
            submit(owner, new_survey, [value])
        request(owner, "POST", f"/api/questions/{first['id']}/grants", code=201,
                json={"username": other_name})
        foreign = survey(other, "不得显示的外部问卷", [first], publish=True)
        submit(other, foreign, [999])
        exact = request(owner, "GET", f"/api/questions/{first['id']}/statistics")
        assert (exact["survey_count"], exact["question_occurrence_count"], exact["total_responses"],
                exact["visited_count"], exact["answer_count"]) == (2, 3, 24, 46, 46)
        assert exact["average"] == pytest.approx(772 / 46) and exact["max_value"] == 50
        login(page, live_url, name, "#/questions/" + first["id"] + "/statistics")
        expect(page.get_by_role("heading", name="跨问卷统计", exact=True)).to_be_visible()
        for key, value in {"surveys": "2", "occurrences": "3", "responses": "24", "visits": "46", "answers": "46"}.items():
            expect(page.locator(f'[data-metric="{key}"]')).to_have_text(value)
        expect(page.locator("#cross-distribution tbody tr")).to_have_count(20)
        expect(page.locator("#cross-distribution [data-next]")).to_be_enabled()
        page.locator("#cross-distribution [data-next]").click()
        expect(page.locator("#cross-distribution [data-previous]")).to_be_enabled()
        expect(page.locator("#cross-distribution")).to_contain_text("50")
        expect(page.locator("#cross-distribution tbody tr")).to_have_count(14)
        expect(page.locator("#cross-contents .content-list li")).to_have_count(20)
        expect(page.locator("#cross-contents [data-next]")).to_be_enabled()
        page.locator("#cross-contents [data-next]").click()
        page.locator("#cross-contents [data-next]").click()
        expect(page.locator("#cross-contents [data-next]")).to_be_disabled()
        expect(page.locator("#cross-contents")).to_contain_text("41–46")
        expect(page.locator("#cross-contents .content-list li")).to_have_count(6)
        page.get_by_role("button", name="查看各版本汇总", exact=True).click()
        expect(page.locator("#cross-family")).to_contain_text("学习投入小时数（新口径）")
        expect(page.locator("#cross-family")).to_contain_text("200")
        assert "999" not in page.locator("#main").inner_text()
        assert "不得显示的外部问卷" not in page.locator("#main").inner_text()
        screenshot(page, "04-cross-survey-numeric-and-family.png")


def test_text_statistics_paginate_and_escape_untrusted_answers(page, live_url):
    dangerous = '<img src=x onerror="window.stage2Injected=1">课程建议'
    with account(live_url) as (owner, name), account(live_url) as (other, other_name):
        item = version(owner, "跨课程文字建议", "text")
        own = survey(owner, "本人问卷", [item], publish=True)
        submit(owner, own, [dangerous])
        for index in range(24):
            submit(owner, own, [f"本人建议内容{index:02}"])
        request(owner, "POST", f"/api/questions/{item['id']}/grants", code=201,
                json={"username": other_name})
        foreign = survey(other, "其他人的问卷", [item], publish=True)
        submit(other, foreign, ["禁止泄露的别人的回答"])
        login(page, live_url, name, "#/questions/" + item["id"] + "/statistics")
        expect(page.locator("#cross-contents")).to_contain_text("本人建议内容23")
        expect(page.locator("#cross-contents [data-next]")).to_be_enabled()
        page.locator("#cross-contents [data-next]").click()
        expect(page.locator("#cross-contents")).to_contain_text("21–25")
        expect(page.get_by_text(dangerous, exact=True)).to_be_visible()
        assert page.evaluate("window.stage2Injected === undefined")
        assert "禁止泄露的别人的回答" not in page.locator("#main").inner_text()
        assert page.locator("#cross-contents img").count() == 0
        screenshot(page, "05-cross-survey-text-content.png")


def test_mobile_library_pagination_untrusted_titles_and_bank_layout(page, live_url):
    dangerous = '<img src=x onerror="window.stage2Injected=1">危险标题'
    with account(live_url) as (owner, name):
        for index in range(12):
            version(owner, f"用于分页的题目{index:02}", "text")
        version(owner, dangerous, "text")
        bank = request(owner, "POST", "/api/question-banks", code=201,
                       json={"title": dangerous, "description": "手机上的题库也应完整可用。"})
        page.set_viewport_size({"width": 390, "height": 844})
        login(page, live_url, name, "#/questions")
        expect(page.locator("[data-question-card]")).to_have_count(12)
        expect(page.get_by_role("heading", name=dangerous, exact=True)).to_be_visible()
        assert page.evaluate("window.stage2Injected === undefined")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("""() => [...document.querySelectorAll('body *')]
          .filter(element => element.getBoundingClientRect().right > innerWidth + 1)
          .map(element => ({tag:element.tagName,class:element.className,text:element.textContent.slice(0,80),right:element.getBoundingClientRect().right}))""")
        page.get_by_role("button", name="下一页", exact=True).click()
        expect(page.locator("[data-question-card]")).to_have_count(1)
        visit(page, live_url, "banks/" + bank["id"])
        expect(page.get_by_role("heading", name=dangerous, exact=True)).to_be_visible()
        assert page.evaluate("window.stage2Injected === undefined")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot(page, "06-mobile-bank-and-navigation.png")


def test_late_cross_statistics_response_cannot_replace_logged_out_page(page, live_url):
    with account(live_url) as (owner, name):
        item = version(owner, "迟到响应统计")
        login(page, live_url, name)
        expect(page.get_by_role("heading", name="我的问卷", exact=True)).to_be_visible()
        page.evaluate("""() => {
          const original = window.fetch;
          window.fetch = async (...args) => {
            const response = await original(...args);
            if (/\\/api\\/questions\\/[^/]+\\/statistics(?:\\?|$)/.test(String(args[0]))) {
              return new Promise(resolve => {
                window.releaseStage2Stats = () => resolve(response);
                document.body.dataset.stage2StatsHeld = 'true';
              });
            }
            return response;
          };
        }""")
        page.evaluate("id => { location.hash = '#/questions/' + id + '/statistics'; }", item["id"])
        expect(page.locator("body")).to_have_attribute("data-stage2-stats-held", "true")
        logout(page)
        page.evaluate("async () => { window.releaseStage2Stats(); await new Promise(resolve => setTimeout(resolve,100)); }")
        expect(page.get_by_role("heading", name="欢迎来到工作台", exact=True)).to_be_visible()
        expect(page.locator("#cross-summary")).to_have_count(0)


@pytest.mark.parametrize("old_response", ["success", "error"])
def test_cancelled_picker_response_cannot_overwrite_reopened_picker(page, live_url, old_response):
    with account(live_url) as (owner, name):
        version(owner, "旧请求中的题目", "text")
        draft = survey(owner, "重新打开选题窗口", [])
        login(page, live_url, name, "#/surveys/" + draft["id"] + "/edit")
        expect(page.get_by_role("button", name="从题库选题", exact=True)).to_be_visible()
        page.evaluate("""mode => {
          const original = window.fetch;
          let intercepted = false;
          window.fetch = async (...args) => {
            const response = await original(...args);
            if (!intercepted && String(args[0]).startsWith('/api/questions?')) {
              intercepted = true;
              return new Promise((resolve,reject) => {
                window.releaseOldPicker = () => mode === 'error'
                  ? reject(new Error('controlled stale request failure')) : resolve(response);
                document.body.dataset.oldPickerHeld = 'true';
              });
            }
            return response;
          };
        }""", old_response)
        page.get_by_role("button", name="从题库选题", exact=True).click()
        expect(page.locator("body")).to_have_attribute("data-old-picker-held", "true")
        modal = page.get_by_role("dialog")
        modal.get_by_role("button", name="取消", exact=True).click()
        latest = version(owner, "新窗口应保留的题目", "text")
        page.get_by_role("button", name="从题库选题", exact=True).click()
        expect(modal.get_by_text("新窗口应保留的题目", exact=True)).to_be_visible()
        page.evaluate("async () => { window.releaseOldPicker(); await new Promise(resolve => setTimeout(resolve,100)); }")
        expect(modal.get_by_text("新窗口应保留的题目", exact=True)).to_be_visible()
        expect(modal.locator("[data-error]")).to_be_hidden()
        pick(page, "新窗口应保留的题目")
        saved = request(owner, "GET", "/api/surveys/" + draft["id"])
        assert saved["question_sources"][0]["version_id"] == latest["id"]


def test_derived_version_partial_success_has_recoverable_snapshot_conflict(page, live_url, database):
    with account(live_url) as (owner, name):
        original = version(owner, "并发操作前的题目")
        draft = survey(owner, "本卷换版冲突演示", [original])
        login(page, live_url, name, "#/surveys/" + draft["id"] + "/edit")
        page.get_by_role("button", name="修改方式", exact=True).click()
        page.get_by_role("button", name="创建题目新版本", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("题目标题", exact=True).fill("已保存但尚未换入的新版")
        # A real concurrent writer makes only the second of the two writes fail.
        request(owner, "PATCH", "/api/surveys/" + draft["id"],
                json={"expected_revision": draft["revision"], "title": "其他页面已修改问卷"})
        modal.get_by_role("button", name="保存", exact=True).click()
        expect(modal.locator("[data-error]")).to_contain_text("新版本 v2 已保存在“我的题目”")
        expect(modal.locator("[data-error]")).to_contain_text("本卷换入失败")
        expect(modal.get_by_role("button", name="保存", exact=True)).to_have_count(0)
        for element in modal.locator("input,textarea,select").all():
            expect(element).to_be_disabled()
        assert database.question_versions.count_documents({}) == 2
        still_old = request(owner, "GET", "/api/surveys/" + draft["id"])
        assert still_old["question_sources"][0]["version_id"] == original["id"]
        screenshot(page, "07-version-created-survey-conflict.png")
        modal.get_by_role("button", name="取消", exact=True).click()
        page.reload()
        expect(page.get_by_role("heading", name="其他页面已修改问卷", exact=True)).to_be_visible()
        page.get_by_role("button", name="替换来源", exact=True).click()
        pick(page, "已保存但尚未换入的新版")
        expect(page.get_by_role("heading", name="已保存但尚未换入的新版", exact=True)).to_be_visible()
        recovered = request(owner, "GET", "/api/surveys/" + draft["id"])
        assert recovered["questions"][0]["id"] == draft["questions"][0]["id"]
        assert recovered["question_sources"][0]["version"] == 2
        assert database.question_versions.count_documents({}) == 2


def test_old_session_unauthorized_response_does_not_expire_new_login(page, live_url, database):
    with account(live_url) as (owner, name):
        version(owner, "新会话中的个人题目", "text")
        login(page, live_url, name)
        expect(page.get_by_role("heading", name="我的问卷", exact=True)).to_be_visible()
        old_token = page.evaluate("sessionStorage.getItem('survey_token')")
        database.sessions.delete_many({})
        page.evaluate("""() => {
          const original = window.fetch;
          let intercepted = false;
          window.fetch = async (...args) => {
            const response = await original(...args);
            if (!intercepted && String(args[0]).startsWith('/api/questions?')) {
              intercepted = true;
              return new Promise(resolve => {
                window.releaseOldUnauthorized = () => resolve(response);
                document.body.dataset.oldUnauthorizedHeld = 'true';
              });
            }
            return response;
          };
          location.hash = '#/questions';
        }""")
        expect(page.locator("body")).to_have_attribute("data-old-unauthorized-held", "true")
        logout(page)
        page.get_by_label("用户名", exact=True).fill(name)
        page.get_by_label("密码", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.locator("[data-question-card]")).to_have_count(1)
        new_token = page.evaluate("sessionStorage.getItem('survey_token')")
        assert new_token and new_token != old_token
        page.evaluate("async () => { window.releaseOldUnauthorized(); await new Promise(resolve => setTimeout(resolve,100)); }")
        expect(page.locator("[data-question-card]")).to_have_count(1)
        expect(page.get_by_role("button", name="退出", exact=True)).to_be_visible()
        assert page.evaluate("sessionStorage.getItem('survey_token')") == new_token
