"""Workspace overview (specs/005 section 4, P1-P9). Real PostgreSQL, no mocks."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from researchos.db import get_engine
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
        "parsed_papers": 0,
        "noted_papers": 0,
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
        "parsed_papers": 0,  # uploaded but never parsed: the worker did not run
        "noted_papers": 0,
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


# --- R1-R5: papers that were processed or have notes (specs/009) -------------------------------


def paper_note(account: Account, project_id: str, paper_id: str, body: str = "n") -> None:
    response = account.client.post(
        f"/api/v1/projects/{project_id}/notes", json={"body": body, "paper_id": paper_id}
    )
    assert response.status_code == 201, response.text


def new_paper(account: Account, project_id: str, marker: str) -> str:
    uploaded = account.upload(project_id, text_pdf(marker))
    assert uploaded.status_code == 202, uploaded.text
    return uploaded.json()["id"]


def test_r1_noted_papers_are_counted_once_each_and_parsing_is_separate(owner: Account):
    project = owner.new_project()["id"]
    parsed = new_paper(owner, project, "parsed")
    assert Worker().run_once() is True
    unparsed = new_paper(owner, project, "unparsed")  # no worker run
    new_paper(owner, project, "untouched")
    paper_note(owner, project, parsed, "one")
    paper_note(owner, project, parsed, "two")
    paper_note(owner, project, unparsed, "three")

    data = overview(owner)
    assert card(data, project)["noted_paper_count"] == 2  # two papers, three notes
    assert data["totals"]["noted_papers"] == 2
    assert data["totals"]["parsed_papers"] == 1


def test_r2_project_notes_do_not_count_as_noted_papers(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, "a")
    owner.client.post(f"/api/v1/projects/{project}/notes", json={"body": "about the project"})
    data = overview(owner)
    assert card(data, project)["noted_paper_count"] == 0
    assert data["totals"]["noted_papers"] == 0


def test_r3_archived_projects_still_count_their_papers(owner: Account):
    project = owner.new_project()["id"]
    paper = new_paper(owner, project, "a")
    assert Worker().run_once() is True
    paper_note(owner, project, paper)
    archive(owner, project)
    data = overview(owner)
    assert data["totals"]["parsed_papers"] == 1 and data["totals"]["noted_papers"] == 1
    assert card(data, project)["noted_paper_count"] == 1


def test_r4_other_workspaces_are_not_counted(owner: Account):
    stranger = sign_up()
    theirs = stranger.new_project()["id"]
    paper = new_paper(stranger, theirs, "theirs")
    assert Worker().run_once() is True
    paper_note(stranger, theirs, paper)
    data = overview(owner)
    assert data["totals"]["parsed_papers"] == 0 and data["totals"]["noted_papers"] == 0


def test_r5_deleting_a_paper_takes_its_notes_out_of_the_count(owner: Account):
    project = owner.new_project()["id"]
    paper = new_paper(owner, project, "a")
    assert Worker().run_once() is True  # a paper with a parse still queued cannot be deleted
    paper_note(owner, project, paper)
    assert overview(owner)["totals"]["noted_papers"] == 1
    assert owner.client.delete(f"/api/v1/projects/{project}/papers/{paper}").status_code == 204
    data = overview(owner)
    assert data["totals"]["noted_papers"] == 0 and card(data, project)["noted_paper_count"] == 0


# --- T1-T5: activity per day -------------------------------------------------------------------


def test_t1_an_empty_workspace_still_has_fourteen_zero_days(owner: Account):
    data = overview(owner)
    days = data["activity"]
    assert len(days) == 14
    assert days[-1]["date"] == data["today"] == day(0)
    assert [d["date"] for d in days] == [day(offset) for offset in range(-13, 1)]
    assert all(
        d[k] == 0 for d in days for k in ("total", "papers", "notes", "tasks", "projects")
    )


def test_t2_todays_events_are_split_by_kind(owner: Account):
    project = owner.new_project()["id"]  # project.created
    paper = new_paper(owner, project, "a")  # paper.imported, paper.parse_queued
    paper_note(owner, project, paper)  # note.created
    task(owner, project, "one")
    task(owner, project, "two")  # task.created x2
    today = overview(owner)["activity"][-1]
    assert today["projects"] == 1 and today["papers"] == 2
    assert today["notes"] == 1 and today["tasks"] == 2
    assert today["total"] == 6


def set_event_age(days_ago: int) -> None:
    """Move every event of the test database to a given number of days ago, at midday UTC."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE research_events SET created_at = "
                "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
                "- make_interval(days => :d) + interval '12 hours'"
            ),
            {"d": days_ago},
        )


def test_t3_the_window_is_fourteen_utc_days(owner: Account):
    project = owner.new_project()["id"]
    for days_ago, expected_total in ((1, 1), (13, 1), (14, 0)):
        set_event_age(days_ago)
        days = overview(owner)["activity"]
        assert sum(d["total"] for d in days) == expected_total, days_ago
        if expected_total:
            assert days[-1 - days_ago]["projects"] == 1
    assert project


def test_t3_events_just_either_side_of_utc_midnight_land_on_different_days(owner: Account):
    owner.new_project()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE research_events SET created_at = "
                "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
                "- interval '1 second'"
            )
        )
    days = overview(owner)["activity"]
    assert days[-2]["projects"] == 1 and days[-1]["projects"] == 0


def test_t4_other_workspaces_events_are_not_counted(owner: Account):
    stranger = sign_up()
    stranger.new_project()
    # `workspace.created` has no project and belongs to no counted kind, so it never shows up.
    assert all(d["total"] == 0 for d in overview(owner)["activity"])


def test_t5_activity_carries_numbers_and_no_words(owner: Account):
    project = owner.new_project("SECRET-PROJECT")["id"]
    task(owner, project, "SECRET-TASK")
    days = overview(owner)["activity"]
    assert "SECRET" not in str(days)
    assert set(days[0]) == {"date", "total", "papers", "notes", "tasks", "projects"}
