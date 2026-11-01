"""Project tasks (specs/004 section 4, T1-T15). Real PostgreSQL, no mocks."""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from researchos.db import get_engine, session_factory
from researchos.models import Task
from researchos.worker import Worker
from tests.conftest import Account, add_member, sign_up
from tests.pdf_samples import text_pdf


def api(project_id: str, tail: str = "") -> str:
    return f"/api/v1/projects/{project_id}{tail}"


def today():
    return datetime.now(UTC).date()


def make(account: Account, project_id: str, title: str = "Task", **fields):
    response = account.client.post(api(project_id, "/tasks"), json={"title": title, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def patch(account: Account, project_id: str, task: dict, **fields):
    return account.client.patch(
        api(project_id, f"/tasks/{task['id']}"),
        json={"expected_version": task["version"], **fields},
    )


def listing(account: Account, project_id: str, **params):
    response = account.client.get(api(project_id, "/tasks"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def count_tasks() -> int:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT count(*) FROM tasks")).scalar_one()


def error_code(response) -> str:
    return response.json()["error"]["code"]


@pytest.fixture
def project_id(owner: Account) -> str:
    return owner.new_project()["id"]


# --- T1-T4: who may do what ---------------------------------------------------------------


def test_t1_viewer_reads_but_cannot_write_and_outsider_sees_nothing(owner, project_id):
    task = make(owner, project_id)
    viewer = add_member(owner, "viewer")
    outsider = sign_up()

    item = listing(viewer, project_id)["items"][0]
    assert item["id"] == task["id"] and item["editable"] is False and item["deletable"] is False
    assert viewer.client.post(api(project_id, "/tasks"), json={"title": "x"}).status_code == 403
    assert patch(viewer, project_id, task, title="y").status_code == 403
    assert viewer.client.delete(api(project_id, f"/tasks/{task['id']}")).status_code == 403
    assert count_tasks() == 1

    assert outsider.client.get(api(project_id, "/tasks")).status_code == 404
    assert outsider.client.post(api(project_id, "/tasks"), json={"title": "x"}).status_code == 404
    assert patch(outsider, project_id, task, title="y").status_code == 404
    assert outsider.client.delete(api(project_id, f"/tasks/{task['id']}")).status_code == 404
    assert count_tasks() == 1


def test_t1_archived_project_is_readable_but_not_writable(owner, project_id):
    task = make(owner, project_id)
    project = owner.client.get(api(project_id)).json()
    archived = owner.client.patch(
        api(project_id), json={"status": "archived", "expected_version": project["version"]}
    )
    assert archived.status_code == 200
    items = listing(owner, project_id)["items"]
    assert items[0]["editable"] is False and items[0]["deletable"] is False
    for call in (
        owner.client.post(api(project_id, "/tasks"), json={"title": "x"}),
        patch(owner, project_id, task, title="y"),
        owner.client.delete(api(project_id, f"/tasks/{task['id']}")),
    ):
        assert call.status_code == 409 and error_code(call) == "project_archived"
    assert count_tasks() == 1


def test_t2_any_member_edits_but_only_creator_or_admin_deletes(owner, project_id):
    a, b = add_member(owner, "member"), add_member(owner, "member")
    task = make(a, project_id, "from a")

    seen_by_b = listing(b, project_id)["items"][0]
    assert seen_by_b["editable"] is True and seen_by_b["deletable"] is False
    assert listing(a, project_id)["items"][0]["deletable"] is True
    assert listing(owner, project_id)["items"][0]["deletable"] is True

    moved = patch(b, project_id, task, status="doing", title="edited by b")
    assert moved.status_code == 200 and moved.json()["status"] == "doing"
    assert moved.json()["created_by"] == a.user["id"]
    assert b.client.delete(api(project_id, f"/tasks/{task['id']}")).status_code == 403
    assert count_tasks() == 1
    assert a.client.delete(api(project_id, f"/tasks/{task['id']}")).status_code == 204

    other = make(a, project_id, "again")
    assert owner.client.delete(api(project_id, f"/tasks/{other['id']}")).status_code == 204
    assert count_tasks() == 0


def test_t3_other_projects_objects_are_refused_and_never_listed(owner, project_id):
    other_project = owner.new_project("Other")["id"]
    foreign_paper = owner.upload(other_project, text_pdf("foreign")).json()["id"]
    foreign_task = make(owner, other_project, "foreign")

    for call in (
        owner.client.post(
            api(project_id, "/tasks"), json={"title": "x", "paper_id": foreign_paper}
        ),
        patch(owner, project_id, make(owner, project_id, "mine"), paper_id=foreign_paper),
    ):
        assert call.status_code == 404
    assert count_tasks() == 2  # foreign and mine; the refused create left nothing

    assert [t["title"] for t in listing(owner, project_id)["items"]] == ["mine"]
    assert patch(owner, project_id, foreign_task, title="hijack").status_code == 404
    assert owner.client.delete(api(project_id, f"/tasks/{foreign_task['id']}")).status_code == 404
    assert listing(owner, other_project)["items"][0]["title"] == "foreign"


def test_t4_assignee_must_be_a_member_of_the_workspace(owner, project_id):
    member = add_member(owner, "member")
    stranger = sign_up()
    ok = make(owner, project_id, assignee_id=member.user["id"])
    assert ok["assignee_id"] == member.user["id"] and ok["assignee_active"] is True
    assert ok["assignee_name"] == member.user["display_name"]

    for who in (stranger.user["id"], str(uuid.uuid4())):
        refused = owner.client.post(
            api(project_id, "/tasks"), json={"title": "x", "assignee_id": who}
        )
        assert refused.status_code == 422 and error_code(refused) == "invalid_assignee"
        edit = patch(owner, project_id, ok, assignee_id=who)
        assert edit.status_code == 422 and error_code(edit) == "invalid_assignee"
    assert count_tasks() == 1
    assert listing(owner, project_id)["items"][0]["assignee_id"] == member.user["id"]


# --- T5-T8: status and fields ------------------------------------------------------------


def test_t5_blocked_needs_a_reason_and_only_blocked_has_one(owner, project_id):
    task = make(owner, project_id)
    bare = patch(owner, project_id, task, status="blocked")
    assert bare.status_code == 422 and error_code(bare) == "blocked_reason_required"
    blank = patch(owner, project_id, task, status="blocked", blocked_reason="   ")
    assert blank.status_code == 422 and error_code(blank) == "blocked_reason_required"
    early = patch(owner, project_id, task, blocked_reason="because")
    assert early.status_code == 422 and error_code(early) == "blocked_reason_not_allowed"
    create = owner.client.post(api(project_id, "/tasks"), json={"title": "x", "status": "blocked"})
    assert create.status_code == 422 and error_code(create) == "blocked_reason_required"
    too_long = patch(owner, project_id, task, status="blocked", blocked_reason="x" * 501)
    assert too_long.status_code == 422
    assert listing(owner, project_id)["items"][0]["version"] == 1

    blocked = patch(owner, project_id, task, status="blocked", blocked_reason="waiting for data")
    assert blocked.status_code == 200 and blocked.json()["blocked_reason"] == "waiting for data"
    reworded = patch(owner, project_id, blocked.json(), blocked_reason="waiting for access")
    assert reworded.status_code == 200 and reworded.json()["blocked_reason"] == "waiting for access"
    # A reason left in the body while leaving blocked is refused rather than guessed at.
    keep = patch(owner, project_id, reworded.json(), status="doing", blocked_reason="still")
    assert keep.status_code == 422 and error_code(keep) == "blocked_reason_not_allowed"
    left = patch(owner, project_id, reworded.json(), status="doing")
    assert left.status_code == 200 and left.json()["blocked_reason"] is None

    created = make(owner, project_id, status="blocked", blocked_reason="from the start")
    assert created["status"] == "blocked" and created["blocked_reason"] == "from the start"


def test_t6_done_records_the_time_and_every_transition_is_allowed(owner, project_id):
    task = make(owner, project_id)
    assert task["completed_at"] is None
    done = patch(owner, project_id, task, status="done").json()
    assert done["completed_at"] is not None and done["status"] == "done"
    reopened = patch(owner, project_id, done, status="doing").json()
    assert reopened["completed_at"] is None and reopened["status"] == "doing"
    cancelled = patch(owner, project_id, reopened, status="cancelled").json()
    assert cancelled["status"] == "cancelled" and cancelled["completed_at"] is None
    restored = patch(owner, project_id, cancelled, status="todo").json()
    assert restored["status"] == "todo"

    # Every ordered pair of statuses works.
    statuses = ["todo", "doing", "blocked", "done", "cancelled"]
    current = restored
    for source in statuses:
        for target in statuses:
            fields = {"status": source}
            if source == "blocked":
                fields["blocked_reason"] = "why"
            current = patch(owner, project_id, current, **fields).json()
            assert current["status"] == source
            fields = {"status": target}
            if target == "blocked":
                fields["blocked_reason"] = "why"
            response = patch(owner, project_id, current, **fields)
            assert response.status_code == 200, (source, target, response.text)
            current = response.json()
            assert (current["completed_at"] is not None) == (target == "done")
            assert (current["blocked_reason"] is not None) == (target == "blocked")


def test_t6_editing_a_done_task_keeps_its_completion_time(owner, project_id):
    done = patch(owner, project_id, make(owner, project_id), status="done").json()
    renamed = patch(owner, project_id, done, title="renamed").json()
    assert renamed["completed_at"] == done["completed_at"]


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "finished"},
        {"status": "done"},  # a task is created open; done is reached by editing
        {"priority": "urgent"},
        {"due_date": "2026-02-30"},
        {"due_date": "tomorrow"},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"description": "x" * 5001},
        {"title": "bad\x00title"},
        {"description": "bell\x07"},
        {"created_by": "00000000-0000-0000-0000-000000000000"},
        {"overdue": True},
        {"completed_at": "2026-01-01T00:00:00Z"},
        {"version": 9},
    ],
)
def test_t7_invalid_input_is_refused_and_nothing_is_stored(owner, project_id, fields):
    response = owner.client.post(api(project_id, "/tasks"), json={"title": "ok", **fields})
    assert response.status_code == 422, response.text
    assert count_tasks() == 0


