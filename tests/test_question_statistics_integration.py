"""Cross-survey reporting over saved phase-one submissions in real MongoDB."""

from copy import deepcopy
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import OperationFailure
import pytest

from app.responses.repository import ResponseRepository


pytestmark = pytest.mark.integration
QUESTIONS = "/api/questions"


def actor(api, username):
    credentials = {"username": username, "password": "Statistics_Password_123"}
    registered = api.post("/api/auth/register", json=credentials)
    assert registered.status_code == 201, registered.text
    logged_in = api.post("/api/auth/login", json=credentials)
    assert logged_in.status_code == 200, logged_in.text
    return registered.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def people(api):
    return actor(api, "Statistics_Owner"), actor(api, "Statistics_Reader"), actor(api, "Statistics_Outsider")


def version(api, headers, content=None, parent=None):
    path = QUESTIONS if parent is None else f"{QUESTIONS}/{parent['id']}/versions"
    result = api.post(path, headers=headers, json=content or {"type": "text", "title": "Reusable text"})
    assert result.status_code == 201, result.text
    return result.json()


def survey(api, headers, title="Cross-survey reporting", **settings):
    result = api.post("/api/surveys", headers=headers, json={"title": title, **settings})
    assert result.status_code == 201, result.text
    return result.json()


def select(api, headers, target, question):
    result = api.post(f"/api/surveys/{target['id']}/question-selections", headers=headers,
                      params={"expected_revision": target["revision"]}, json={"version_id": question["id"]})
    assert result.status_code == 201, result.text
    return result.json()


def add_local(api, headers, target, content):
    result = api.post(f"/api/surveys/{target['id']}/questions", headers=headers,
                      params={"expected_revision": target["revision"]}, json=content)
    assert result.status_code == 201, result.text
    return result.json()


def publish(api, headers, target):
    result = api.post(f"/api/surveys/{target['id']}/publish", headers=headers,
                      json={"expected_revision": target["revision"]})
    assert result.status_code == 200, result.text
    return result.json()


def rules(api, headers, target, configured):
    result = api.put(f"/api/surveys/{target['id']}/rules", headers=headers,
                     json={"expected_revision": target["revision"], "rules": configured})
    assert result.status_code == 200, result.text
    return result.json()


def payload(target, answers, anonymous=False):
    return {"survey_revision": target["revision"], "request_id": uuid4().hex,
            "answers": answers, "anonymous": anonymous}


def submit(api, headers, target, answers, anonymous=False):
    result = api.post(target["share_url"] + "/responses", headers=headers,
                      json=payload(target, answers, anonymous))
    assert result.status_code == 201, result.text
    return result.json()


def grant(api, headers, question, recipient):
    result = api.post(f"{QUESTIONS}/{question['id']}/grants", headers=headers,
                      json={"username": recipient["username"]})
    assert result.status_code == 201, result.text


def revoke(api, headers, question, recipient):
    result = api.delete(f"{QUESTIONS}/{question['id']}/grants/{recipient['id']}", headers=headers)
    assert result.status_code == 204, result.text
