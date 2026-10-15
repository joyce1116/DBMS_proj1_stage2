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
        credentials = {"username": "demo_" + secrets.token_hex(5), "password": secrets.token_urlsafe(24)}
        request("注册", "POST", "/api/auth/register", 201, json=credentials)
        login = request("登录", "POST", "/api/auth/login", 200, json=credentials)
        headers = {"Authorization": "Bearer " + login["access_token"]}
        survey = request("创建草稿", "POST", "/api/surveys", 201, headers=headers,
                         json={"title": "课程体验问卷", "allow_anonymous": True})
        base = "/api/surveys/" + survey["id"]
        questions = [
            {"type": "single_choice", "title": "年级", "required": True,
             "options": [{"label": "大一"}, {"label": "大二"}]},
            {"type": "multiple_choice", "title": "喜欢的水果", "required": True,
             "options": [{"label": "苹果"}, {"label": "香蕉"}, {"label": "西瓜"}],
             "min_selections": 2, "max_selections": 2},
            {"type": "text", "title": "建议", "min_length": 2, "max_length": 100},
            {"type": "number", "title": "年龄", "required": True,
             "min_value": 0, "max_value": 120, "integer_only": True},
        ]
        for question in questions:
            survey = request("添加" + question["type"], "POST", base + "/questions", 201,
                             headers=headers, params={"expected_revision": survey["revision"]}, json=question)
        survey = request("发布", "POST", base + "/publish", 200, headers=headers,
                         json={"expected_revision": survey["revision"]})
        share = survey["share_url"]
        request("匿名设置仍要求登录", "GET", share, 401)
        shared = request("登录读取分享", "GET", share, 200, headers=headers)
        if shared["title"] != "课程体验问卷":
            raise RuntimeError("Chinese content did not round-trip correctly")
        first, multi, text, number = shared["questions"]
        values = [first["options"][0]["id"], [option["id"] for option in multi["options"][:2]], "建议增加实践", 20]
        for question, value in zip(shared["questions"], values, strict=True):
            request("校验" + question["type"], "POST", share + "/validate", 200,
                    headers=headers, json={"question_id": question["id"], "value": value})
        request("年龄超范围", "POST", share + "/validate", 422, headers=headers,
                json={"question_id": number["id"], "value": 121})
        request("发布后冻结编辑", "PATCH", base, 409, headers=headers,
                json={"expected_revision": survey["revision"], "title": "禁止覆盖"})
        request("关闭", "POST", base + "/close", 200, headers=headers,
                json={"expected_revision": survey["revision"]})
        request("关闭后链接拒绝访问", "GET", share, 410, headers=headers)
        request("退出", "POST", "/api/auth/logout", 204, headers=headers)
        print(f"演示完成；保留账号 {credentials['username']} 和问卷 {survey['id']}，未提交答案。")


if __name__ == "__main__":
    main()