def test_t7_invalid_edits_are_refused_and_nothing_changes(owner, project_id):
    task = make(owner, project_id, "keep", description="keep")
    for fields in (
        {"title": ""},
        {"title": None},
        {"description": None},
        {"status": None},
        {"priority": None},
        {"status": "nonsense"},
        {"due_date": "2026-13-01"},
        {"created_by": str(uuid.uuid4())},
        {"completed_at": "2026-01-01T00:00:00Z"},
        {"overdue": False},
        {"title": "x" * 201},
    ):
        assert patch(owner, project_id, task, **fields).status_code == 422, fields
    assert (
        owner.client.patch(api(project_id, f"/tasks/{task['id']}"), json={"title": "x"}).status_code
        == 422
    )
    stored = listing(owner, project_id)["items"][0]
    assert stored["title"] == "keep" and stored["description"] == "keep" and stored["version"] == 1


def test_t7_limits_are_inclusive_and_text_is_trimmed(owner, project_id):
    task = make(owner, project_id, "  " + "t" * 200 + "  ", description="d" * 4991 + "\nline\ttab")
    assert task["title"] == "t" * 200
    assert len(task["description"]) == 5000 and task["description"].endswith("\nline\ttab")


def test_t8_overdue_is_decided_by_the_server(owner, project_id):
    yesterday = (today() - timedelta(days=1)).isoformat()
    tomorrow = (today() + timedelta(days=1)).isoformat()
    open_late = [
        make(owner, project_id, s, status=s, due_date=yesterday) for s in ("todo", "doing")
    ]
    blocked = patch(
        owner,
        project_id,
        make(owner, project_id, "blocked", due_date=yesterday),
        status="blocked",
        blocked_reason="r",
    ).json()
    done = patch(
        owner, project_id, make(owner, project_id, "d", due_date=yesterday), status="done"
    ).json()
    cancelled = patch(
        owner, project_id, make(owner, project_id, "c", due_date=yesterday), status="cancelled"
    ).json()
    no_date = make(owner, project_id, "none")
    due_today = make(owner, project_id, "today", due_date=today().isoformat())
    due_tomorrow = make(owner, project_id, "tomorrow", due_date=tomorrow)

    assert all(t["overdue"] for t in [*open_late, blocked])
    assert not any(t["overdue"] for t in [done, cancelled, no_date, due_today, due_tomorrow])
    page = listing(owner, project_id)
    assert {t["title"]: t["overdue"] for t in page["items"]} == {
        "todo": True,
        "doing": True,
        "blocked": True,
        "d": False,
        "c": False,
        "none": False,
        "today": False,
        "tomorrow": False,
    }
    assert page["counts"]["overdue"] == 3
    reopened = patch(owner, project_id, done, status="todo").json()
    assert reopened["overdue"] is True
    assert listing(owner, project_id)["counts"]["overdue"] == 4


