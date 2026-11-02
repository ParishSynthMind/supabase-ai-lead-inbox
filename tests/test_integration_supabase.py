"""Integration tests against the LOCAL Supabase stack.

    supabase start
    SUPABASE_INTEGRATION=1 pytest -m integration

Keys come from `supabase status -o env` at runtime (the standard local development keys).
Every row these tests create uses an @rls-test.example address and is deleted afterwards.
"""
import os
import subprocess
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from lead_inbox.app import create_app
from lead_inbox.config import Settings
from lead_inbox.store import SupabaseStore
from tests.fakes import FakeClassifier

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SUPABASE_INTEGRATION") != "1", reason="set SUPABASE_INTEGRATION=1 with supabase start running"),
]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOMAIN = "rls-test.example"


@pytest.fixture(scope="module")
def env():
    out = subprocess.run(["supabase", "status", "-o", "env"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {k: v.strip().strip('"') for k, v in values.items()}


def headers(key, **extra):
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", **extra}


@pytest.fixture(scope="module")
def rest(env):
    url = f"{env['API_URL']}/rest/v1/leads"
    yield url
    httpx.delete(url, params={"email": f"like.*@{DOMAIN}"}, headers=headers(env["SERVICE_ROLE_KEY"]))


def lead(**over):
    return {"name": "RLS Test", "email": f"{uuid.uuid4().hex[:8]}@{DOMAIN}", "message": "Integration test lead", **over}


def psql(env, sql):
    project = "supabase-ai-lead-inbox"
    return subprocess.run(["docker", "exec", f"supabase_db_{project}", "psql", "-U", "postgres", "-tA", "-c", sql],
                          capture_output=True, text=True)


def test_anon_can_insert(env, rest):
    r = httpx.post(rest, json=lead(), headers=headers(env["ANON_KEY"], Prefer="return=minimal"))
    assert r.status_code == 201


def test_anon_cannot_read(env, rest):
    r = httpx.get(rest, params={"select": "*"}, headers=headers(env["ANON_KEY"]))
    assert r.status_code == 401 and r.json()["code"] == "42501"


def test_anon_cannot_read_back_its_own_insert(env, rest):
    r = httpx.post(rest, json=lead(), headers=headers(env["ANON_KEY"], Prefer="return=representation"))
    assert r.status_code == 401


def test_anon_cannot_set_pipeline_columns(env, rest):
    for extra in ({"temperature": "Hot"}, {"status": "classified"}, {"score": 99}, {"needs_review": True}):
        r = httpx.post(rest, json=lead(**extra), headers=headers(env["ANON_KEY"], Prefer="return=minimal"))
        assert r.status_code == 401, extra


def test_anon_cannot_update_or_delete(env, rest):
    anon = headers(env["ANON_KEY"])
    assert httpx.patch(rest, params={"email": f"like.*@{DOMAIN}"}, json={"message": "x"}, headers=anon).status_code == 401
    assert httpx.delete(rest, params={"email": f"like.*@{DOMAIN}"}, headers=anon).status_code == 401


def test_database_constraints_apply_to_anon(env, rest):
    r = httpx.post(rest, json=lead(email="not-an-email"), headers=headers(env["ANON_KEY"], Prefer="return=minimal"))
    assert r.status_code == 400 and r.json()["code"] == "23514"


def test_signed_in_user_cannot_read(env, rest):
    email, password = f"user-{uuid.uuid4().hex[:6]}@{DOMAIN}", uuid.uuid4().hex
    admin = httpx.post(f"{env['API_URL']}/auth/v1/admin/users", headers=headers(env["SERVICE_ROLE_KEY"]),
                       json={"email": email, "password": password, "email_confirm": True})
    assert admin.status_code == 200, admin.text
    try:
        token = httpx.post(f"{env['API_URL']}/auth/v1/token", params={"grant_type": "password"},
                           headers={"apikey": env["ANON_KEY"]}, json={"email": email, "password": password}).json()
        r = httpx.get(rest, params={"select": "*"},
                      headers={"apikey": env["ANON_KEY"], "Authorization": f"Bearer {token['access_token']}"})
        # PostgREST answers 403 (not 401) when the caller is signed in but lacks the privilege.
        assert r.status_code == 403 and r.json()["code"] == "42501"
    finally:
        httpx.delete(f"{env['API_URL']}/auth/v1/admin/users/{admin.json()['id']}", headers=headers(env["SERVICE_ROLE_KEY"]))


def test_rls_still_hides_rows_if_select_is_granted_by_mistake(env, rest):
    # Inside a rolled-back transaction: grant SELECT to anon, then read as anon. RLS (no SELECT
    # policy) must return zero rows even though the privilege is there.
    httpx.post(rest, json=lead(), headers=headers(env["ANON_KEY"], Prefer="return=minimal"))
    result = psql(env, "begin; grant select on public.leads to anon; set local role anon; "
                       "select 'visible=' || count(*) from public.leads; rollback;")
    assert result.returncode == 0, result.stderr
    assert "visible=0" in result.stdout
    assert psql(env, "select relrowsecurity from pg_class where oid = 'public.leads'::regclass").stdout.strip() == "t"


def test_service_role_reads(env, rest):
    r = httpx.get(rest, params={"select": "id,email", "email": f"like.*@{DOMAIN}"}, headers=headers(env["SERVICE_ROLE_KEY"]))
    assert r.status_code == 200 and len(r.json()) >= 1


def test_full_service_flow_against_real_database(env, rest):
    settings = Settings(env["API_URL"], env["SERVICE_ROLE_KEY"], "integration-team-key-0123456789")
    store = SupabaseStore(settings.supabase_url, settings.service_role_key)
    app = TestClient(create_app(settings, store, FakeClassifier(), rate_limit_per_minute=100))
    auth = {"Authorization": f"Bearer {settings.team_api_key}"}

    lead_id = app.post("/leads", json=lead(company="Acme")).json()["id"]
    row = next(l for l in app.get("/leads?temperature=Hot&limit=200", headers=auth).json()["leads"] if l["id"] == lead_id)
    assert row["status"] == "classified" and row["score"] == 85 and row["classified_at"]

    # A lead the website inserted directly with the anon key is picked up by classify-pending.
    direct = lead(name="Direct Insert")
    httpx.post(rest, json=direct, headers=headers(env["ANON_KEY"], Prefer="return=minimal"))
    app.post("/leads/classify-pending?limit=100", headers=auth)
    stored = httpx.get(rest, params={"select": "status,temperature", "email": f"eq.{direct['email']}"},
                       headers=headers(env["SERVICE_ROLE_KEY"])).json()[0]
    assert stored == {"status": "classified", "temperature": "Hot"}
    assert app.get("/health").json() == {"ok": True}
