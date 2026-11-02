import pytest
from fastapi.testclient import TestClient

from lead_inbox.app import create_app
from lead_inbox.classifier import safe_default
from lead_inbox.config import Settings
from tests.fakes import FakeClassifier, FakeStore

TEAM_KEY = "team-key-for-tests-0123456789"
AUTH = {"Authorization": f"Bearer {TEAM_KEY}"}
LEAD = {"name": "Ana Lopez", "email": "Ana@Example.com", "company": "Acme",
        "message": "We need 20 standing desks before June, budget approved. Can you quote?"}


@pytest.fixture
def parts():
    settings = Settings("http://supabase.test", "service-key", TEAM_KEY)
    store, classifier = FakeStore(), FakeClassifier()
    client = TestClient(create_app(settings, store, classifier, rate_limit_per_minute=3))
    return client, store, classifier


def test_submit_saves_then_classifies(parts):
    client, store, classifier = parts
    response = client.post("/leads", json=LEAD)
    assert response.status_code == 201
    body = response.json()
    assert body["ok"] and "temperature" not in body           # the visitor never sees the score
    row = store.rows[body["id"]]
    assert row["temperature"] == "Hot" and row["status"] == "classified" and row["email"] == "ana@example.com"
    assert classifier.seen[0]["message"] == LEAD["message"]


def test_lead_is_kept_when_classification_cannot_be_saved(parts):
    client, store, _ = parts
    store.fail_update = True
    lead_id = client.post("/leads", json=LEAD).json()["id"]
    assert store.rows[lead_id]["status"] == "new"           # picked up later by classify-pending


def test_store_down_gives_503(parts):
    client, store, _ = parts
    store.fail_insert = True
    assert client.post("/leads", json=LEAD).status_code == 503


def test_validation(parts):
    client, _, _ = parts
    assert client.post("/leads", json={**LEAD, "email": "nope"}).status_code == 422
    assert client.post("/leads", json={**LEAD, "message": ""}).status_code == 422
    assert client.post("/leads", json={**LEAD, "message": "x" * 5001}).status_code == 422


def test_honeypot_drops_bots_quietly(parts):
    client, store, classifier = parts
    response = client.post("/leads", json={**LEAD, "website": "http://spam.example"})
    assert response.status_code == 201 and store.rows == {} and classifier.seen == []


def test_rate_limit(parts):
    client, _, _ = parts
    codes = [client.post("/leads", json=LEAD).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]


def test_team_endpoints_need_the_key(parts):
    client, _, _ = parts
    assert client.get("/leads").status_code == 401
    assert client.get("/leads", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/leads", headers=AUTH).status_code == 200


def test_list_filters(parts):
    client, store, classifier = parts
    client.post("/leads", json=LEAD)
    classifier.result = safe_default("API down")
    client.post("/leads", json={**LEAD, "name": "Ben"})
    hot = client.get("/leads?temperature=Hot", headers=AUTH).json()
    review = client.get("/leads?needs_review=true", headers=AUTH).json()
    assert hot["count"] == 1 and hot["leads"][0]["name"] == "Ana Lopez"
    assert review["count"] == 1 and review["leads"][0]["temperature"] == "Warm"
    assert client.get("/leads?temperature=Lukewarm", headers=AUTH).status_code == 422


def test_status_update(parts):
    client, store, _ = parts
    lead_id = client.post("/leads", json=LEAD).json()["id"]
    assert client.post(f"/leads/{lead_id}/status", json={"status": "contacted"}, headers=AUTH).json()["status"] == "contacted"
    assert client.post(f"/leads/{lead_id}/status", json={"status": "deleted"}, headers=AUTH).status_code == 422
    assert client.post("/leads/missing/status", json={"status": "won"}, headers=AUTH).status_code == 404


def test_classify_pending_handles_direct_inserts(parts):
    client, store, _ = parts
    store.insert_lead({"name": "Direct", "email": "d@example.com", "message": "Inserted with the anon key"}, None)
    result = client.post("/leads/classify-pending", headers=AUTH).json()
    assert result["classified"] == 1 and result["leads"][0]["temperature"] == "Hot"
    assert client.post("/leads/classify-pending", headers=AUTH).json()["classified"] == 0


def test_spam_gets_spam_status(parts):
    client, store, classifier = parts
    classifier.result.is_spam = True
    lead_id = client.post("/leads", json=LEAD).json()["id"]
    assert store.rows[lead_id]["status"] == "spam"


def test_health(parts):
    client, _, _ = parts
    assert client.get("/health").json() == {"ok": True}


def test_settings_require_strong_team_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://x")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("TEAM_API_KEY", "short")
    with pytest.raises(RuntimeError, match="24"):
        Settings.from_env()