# --- T9-T13: concurrency and consistency -------------------------------------------------


def test_t9_two_edits_from_the_same_version_cannot_both_win(owner, project_id):
    task = make(owner, project_id, "start")
    results = []
    barrier = threading.Barrier(2)

    def edit(title: str) -> None:
        barrier.wait()
        results.append((title, patch(owner, project_id, task, title=title)))

    threads = [threading.Thread(target=edit, args=(name,)) for name in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status_code for _, r in results) == [200, 409]
    winner = next(title for title, r in results if r.status_code == 200)
    loser = next(r for _, r in results if r.status_code == 409)
    assert error_code(loser) == "version_conflict"
    assert loser.json()["error"]["details"]["current_version"] == 2
    stored = listing(owner, project_id)["items"][0]
    assert stored["title"] == winner and stored["version"] == 2
    assert patch(owner, project_id, task, title="late").status_code == 409
    assert listing(owner, project_id)["items"][0]["title"] == winner


def test_t10_null_clears_and_unsent_fields_stay(owner, project_id):
    member = add_member(owner, "member")
    paper = owner.upload(project_id, text_pdf("p")).json()["id"]
    task = make(
        owner,
        project_id,
        "t",
        description="desc",
        priority="high",
        assignee_id=member.user["id"],
        due_date="2031-01-01",
        paper_id=paper,
    )
    assert task["paper_id"] == paper and task["due_date"] == "2031-01-01"
    renamed = patch(owner, project_id, task, title="new").json()
    assert (renamed["assignee_id"], renamed["due_date"], renamed["paper_id"]) == (
        member.user["id"],
        "2031-01-01",
        paper,
    )
    assert renamed["description"] == "desc" and renamed["priority"] == "high"
    cleared = patch(
        owner, project_id, renamed, assignee_id=None, due_date=None, paper_id=None
    ).json()
    assert (cleared["assignee_id"], cleared["due_date"], cleared["paper_id"]) == (None, None, None)
    assert cleared["assignee_name"] is None and cleared["assignee_active"] is None
    assert cleared["paper_title"] is None
    assert cleared["title"] == "new" and cleared["priority"] == "high"


