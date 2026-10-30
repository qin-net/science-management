"""Acceptance A1-A9 in specs/001: accounts, roles and tenant isolation."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from researchos.db import session_factory
from researchos.main import app
from researchos.models import AuthSession, Job, Paper, ResearchEvent, User
from researchos.worker import Worker
from tests.conftest import add_member, sign_up
from tests.pdf_samples import text_pdf

PASSWORD = "correct horse battery"


def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def imported_paper(account) -> tuple[dict, dict]:
    project = account.new_project()
    response = account.upload(project["id"], text_pdf())
    assert response.status_code == 202, response.text
    return project, response.json()


def test_a1_register_creates_owned_workspace_and_stores_no_plaintext():
    browser = client()
    response = browser.post(
        "/api/v1/auth/register",
        json={"email": "A1@Example.com", "password": PASSWORD, "display_name": "A"},
    )
    assert response.status_code == 201
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = browser.cookies["ros_session"]

    assert [w["role"] for w in browser.get("/api/v1/workspaces").json()] == ["owner"]
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.email == "a1@example.com"))
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
        assert token not in session.token_hash


def test_a2_wrong_password_and_unknown_email_look_identical(owner):
    wrong = client().post(
        "/api/v1/auth/login", json={"email": owner.user["email"], "password": "wrong-password"}
    )
    unknown = client().post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_a3_logged_out_and_expired_sessions_are_refused(owner):
    assert owner.client.get("/api/v1/auth/me").status_code == 200

    other = client()
    login = other.post(
        "/api/v1/auth/login", json={"email": owner.user["email"], "password": PASSWORD}
    )
    assert login.status_code == 200
    with session_factory()() as db:
        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == uuid.UUID(owner.user["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    assert other.get("/api/v1/auth/me").status_code == 401

    fresh = client()
    fresh.post("/api/v1/auth/login", json={"email": owner.user["email"], "password": PASSWORD})
    stale_cookie = fresh.cookies["ros_session"]
    assert fresh.post("/api/v1/auth/logout").status_code == 204
    replay = client()
    replay.cookies.set("ros_session", stale_cookie)
    assert replay.get("/api/v1/auth/me").status_code == 401


def test_anonymous_requests_are_refused():
    assert client().get("/api/v1/workspaces").status_code == 401


def test_a4_outsider_gets_404_everywhere_and_changes_nothing(owner, outsider):
    project, paper = imported_paper(owner)
    version = paper["current_version"]["id"]
    project_id, paper_id = project["id"], paper["id"]
    job_id = paper["parse_job"]["id"]
    base = f"/api/v1/projects/{project_id}"
    pdf = {"content": text_pdf(), "headers": {"Content-Type": "application/pdf"}}

    with session_factory()() as db:
        events_before = len(list(db.scalars(select(ResearchEvent))))

    reads = [
        f"/api/v1/workspaces/{owner.workspace_id}/projects",
        f"/api/v1/workspaces/{owner.workspace_id}/members",
        base,
        f"{base}/events",
        f"{base}/papers",
        f"{base}/papers/{paper_id}",
        f"{base}/papers/{paper_id}/versions",
        f"{base}/papers/{paper_id}/versions/{version}/file",
        f"{base}/papers/{paper_id}/versions/{version}/blocks",
    ]
    for path in reads:
        response = outsider.client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found"

    writes = [
        ("POST", f"/api/v1/workspaces/{owner.workspace_id}/projects", {"json": {"name": "x"}}),
        (
            "POST",
            f"/api/v1/workspaces/{owner.workspace_id}/members",
            {"json": {"email": outsider.user["email"], "role": "owner"}},
        ),
        ("PATCH", base, {"json": {"expected_version": 1, "name": "x"}}),
        ("POST", f"{base}/papers", pdf),
        ("PATCH", f"{base}/papers/{paper_id}", {"json": {"title": "x"}}),
        ("POST", f"{base}/papers/{paper_id}/versions", pdf),
        ("POST", f"{base}/papers/{paper_id}/versions/{version}/parse", {}),
        ("POST", f"{base}/jobs/{job_id}/cancel", {}),
        ("DELETE", f"{base}/papers/{paper_id}", {}),
    ]
    for method, path, kwargs in writes:
        assert outsider.client.request(method, path, **kwargs).status_code == 404, path

    with session_factory()() as db:
        assert len(list(db.scalars(select(ResearchEvent)))) == events_before
        job = db.get(Job, uuid.UUID(job_id))
        assert job.cancel_requested is False
        assert db.get(Paper, uuid.UUID(paper_id)).title is None


def test_a5_viewer_reads_but_cannot_write(owner):
    project, paper = imported_paper(owner)
    Worker().run_once()
    viewer = add_member(owner, "viewer")
    base = f"/api/v1/projects/{project['id']}"
    version = paper["current_version"]["id"]

    assert viewer.client.get(f"{base}/papers").status_code == 200
    file_url = f"{base}/papers/{paper['id']}/versions/{version}/file"
    assert viewer.client.get(file_url).status_code == 200
    blocks = viewer.client.get(f"{base}/papers/{paper['id']}/versions/{version}/blocks")
    assert blocks.status_code == 200 and blocks.json()["parsed"] is True

    pdf = {"content": text_pdf(), "headers": {"Content-Type": "application/pdf"}}
    denied = [
        ("POST", f"/api/v1/workspaces/{owner.workspace_id}/projects", {"json": {"name": "x"}}),
        ("PATCH", base, {"json": {"expected_version": 1, "name": "x"}}),
        ("POST", f"{base}/papers", pdf),
        ("PATCH", f"{base}/papers/{paper['id']}", {"json": {"title": "x"}}),
        ("POST", f"{base}/papers/{paper['id']}/versions/{version}/parse", {}),
        ("POST", f"{base}/jobs/{paper['parse_job']['id']}/cancel", {}),
        ("DELETE", f"{base}/papers/{paper['id']}", {}),
    ]
    for method, path, kwargs in denied:
        response = viewer.client.request(method, path, **kwargs)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "forbidden"


def test_a6_removed_member_loses_access_with_a_live_session(owner):
    project, _ = imported_paper(owner)
    member = add_member(owner, "member")
    path = f"/api/v1/projects/{project['id']}/papers"
    assert member.client.get(path).status_code == 200
    removed = owner.client.delete(
        f"/api/v1/workspaces/{owner.workspace_id}/members/{member.user['id']}"
    )
    assert removed.status_code == 204
    assert member.client.get("/api/v1/auth/me").status_code == 200
    assert member.client.get(path).status_code == 404


def test_a7_paper_is_unreachable_through_another_project(owner):
    first, paper = imported_paper(owner)
    second = owner.new_project("Second")
    version = paper["current_version"]["id"]
    base = f"/api/v1/projects/{second['id']}/papers/{paper['id']}"
    assert owner.client.get(base).status_code == 404
    assert owner.client.get(f"{base}/versions/{version}/file").status_code == 404
    assert owner.client.get(f"{base}/versions/{version}/blocks").status_code == 404
    assert owner.client.post(f"{base}/versions/{version}/parse").status_code == 404
    assert owner.client.delete(base).status_code == 404
    home = f"/api/v1/projects/{first['id']}/papers/{paper['id']}"
    assert owner.client.get(home).status_code == 200


def test_a8_last_owner_cannot_be_removed_or_demoted(owner):
    me = owner.user["id"]
    members = f"/api/v1/workspaces/{owner.workspace_id}/members/{me}"
    demoted = owner.client.patch(members, json={"role": "admin"})
    assert demoted.status_code == 409 and demoted.json()["error"]["code"] == "last_owner"
    removed = owner.client.delete(members)
    assert removed.status_code == 409 and removed.json()["error"]["code"] == "last_owner"


def test_admin_cannot_touch_owner_role(owner):
    admin = add_member(owner, "admin")
    target = f"/api/v1/workspaces/{owner.workspace_id}/members/{owner.user['id']}"
    assert admin.client.delete(target).status_code == 403
    grant = admin.client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/members",
        json={"email": sign_up().user["email"], "role": "owner"},
    )
    assert grant.status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_a9_cross_site_write_is_rejected(owner, origin):
    response = owner.client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/projects",
        json={"name": "x"},
        headers={"Origin": origin},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"


def test_a9_allowed_origin_can_write(owner):
    response = owner.client.post(
        f"/api/v1/workspaces/{owner.workspace_id}/projects",
        json={"name": "x"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 201


def test_archived_project_rejects_writes_but_stays_readable(owner):
    project, paper = imported_paper(owner)
    path = f"/api/v1/projects/{project['id']}"
    archived = owner.client.patch(path, json={"expected_version": 1, "status": "archived"})
    assert archived.status_code == 200
    rejected = owner.upload(project["id"], text_pdf())
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "project_archived"
    assert owner.client.get(f"{path}/papers/{paper['id']}").status_code == 200


def test_project_edit_with_stale_version_conflicts(owner):
    project = owner.new_project()
    path = f"/api/v1/projects/{project['id']}"
    assert owner.client.patch(path, json={"expected_version": 1, "name": "A"}).status_code == 200
    stale = owner.client.patch(path, json={"expected_version": 1, "name": "B"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    assert owner.client.get(path).json()["name"] == "A"


def test_database_holds_no_session_tokens_in_events(owner):
    token = owner.client.cookies["ros_session"]
    with session_factory()() as db:
        dump = db.scalar(
            text("select coalesce(string_agg(payload::text, ' '), '') from research_events")
        )
    assert token not in dump
