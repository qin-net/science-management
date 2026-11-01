"""Workspace overview (specs/005 section 4, P1-P9). Real PostgreSQL, no mocks."""

from datetime import UTC, datetime, timedelta

from researchos.worker import Worker
from tests.conftest import Account, add_member, sign_up
from tests.pdf_samples import scanned_pdf, text_pdf


def url(account: Account) -> str:
    return f"/api/v1/workspaces/{account.workspace_id}/overview"


def overview(account: Account) -> dict:
    response = account.client.get(url(account))
    assert response.status_code == 200, response.text
    return response.json()


def task(account: Account, project_id: str, title: str = "t", **fields) -> dict:
    response = account.client.post(
        f"/api/v1/projects/{project_id}/tasks", json={"title": title, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


def move(account: Account, project_id: str, item: dict, **fields) -> dict:
    response = account.client.patch(
        f"/api/v1/projects/{project_id}/tasks/{item['id']}",
        json={"expected_version": item["version"], **fields},
    )
    assert response.status_code == 200, response.text
    return response.json()


def day(offset: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=offset)).isoformat()


def archive(account: Account, project_id: str) -> None:
    current = account.client.get(f"/api/v1/projects/{project_id}").json()
    done = account.client.patch(
        f"/api/v1/projects/{project_id}",
        json={"status": "archived", "expected_version": current["version"]},
    )
    assert done.status_code == 200, done.text


def card(data: dict, project_id: str) -> dict:
    return next(p for p in data["projects"] if p["id"] == project_id)


# --- P1, P7: who may read, and the empty case -------------------------------------------------


def test_p1_members_read_and_strangers_do_not(owner: Account):
    project = owner.new_project()["id"]
    task(owner, project, "mine", assignee_id=owner.user["id"])
    viewer, member, outsider = add_member(owner, "viewer"), add_member(owner, "member"), sign_up()
    assert outsider.client.get(url(owner)).status_code == 404

    seen = {name: overview(who) for name, who in (("viewer", viewer), ("member", member))}
    assert seen["viewer"]["totals"] == seen["member"]["totals"] == overview(owner)["totals"]
    assert seen["viewer"]["projects"] == seen["member"]["projects"]
    assert seen["viewer"]["recent_events"] == seen["member"]["recent_events"]
    # "My tasks" depends on the caller: the owner has one, the others have none.
    assert overview(owner)["my_tasks"]["total"] == 1
    assert seen["viewer"]["my_tasks"] == {"total": 0, "items": []}


def test_p7_an_empty_workspace_is_all_zeros(owner: Account):
    data = overview(owner)
    assert data["totals"] == {
        "active_projects": 0,
        "archived_projects": 0,
        "papers": 0,
        "open_tasks": 0,
        "overdue_tasks": 0,
    }
    assert data["projects"] == [] and data["recent_events"] == []
    assert data["my_tasks"] == {"total": 0, "items": []}
    assert data["today"] == day(0)


def test_p8_a_project_without_events_has_no_last_activity(owner: Account):
    from sqlalchemy import text

    from researchos.db import get_engine

    project = owner.new_project()["id"]
    assert card(overview(owner), project)["last_activity_at"] is not None  # project.created
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM research_events WHERE project_id = :p"), {"p": project})
    data = overview(owner)
    assert card(data, project)["last_activity_at"] is None
    assert data["recent_events"] == []


# --- P2, P3: counts match what is stored ---------------------------------------------------


def test_p2_counts_match_and_archived_projects_stay_out_of_the_totals(owner: Account):
    live = owner.new_project("Live")["id"]
    old = owner.new_project("Old")["id"]
    owner.upload(live, text_pdf("a"))
    owner.upload(live, text_pdf("b"))
    owner.upload(old, text_pdf("c"))

    task(owner, live, "todo")
    task(owner, live, "late", due_date=day(-1))
    doing = task(owner, live, "doing")
    move(owner, live, doing, status="doing")
    done = task(owner, live, "done", due_date=day(-5))
    move(owner, live, done, status="done")
    task(owner, old, "old open", due_date=day(-3))
    old_done = task(owner, old, "old done")
    move(owner, old, old_done, status="cancelled")
    archive(owner, old)

    data = overview(owner)
    assert data["totals"] == {
        "active_projects": 1,
        "archived_projects": 1,
        "papers": 3,
        "open_tasks": 3,  # todo, late, doing; nothing from the archived project
        "overdue_tasks": 1,
    }
    assert [p["name"] for p in data["projects"]] == ["Old", "Live"]  # newest project first
    assert card(data, live)["paper_count"] == 2
    assert card(data, live)["tasks"] == {
        "todo": 2,
        "doing": 1,
        "blocked": 0,
        "done": 1,
        "cancelled": 0,
        "overdue": 1,
    }
    # The archived project still shows its own numbers on its card.
    assert card(data, old)["status"] == "archived" and card(data, old)["paper_count"] == 1
    assert card(data, old)["tasks"] == {
        "todo": 1,
        "doing": 0,
        "blocked": 0,
        "done": 0,
        "cancelled": 1,
        "overdue": 1,
    }
    # The totals agree with the project page's own counts.
    page = owner.client.get(f"/api/v1/projects/{live}/tasks").json()["counts"]
    assert {k: page[k] for k in page} == card(data, live)["tasks"]


def test_p3_parsed_counts_only_papers_with_a_current_parse(owner: Account, upstream):
    project = owner.new_project()["id"]
    owner.upload(project, text_pdf("readable"))
    assert Worker().run_once() is True
    owner.upload(project, scanned_pdf())
    Worker().run_once()
    imported = owner.client.post(
        f"/api/v1/projects/{project}/papers/import", json={"doi": "10.1038/nature14539"}
    )
    assert imported.status_code == 201, imported.text  # metadata only, no PDF
    papers = owner.client.get(f"/api/v1/projects/{project}/papers").json()["items"]
    parsed = [p for p in papers if p["parse_job"] and p["parse_job"]["status"] == "succeeded"]
    assert len(parsed) == 1
    data = overview(owner)
    assert card(data, project)["paper_count"] == len(papers) == 3
    assert card(data, project)["parsed_paper_count"] == 1


# --- P4: my tasks -----------------------------------------------------------------------


def test_p4_my_tasks_are_mine_open_and_ordered(owner: Account):
    other = add_member(owner, "member")
    project = owner.new_project()["id"]
    me = owner.user["id"]
    task(owner, project, "no date", assignee_id=me)
    task(owner, project, "later", assignee_id=me, due_date=day(9))
    task(owner, project, "soon", assignee_id=me, due_date=day(1))
    task(owner, project, "late, recent", assignee_id=me, due_date=day(-1))
    task(owner, project, "late, older", assignee_id=me, due_date=day(-4))
    finished = task(owner, project, "finished", assignee_id=me, due_date=day(-9))
    move(owner, project, finished, status="done")
    dropped = task(owner, project, "dropped", assignee_id=me)
    move(owner, project, dropped, status="cancelled")
    blocked = task(owner, project, "blocked", assignee_id=me, due_date=day(3))
    move(owner, project, blocked, status="blocked", blocked_reason="r")
    task(owner, project, "unassigned", due_date=day(-2))
    task(owner, project, "theirs", assignee_id=other.user["id"], due_date=day(-2))

    mine = overview(owner)["my_tasks"]
    assert [t["title"] for t in mine["items"]] == [
        "late, older",
        "late, recent",
        "soon",
        "blocked",
        "later",
        "no date",
    ]
    assert mine["total"] == 6
    assert [t["overdue"] for t in mine["items"]] == [True, True, False, False, False, False]
    first = mine["items"][0]
    assert first["project_id"] == project and first["project_name"] == "Project"
    assert first["due_date"] == day(-4) and first["status"] == "todo"
    assert [t["title"] for t in overview(other)["my_tasks"]["items"]] == ["theirs"]


def test_p4_the_list_is_capped_but_the_total_is_real(owner: Account):
    project = owner.new_project()["id"]
    for i in range(23):
        task(owner, project, f"t{i}", assignee_id=owner.user["id"], due_date=day(i + 1))
    mine = overview(owner)["my_tasks"]
    assert mine["total"] == 23 and len(mine["items"]) == 20
    assert mine["items"][0]["title"] == "t0"


def test_p4_archived_projects_have_no_todo(owner: Account):
    project = owner.new_project()["id"]
    task(owner, project, "frozen", assignee_id=owner.user["id"], due_date=day(-1))
    assert overview(owner)["my_tasks"]["total"] == 1
    archive(owner, project)
    assert overview(owner)["my_tasks"] == {"total": 0, "items": []}


# --- P5, P6, P9: isolation, events, freshness -----------------------------------------------


def test_p5_other_workspaces_never_leak_in(owner: Account):
    stranger = sign_up()
    theirs = stranger.new_project("Theirs")["id"]
    task(stranger, theirs, "secret", assignee_id=stranger.user["id"], due_date=day(-1))
    stranger.upload(theirs, text_pdf("x"))
    mine = owner.new_project("Mine")["id"]

    data = overview(owner)
    assert [p["id"] for p in data["projects"]] == [mine]
    assert data["totals"]["papers"] == 0 and data["totals"]["overdue_tasks"] == 0
    assert data["my_tasks"]["total"] == 0
    assert all(e["project_id"] == mine for e in data["recent_events"])
    assert "secret" not in str(data) and "Theirs" not in str(data)


def test_p6_recent_events_are_newest_first_capped_and_wordless(owner: Account):
    project = owner.new_project("Visible project")["id"]
    for i in range(25):
        task(owner, project, f"SECRET-TITLE-{i}")
    note = owner.client.post(f"/api/v1/projects/{project}/notes", json={"body": "SECRET-NOTE-BODY"})
    assert note.status_code == 201

    events = overview(owner)["recent_events"]
    assert len(events) == 20
    assert events[0]["event_type"] == "note.created"
    stamps = [e["created_at"] for e in events]
    assert stamps == sorted(stamps, reverse=True)
    assert all(e["project_name"] == "Visible project" for e in events)
    assert all(e["actor_name"] == owner.user["display_name"] for e in events)
    assert "SECRET" not in str(events)


def test_p9_the_overview_is_read_fresh_every_time(owner: Account):
    project = owner.new_project()["id"]
    item = task(owner, project, "move me", assignee_id=owner.user["id"], due_date=day(-1))
    assert overview(owner)["totals"]["overdue_tasks"] == 1
    moved = move(owner, project, item, status="done")
    data = overview(owner)
    assert data["totals"]["overdue_tasks"] == 0 and data["totals"]["open_tasks"] == 0
    assert data["my_tasks"]["total"] == 0
    move(owner, project, moved, status="doing")
    assert overview(owner)["totals"]["overdue_tasks"] == 1
    assert owner.client.delete(f"/api/v1/projects/{project}/tasks/{item['id']}").status_code == 204
    assert card(overview(owner), project)["tasks"]["doing"] == 0
