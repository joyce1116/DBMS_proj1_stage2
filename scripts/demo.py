"""Live HTTP demonstration. Start MongoDB and Uvicorn before running this."""

import argparse
import secrets

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=10, trust_env=False) as client:
        def request(label: str, method: str, path: str, expected: int, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code != expected:
                raise RuntimeError(f"{label}: expected {expected}, got {response.status_code}: {response.text}")
            print(f"{label}: HTTP {response.status_code}")
            return response.json() if response.content else None

        request("数据库就绪", "GET", "/health/ready", 200)
        accounts = []
        for role in ("owner", "bob", "carol"):
            credentials = {"username": "demo_" + role + "_" + secrets.token_hex(5), "password": secrets.token_urlsafe(24)}
            request(role + "注册", "POST", "/api/auth/register", 201, json=credentials)
            login = request(role + "登录", "POST", "/api/auth/login", 200, json=credentials)
            accounts.append((credentials["username"], {"Authorization": "Bearer " + login["access_token"]}))
        (_, headers), (_, bob), (_, carol) = accounts
        survey = request("创建草稿", "POST", "/api/surveys", 201, headers=headers,
                         json={"title": "课程体验问卷", "allow_anonymous": True, "allow_repeat": True})
        base = "/api/surveys/" + survey["id"]
        questions = [
            {"type": "single_choice", "title": "年级", "required": True,
             "options": [{"label": "大一"}, {"label": "大二"}]},
            {"type": "multiple_choice", "title": "喜欢的水果", "required": True,
             "options": [{"label": "苹果"}, {"label": "香蕉"}, {"label": "西瓜"}],
             "min_selections": 2, "max_selections": 2},
            {"type": "number", "title": "年龄", "required": True,
             "min_value": 0, "max_value": 120, "integer_only": True},
            {"type": "text", "title": "建议", "min_length": 2, "max_length": 100},
        ]
        for question in questions:
            survey = request("添加" + question["type"], "POST", base + "/questions", 201,
                             headers=headers, params={"expected_revision": survey["revision"]}, json=question)
        first, multi, number, text = survey["questions"]
        rules = [
            {"kind": "choice", "source_id": first["id"], "target_id": number["id"],
             "operator": "eq", "option_ids": [first["options"][1]["id"]]},
            {"kind": "choice", "source_id": multi["id"], "target_id": text["id"],
             "operator": "contains_all", "option_ids": [opt["id"] for opt in multi["options"][:2]]},
            {"kind": "number", "source_id": number["id"], "target_id": "end", "operator": "lt", "value": 18},
        ]
        survey = request("保存三类跳转规则", "PUT", base + "/rules", 200, headers=headers,
                         json={"expected_revision": survey["revision"], "rules": rules})
        survey = request("发布", "POST", base + "/publish", 200, headers=headers,
                         json={"expected_revision": survey["revision"]})
        share = survey["share_url"]
        request("匿名设置仍要求登录", "GET", share, 401)
        shared = request("登录读取分享", "GET", share, 200, headers=headers)
        if shared["title"] != "课程体验问卷":
            raise RuntimeError("Chinese content did not round-trip correctly")
        values = [first["options"][0]["id"], [option["id"] for option in multi["options"][:2]], 20, "建议增加实践"]
        for question, value in zip(shared["questions"], values, strict=True):
            request("校验" + question["type"], "POST", share + "/validate", 200,
                    headers=headers, json={"question_id": question["id"], "value": value})
        request("年龄超范围", "POST", share + "/validate", 422, headers=headers,
                json={"question_id": number["id"], "value": 121})
        answers = [
            {first["id"]: first["options"][0]["id"], multi["id"]: values[1], text["id"]: values[3]},
            {first["id"]: first["options"][1]["id"], number["id"]: 20, text["id"]: "希望增加实验"},
            {first["id"]: first["options"][1]["id"], number["id"]: 16},
        ]
        requests = [{"request_id": secrets.token_hex(16), "survey_revision": survey["revision"],
                     "anonymous": i == 0, "answers": answer} for i, answer in enumerate(answers)]
        for i, (data, respondent) in enumerate(zip(requests, (bob, bob, carol), strict=True)):
            preview = request(f"路径预览{i + 1}", "POST", share + "/navigate", 200, headers=respondent,
                              json={key: data[key] for key in ("survey_revision", "answers")})
            if not preview["completed"] or preview["path"] != list(data["answers"]):
                raise RuntimeError("The navigation path does not match the configured branches")
            request(f"保存答卷{i + 1}", "POST", share + "/responses", 201, headers=respondent, json=data)
        request("同一请求重试", "POST", share + "/responses", 200, headers=bob, json=requests[0])
        changed = {**requests[0], "anonymous": False}
        request("同一请求改内容", "POST", share + "/responses", 409, headers=bob, json=changed)
        request("填写者无权查看统计", "GET", base + "/statistics", 404, headers=bob)
        stats = request("整卷统计", "GET", base + "/statistics", 200, headers=headers)
        if stats["total_responses"] != 3 or stats["questions"][2]["average"] != 18:
            raise RuntimeError("Statistics do not match saved submissions")
        details = request("文本内容统计", "GET", base + "/statistics/" + text["id"], 200, headers=headers)
        if details["contents_total"] != 2:
            raise RuntimeError("Text contents were not saved correctly")
        responses = request("创建者查看答卷", "GET", base + "/responses", 200, headers=headers)
        if any(item["respondent"] is not None for item in responses["items"] if item["anonymous"]):
            raise RuntimeError("An anonymous response disclosed its respondent")
        request("发布后冻结编辑", "PATCH", base, 409, headers=headers,
                json={"expected_revision": survey["revision"], "title": "禁止覆盖"})
        request("关闭", "POST", base + "/close", 200, headers=headers,
                json={"expected_revision": survey["revision"]})
        request("关闭后链接拒绝访问", "GET", share, 410, headers=headers)
        request("关闭后可重取已有回执", "POST", share + "/responses", 200, headers=bob, json=requests[0])
        request("关闭后拒绝新答卷", "POST", share + "/responses", 410, headers=bob,
                json={**requests[0], "request_id": secrets.token_hex(16)})
        for username, auth in accounts:
            request(username + "退出", "POST", "/api/auth/logout", 204, headers=auth)
        print(f"演示完成；保留问卷 {survey['id']}、三个演示账号及三份答卷；数字平均值18，文本2条。")


if __name__ == "__main__":
    main()