def test_t10_a_patch_that_changes_nothing_does_not_bump_the_version(owner, project_id):
    task = make(owner, project_id, "same")
    again = patch(owner, project_id, task, title="same")
    assert again.status_code == 200 and again.json()["version"] == 1
    events = owner.client.get(api(project_id, "/events")).json()
    assert [e["event_type"] for e in events if e["event_type"].startswith("task.")] == [
        "task.created"
    ]


def test_t11_an_assignee_who_left_is_kept_and_flagged(owner, project_id):
    member = add_member(owner, "member")
    make(owner, project_id, assignee_id=member.user["id"])
    removed = owner.client.delete(
        f"/api/v1/workspaces/{owner.workspace_id}/members/{member.user['id']}"
    )
    assert removed.status_code == 204
    item = listing(owner, project_id)["items"][0]
    assert item["assignee_id"] == member.user["id"] and item["assignee_active"] is False
    assert item["assignee_name"] == member.user["display_name"]
    # An unchanged departed assignee does not block unrelated edits.
    assert patch(owner, project_id, item, title="renamed").status_code == 200
    # But no one can newly assign the departed person.
    again = patch(owner, project_id, item | {"version": 2}, assignee_id=None)
    assert again.status_code == 200
    back = patch(owner, project_id, again.json(), assignee_id=member.user["id"])
    assert back.status_code == 422 and error_code(back) == "invalid_assignee"


def test_t12_deleting_a_paper_keeps_its_tasks(owner, project_id):
    keep_paper = owner.upload(project_id, text_pdf("keep")).json()["id"]
    gone_paper = owner.upload(project_id, text_pdf("gone")).json()["id"]
    while Worker().run_once():  # a paper with a running parse cannot be deleted
        pass
    linked = [make(owner, project_id, f"gone {i}", paper_id=gone_paper) for i in range(2)]
    other = make(owner, project_id, "other", paper_id=keep_paper)
    free = make(owner, project_id, "free")

    assert owner.client.delete(api(project_id, f"/papers/{gone_paper}")).status_code == 204
    items = {t["id"]: t for t in listing(owner, project_id)["items"]}
    assert len(items) == 4
    for task in linked:
        assert items[task["id"]]["paper_id"] is None and items[task["id"]]["paper_title"] is None
    assert items[other["id"]]["paper_id"] == keep_paper
    assert items[free["id"]]["paper_id"] is None
    with get_engine().connect() as conn:
        payload = conn.execute(
            text("SELECT payload FROM research_events WHERE event_type = 'paper.deleted'")
        ).scalar_one()
    assert payload["task_count"] == 2


