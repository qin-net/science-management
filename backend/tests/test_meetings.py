"""Group meetings (specs/010 section 4, G1-G9 and H1-H3). Real PostgreSQL, no mocks."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from researchos.db import get_engine
from tests.conftest import Account, add_member, sign_up


def api(project_id: str, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/meetings{suffix}"


def when(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def make(account: Account, project_id: str, title: str = "Weekly", **fields) -> dict:
    body = {"title": title, "starts_at": when(days=1), **fields}
    response = account.client.post(api(project_id), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def overview(account: Account) -> dict:
    response = account.client.get(f"/api/v1/workspaces/{account.workspace_id}/overview")
    assert response.status_code == 200, response.text
    return response.json()


def archive(account: Account, project_id: str) -> None:
    current = account.client.get(f"/api/v1/projects/{project_id}").json()
    done = account.client.patch(
        f"/api/v1/projects/{project_id}",
        json={"status": "archived", "expected_version": current["version"]},
    )
    assert done.status_code == 200, done.text


# --- G1, G2, G7: who may do what ---------------------------------------------------------------


def test_g1_roles(owner: Account):
    project = owner.new_project()["id"]
    viewer, member, other = (add_member(owner, r) for r in ("viewer", "member", "member"))
    outsider = sign_up()

    by_member = make(member, project, "member's")
    assert viewer.client.get(api(project)).status_code == 200
    assert outsider.client.get(api(project)).status_code == 404
    body = {"title": "x", "starts_at": when(days=1)}
    assert viewer.client.post(api(project), json=body).status_code == 403
    assert outsider.client.post(api(project), json=body).status_code == 404

    item = by_member
    patch = {"expected_version": item["version"], "title": "renamed"}
    assert viewer.client.patch(api(project, f"/{item['id']}"), json=patch).status_code == 403
    assert other.client.patch(api(project, f"/{item['id']}"), json=patch).status_code == 200

    assert viewer.client.delete(api(project, f"/{item['id']}")).status_code == 403
    assert other.client.delete(api(project, f"/{item['id']}")).status_code == 403  # not the author
    assert owner.client.delete(api(project, f"/{item['id']}")).status_code == 204  # an admin may
    mine = make(member, project, "again")
    assert member.client.delete(api(project, f"/{mine['id']}")).status_code == 204


def test_g2_an_archived_project_is_readable_and_not_writable(owner: Account):
    project = owner.new_project()["id"]
    item = make(owner, project)
    archive(owner, project)
    listed = owner.client.get(api(project))
    assert listed.status_code == 200
    # The page decides what to show from these flags, so they must say read-only too.
    assert [(m["editable"], m["deletable"]) for m in listed.json()["items"]] == [(False, False)]
    body = {"title": "x", "starts_at": when(days=1)}
    for response in (
        owner.client.post(api(project), json=body),
        owner.client.patch(
            api(project, f"/{item['id']}"), json={"expected_version": 1, "title": "y"}
        ),
        owner.client.delete(api(project, f"/{item['id']}")),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "project_archived"


def test_g7_a_meeting_is_not_reachable_through_another_project(owner: Account):
    mine, theirs = owner.new_project()["id"], owner.new_project("Other")["id"]
    item = make(owner, theirs)
    assert owner.client.get(api(mine)).json()["items"] == []
    patch = {"expected_version": 1, "title": "x"}
    assert owner.client.patch(api(mine, f"/{item['id']}"), json=patch).status_code == 404
    assert owner.client.delete(api(mine, f"/{item['id']}")).status_code == 404


# --- G3, G8, G9: what is accepted and stored --------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "starts_at": "2030-01-01T09:00:00Z"},
        {"title": "   ", "starts_at": "2030-01-01T09:00:00Z"},
        {"title": "x" * 201, "starts_at": "2030-01-01T09:00:00Z"},
        {"title": "bad\x00", "starts_at": "2030-01-01T09:00:00Z"},
        {"title": "no zone", "starts_at": "2030-01-01T09:00:00"},
        {"title": "year", "starts_at": "1999-12-31T23:00:00Z"},
        {"title": "year", "starts_at": "2101-01-01T00:00:00Z"},
        {"title": "place", "starts_at": "2030-01-01T09:00:00Z", "location": "x" * 201},
        {"title": "place", "starts_at": "2030-01-01T09:00:00Z", "location": "a\x07b"},
        {"title": "extra", "starts_at": "2030-01-01T09:00:00Z", "created_by": "x"},
        {"title": "extra", "starts_at": "2030-01-01T09:00:00Z", "version": 9},
        {"title": "extra", "starts_at": "2030-01-01T09:00:00Z", "past": True},
        {"title": "missing time"},
    ],
)
def test_g3_invalid_bodies_are_refused(owner: Account, body):
    project = owner.new_project()["id"]
    response = owner.client.post(api(project), json=body)
    assert response.status_code == 422, response.text
    assert owner.client.get(api(project)).json()["items"] == []


def test_g3_limits_are_inclusive_and_an_empty_place_is_none(owner: Account):
    project = owner.new_project()["id"]
    assert make(owner, project, "x" * 200, location="y" * 200)["location"] == "y" * 200
    assert make(owner, project, "empty place", location="   ")["location"] is None
    hostile = '<img src=x onerror=alert(1)> & "quotes"'
    assert make(owner, project, hostile, location=hostile)["location"] == hostile


def test_g8_the_database_refuses_what_the_api_would(owner: Account):
    project = owner.new_project()
    base = {"w": project["workspace_id"], "p": project["id"], "u": owner.user["id"]}
    insert = (
        "INSERT INTO meetings (id, workspace_id, project_id, title, starts_at, location, "
        "created_by, version, created_at, updated_at) VALUES (gen_random_uuid(), :w, :p, :t, "
        "now(), :l, :u, 1, now(), now())"
    )
    for title, place in (("", None), ("x" * 201, None), ("ok", ""), ("ok", "y" * 201)):
        with pytest.raises(IntegrityError), get_engine().begin() as conn:
            conn.execute(text(insert), {**base, "t": title, "l": place})


def test_g9_the_instant_is_kept_whatever_offset_it_was_sent_in(owner: Account):
    project = owner.new_project()["id"]
    a = make(owner, project, "east", starts_at="2030-06-01T09:30:00+08:00")
    b = make(owner, project, "utc", starts_at="2030-06-01T01:30:00Z")
    assert a["starts_at"] == b["starts_at"]
    with get_engine().connect() as conn:
        stored = conn.execute(
            text("SELECT starts_at FROM meetings WHERE id = :i"), {"i": a["id"]}
        ).scalar_one()
    assert stored == datetime(2030, 6, 1, 1, 30, tzinfo=UTC)
    listed = owner.client.get(api(project)).json()["items"]
    assert {m["id"] for m in listed} == {a["id"], b["id"]}


# --- G4, G5, G6: reading, editing, events -----------------------------------------------------


def test_g4_listing_is_by_start_time_and_flags_follow_the_caller(owner: Account):
    project = owner.new_project()["id"]
    member = add_member(owner, "member")
    late = make(owner, project, "late", starts_at=when(days=5))
    early = make(member, project, "early", starts_at=when(days=1))
    gone = make(owner, project, "past", starts_at=when(days=-2))

    items = owner.client.get(api(project)).json()["items"]
    assert [m["id"] for m in items] == [gone["id"], early["id"], late["id"]]
    assert [m["past"] for m in items] == [True, False, False]
    by_id = {m["id"]: m for m in items}
    assert by_id[early["id"]]["created_by_name"] == member.user["display_name"]
    assert by_id[early["id"]]["deletable"] is True  # an owner may delete anyone's
    seen_by_member = {m["id"]: m for m in member.client.get(api(project)).json()["items"]}
    assert seen_by_member[early["id"]]["deletable"] is True
    assert seen_by_member[late["id"]]["deletable"] is False
    assert seen_by_member[late["id"]]["editable"] is True


def test_g5_editing_is_versioned_partial_and_idempotent(owner: Account):
    project = owner.new_project()["id"]
    item = make(owner, project, "orig", location="Room 1")
    url = api(project, f"/{item['id']}")

    stale = owner.client.patch(url, json={"expected_version": 5, "title": "x"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 1

    moved = owner.client.patch(url, json={"expected_version": 1, "title": "renamed"}).json()
    assert moved["title"] == "renamed" and moved["location"] == "Room 1"
    assert moved["version"] == 2 and moved["starts_at"] == item["starts_at"]

    cleared = owner.client.patch(url, json={"expected_version": 2, "location": None}).json()
    assert cleared["location"] is None and cleared["version"] == 3

    same = owner.client.patch(url, json={"expected_version": 3, "title": "renamed"}).json()
    assert same["version"] == 3  # nothing changed, nothing recorded

    for bad in ({"title": None}, {"starts_at": None}, {"title": ""}, {"created_by": "x"}):
        response = owner.client.patch(url, json={"expected_version": 3, **bad})
        assert response.status_code == 422, bad


def test_g6_events_name_the_change_and_hold_no_words(owner: Account):
    project = owner.new_project()["id"]
    item = make(owner, project, "SECRET-TITLE", location="SECRET-PLACE")
    url = api(project, f"/{item['id']}")
    owner.client.patch(url, json={"expected_version": 1, "title": "SECRET-NEW"})
    owner.client.delete(url)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT event_type, payload FROM research_events "
                "WHERE event_type LIKE 'meeting.%' ORDER BY created_at"
            )
        ).all()
    assert [r[0] for r in rows] == ["meeting.created", "meeting.updated", "meeting.deleted"]
    assert rows[1][1] == {"changed": ["title"]}
    assert "SECRET" not in str(rows)


# --- H1-H3: the home page ---------------------------------------------------------------------


def test_h1_next_meeting_is_the_earliest_that_has_not_started(owner: Account):
    assert overview(owner)["next_meeting"] is None
    project = owner.new_project("Live")["id"]
    old = owner.new_project("Old")["id"]
    stranger = sign_up()
    theirs = stranger.new_project()["id"]

    make(owner, project, "yesterday", starts_at=when(days=-1))
    make(owner, project, "later", starts_at=when(days=9))
    soon = make(owner, project, "soon", starts_at=when(days=3, hours=2, minutes=30))
    make(owner, old, "archived but sooner", starts_at=when(days=1))
    archive(owner, old)
    make(stranger, theirs, "foreign and sooner", starts_at=when(hours=1))

    nxt = overview(owner)["next_meeting"]
    assert nxt["id"] == soon["id"] and nxt["title"] == "soon"
    assert nxt["project_name"] == "Live" and nxt["project_id"] == project
    expected = timedelta(days=3, hours=2, minutes=30).total_seconds()
    assert abs(nxt["seconds_until"] - expected) < 5


def test_h1_ties_are_broken_by_creation_order(owner: Account):
    project = owner.new_project()["id"]
    same = when(days=2)
    first = make(owner, project, "first", starts_at=same)
    make(owner, project, "second", starts_at=same)
    assert overview(owner)["next_meeting"]["id"] == first["id"]


def test_h1_deleting_the_next_one_moves_to_the_following(owner: Account):
    project = owner.new_project()["id"]
    a = make(owner, project, "a", starts_at=when(days=1))
    b = make(owner, project, "b", starts_at=when(days=2))
    assert overview(owner)["next_meeting"]["id"] == a["id"]
    assert owner.client.delete(api(project, f"/{a['id']}")).status_code == 204
    assert overview(owner)["next_meeting"]["id"] == b["id"]


def test_h2_a_viewer_sees_the_next_meeting(owner: Account):
    project = owner.new_project()["id"]
    item = make(owner, project)
    viewer = add_member(owner, "viewer")
    assert overview(viewer)["next_meeting"]["id"] == item["id"]


def test_h3_activity_counts_meetings_and_the_total_adds_up(owner: Account):
    project = owner.new_project()["id"]  # project.created
    a = make(owner, project)  # meeting.created
    owner.client.patch(api(project, f"/{a['id']}"), json={"expected_version": 1, "title": "n"})
    owner.client.delete(api(project, f"/{a['id']}"))  # meeting.updated, meeting.deleted
    today = overview(owner)["activity"][-1]
    assert today["meetings"] == 3 and today["projects"] == 1
    assert today["total"] == (
        today["papers"] + today["notes"] + today["tasks"] + today["projects"] + today["meetings"]
    )
    events = overview(owner)["recent_events"]
    assert [e["event_type"] for e in events[:3]] == [
        "meeting.deleted",
        "meeting.updated",
        "meeting.created",
    ]
    assert "title" not in str(events)