def test_t13_filters_and_counts(owner, project_id):
    member = add_member(owner, "member")
    paper = owner.upload(project_id, text_pdf("p")).json()["id"]
    make(owner, project_id, "a", assignee_id=member.user["id"], paper_id=paper)
    make(owner, project_id, "b", status="doing", assignee_id=owner.user["id"])
    make(owner, project_id, "c")
    done = patch(owner, project_id, make(owner, project_id, "d"), status="done").json()
    assert done["status"] == "done"

    def titles(**params):
        return sorted(t["title"] for t in listing(owner, project_id, **params)["items"])

    assert titles(status="todo") == ["a", "c"]
    assert titles(status=["todo", "doing"]) == ["a", "b", "c"]
    assert titles(assignee=member.user["id"]) == ["a"]
    assert titles(assignee="none") == ["c", "d"]
    assert titles(paper_id=paper) == ["a"]
    assert titles(status="todo", assignee="none") == ["c"]

    everything = {"todo": 2, "doing": 1, "blocked": 0, "done": 1, "cancelled": 0, "overdue": 0}
    filtered = listing(owner, project_id, status="doing")
    assert filtered["total"] == 1 and filtered["counts"] == everything
    paged = listing(owner, project_id, limit=2, offset=3)
    assert len(paged["items"]) == 1 and paged["total"] == 4 and paged["counts"] == everything
    assert listing(owner, project_id, limit=2)["items"][0]["title"] == "d"  # newest first

    for params in (
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"status": "nope"},
        {"assignee": "someone"},
        {"paper_id": "x"},
    ):
        response = owner.client.get(api(project_id, "/tasks"), params=params)
        assert response.status_code == 422, params
    assert listing(owner, project_id, limit=200)["limit"] == 200


# --- T14, T15: events and storage --------------------------------------------------------


def test_t14_events_say_what_happened_without_the_words(owner, project_id):
    title, reason = "SECRET-TITLE-9921", "SECRET-REASON-9921"
    task = make(owner, project_id, title, description="SECRET-DESC-9921")
    blocked = patch(owner, project_id, task, status="blocked", blocked_reason=reason).json()
    renamed = patch(owner, project_id, blocked, title=title + " v2").json()
    owner.client.delete(api(project_id, f"/tasks/{renamed['id']}"))

    events = owner.client.get(api(project_id, "/events")).json()
    task_events = sorted(
        (e for e in events if e["event_type"].startswith("task.")), key=lambda e: e["created_at"]
    )
    assert [e["event_type"] for e in task_events] == [
        "task.created",
        "task.updated",
        "task.updated",
        "task.deleted",
    ]
    created, to_blocked, rename, deleted = task_events
    assert created["payload"] == {"status": "todo", "paper_id": None}
    assert to_blocked["payload"] == {
        "status": "blocked",
        "paper_id": None,
        "changed": ["status", "blocked_reason"],
        "from": "todo",
        "to": "blocked",
    }
    assert rename["payload"] == {"status": "blocked", "paper_id": None, "changed": ["title"]}
    assert deleted["payload"] == {"status": "blocked", "paper_id": None}
    for event in task_events:
        assert event["actor_id"] == owner.user["id"]
        assert "SECRET" not in str(event)


def test_t15_tasks_are_read_back_and_the_database_refuses_bad_combinations(owner, project_id):
    made = make(owner, project_id, "durable", priority="low")
    with session_factory()() as db:
        row = db.get(Task, uuid.UUID(made["id"]))
        assert row is not None and row.title == "durable" and row.priority == "low"
        assert row.status == "todo" and row.version == 1

    ws, pid, uid = (uuid.UUID(x) for x in (owner.workspace_id, project_id, owner.user["id"]))

    def raw(**fields) -> Task:
        base = {"workspace_id": ws, "project_id": pid, "created_by": uid, "title": "t"}
        return Task(
            **{
                **base,
                "description": "",
                "status": "todo",
                "priority": "normal",
                "version": 1,
                **fields,
            }
        )

    now = datetime.now(UTC)
    bad = {
        "ck_task_blocked_reason": [
            {"status": "blocked"},
            {"status": "blocked", "blocked_reason": ""},
            {"status": "todo", "blocked_reason": "why"},
        ],
        "ck_task_completed": [{"status": "done"}, {"status": "todo", "completed_at": now}],
        "ck_task_status": [{"status": "weird"}],
        "ck_task_priority": [{"priority": "weird"}],
        "ck_task_title_length": [{"title": ""}, {"title": "x" * 201}],
        "ck_task_description_length": [{"description": "x" * 5001}],
    }
    for constraint, cases in bad.items():
        for fields in cases:
            with session_factory()() as db:
                db.add(raw(**fields))
                with pytest.raises(Exception, match=constraint):
                    db.commit()
    assert count_tasks() == 1
