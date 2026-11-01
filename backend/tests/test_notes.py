"""Notes and block-anchored annotations (specs/003 section 4, K1-K15). Real PostgreSQL and PDFs."""

import threading
import uuid

import pytest
from sqlalchemy import text

from researchos.db import get_engine
from researchos.models import Note
from researchos.worker import Worker
from tests.conftest import Account, add_member, sign_up
from tests.pdf_samples import text_pdf


def api(project_id: str, tail: str = "") -> str:
    return f"/api/v1/projects/{project_id}{tail}"


def parsed_paper(account: Account, project_id: str, marker: str = "Marker text", pages: int = 1):
    paper = account.upload(project_id, text_pdf(marker, pages=pages)).json()
    assert Worker().run_once() is True
    detail = account.client.get(api(project_id, f"/papers/{paper['id']}")).json()
    version_id = detail["current_version"]["id"]
    blocks = account.client.get(
        api(project_id, f"/papers/{paper['id']}/versions/{version_id}/blocks")
    ).json()["items"]
    assert blocks, "the sample must parse into blocks"
    return paper["id"], version_id, blocks


def annotate(account: Account, project_id: str, paper_id: str, version_id: str, block_id, body=""):
    return account.client.post(
        api(project_id, "/notes"),
        json={
            "body": body,
            "paper_id": paper_id,
            "anchor": {"version_id": version_id, "block_id": block_id},
        },
    )


def count_notes() -> int:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT count(*) FROM notes")).scalar_one()


@pytest.fixture
def setup(owner: Account):
    project = owner.new_project()
    paper_id, version_id, blocks = parsed_paper(owner, project["id"])
    return project["id"], paper_id, version_id, blocks


# --- K1, K2, K3, K4: who may do what -------------------------------------------------------


def test_k1_viewer_reads_but_cannot_write_and_outsider_sees_nothing(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    note = owner.client.post(api(project_id, "/notes"), json={"body": "mine"}).json()
    viewer = add_member(owner, "viewer")
    outsider = sign_up()

    listed = viewer.client.get(api(project_id, "/notes"))
    assert listed.status_code == 200 and [n["id"] for n in listed.json()["items"]] == [note["id"]]
    item = listed.json()["items"][0]
    assert item["editable"] is False and item["deletable"] is False
    assert viewer.client.post(api(project_id, "/notes"), json={"body": "x"}).status_code == 403
    patch = {"body": "y", "expected_version": 1}
    url = api(project_id, f"/notes/{note['id']}")
    assert viewer.client.patch(url, json=patch).status_code == 403
    assert viewer.client.delete(api(project_id, f"/notes/{note['id']}")).status_code == 403
    assert count_notes() == 1

    assert outsider.client.get(api(project_id, "/notes")).status_code == 404
    assert outsider.client.post(api(project_id, "/notes"), json={"body": "x"}).status_code == 404
    assert outsider.client.delete(api(project_id, f"/notes/{note['id']}")).status_code == 404
    assert count_notes() == 1


def test_k1_archived_project_is_readable_but_not_writable(owner: Account, setup):
    project_id, *_ = setup
    note = owner.client.post(api(project_id, "/notes"), json={"body": "before"}).json()
    project = owner.client.get(api(project_id)).json()
    archived = owner.client.patch(
        api(project_id), json={"status": "archived", "expected_version": project["version"]}
    )
    assert archived.status_code == 200, archived.text
    listed = owner.client.get(api(project_id, "/notes")).json()["items"]
    assert [n["id"] for n in listed] == [note["id"]]
    assert listed[0]["editable"] is False and listed[0]["deletable"] is False
    for call in (
        owner.client.post(api(project_id, "/notes"), json={"body": "x"}),
        owner.client.patch(
            api(project_id, f"/notes/{note['id']}"), json={"body": "x", "expected_version": 1}
        ),
        owner.client.delete(api(project_id, f"/notes/{note['id']}")),
    ):
        assert call.status_code == 409 and call.json()["error"]["code"] == "project_archived"
    assert count_notes() == 1


def test_k2_only_the_author_edits_and_authors_or_admins_delete(owner: Account):
    project = owner.new_project()["id"]
    author = add_member(owner, "member")
    other = add_member(owner, "member")
    note = author.client.post(api(project, "/notes"), json={"body": "from the author"}).json()
    assert note["editable"] is True and note["deletable"] is True
    url = api(project, f"/notes/{note['id']}")

    seen_by_other = other.client.get(api(project, "/notes")).json()["items"][0]
    assert seen_by_other["editable"] is False and seen_by_other["deletable"] is False
    seen_by_owner = owner.client.get(api(project, "/notes")).json()["items"][0]
    assert seen_by_owner["editable"] is False and seen_by_owner["deletable"] is True

    assert other.client.patch(url, json={"body": "x", "expected_version": 1}).status_code == 403
    assert owner.client.patch(url, json={"body": "x", "expected_version": 1}).status_code == 403
    assert other.client.delete(url).status_code == 403
    assert count_notes() == 1
    assert owner.client.delete(url).status_code == 204

    again = author.client.post(api(project, "/notes"), json={"body": "second"}).json()
    assert author.client.delete(api(project, f"/notes/{again['id']}")).status_code == 204
    assert count_notes() == 0


def test_k3_other_projects_objects_are_refused_and_never_listed(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    other_project = owner.new_project("Other")["id"]
    other_paper, other_version, other_blocks = parsed_paper(owner, other_project, "Other marker")

    cases = [
        (paper_id, other_version, other_blocks[0]["id"]),  # version of another project
        (other_paper, version_id, blocks[0]["id"]),  # paper of another project
        (paper_id, version_id, other_blocks[0]["id"]),  # block of another project
        (paper_id, other_version, blocks[0]["id"]),
    ]
    for paper, version, block in cases:
        response = annotate(owner, project_id, paper, version, block)
        assert response.status_code in (404, 422), response.text
    assert count_notes() == 0

    owner.client.post(api(other_project, "/notes"), json={"body": "elsewhere"})
    assert owner.client.get(api(project_id, "/notes")).json()["total"] == 0
    elsewhere = owner.client.get(api(other_project, "/notes")).json()["items"][0]
    cross = api(project_id, f"/notes/{elsewhere['id']}")
    assert owner.client.delete(cross).status_code == 404
    assert count_notes() == 1


def test_k4_the_client_cannot_supply_the_quoted_text_or_the_author(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    base = {
        "body": "x",
        "paper_id": paper_id,
        "anchor": {"version_id": version_id, "block_id": blocks[0]["id"]},
    }
    forged = [
        {**base, "anchor": {**base["anchor"], "text": "The paper says X"}},
        {**base, "anchor": {**base["anchor"], "page_no": 9}},
        {**base, "anchor": {**base["anchor"], "content_hash": "sha256:" + "0" * 64}},
        {**base, "author_id": str(uuid.uuid4())},
        {**base, "version": 7},
    ]
    for payload in forged:
        response = owner.client.post(api(project_id, "/notes"), json=payload)
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "validation_error"
    assert count_notes() == 0


# --- K5 to K9: anchors ---------------------------------------------------------------------


def test_k5_annotation_copies_the_block_and_is_located(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    block = next(b for b in blocks if "Marker text" in b["text"])
    response = annotate(owner, project_id, paper_id, version_id, block["id"], "check this")
    assert response.status_code == 201, response.text
    note = response.json()
    assert note["kind"] == "annotation" and note["author_id"] == owner.user["id"]
    anchor = note["anchor"]
    assert anchor["status"] == "located" and anchor["block_id"] == block["id"]
    assert anchor["text"] == block["text"] and anchor["page_no"] == block["page_no"]
    assert anchor["bbox"] == block["bbox"] and anchor["granularity"] == block["granularity"]
    assert anchor["version_id"] == version_id and anchor["version_no"] == 1
    with get_engine().connect() as conn:
        stored = conn.execute(text("SELECT anchor FROM notes")).scalar_one()
    assert stored["content_hash"] == block["content_hash"]
    assert stored["block_seq"] == block["seq"]

    empty = annotate(owner, project_id, paper_id, version_id, blocks[0]["id"], "")
    assert empty.status_code == 201, "an annotation may be a bare marker"


def test_k6_nothing_to_anchor_to_without_a_parse(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    for block_id in (str(uuid.uuid4()), blocks[0]["id"]):
        with get_engine().begin() as conn:
            conn.execute(text("UPDATE paper_versions SET current_parse_job_id = NULL"))
        response = annotate(owner, project_id, paper_id, version_id, block_id)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_anchor"
    assert count_notes() == 0

    # A paper imported by metadata has no version at all.
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM paper_versions")).scalar_one() == 1
    missing = annotate(owner, project_id, paper_id, str(uuid.uuid4()), blocks[0]["id"])
    assert missing.status_code == 404
    bare = owner.client.post(
        api(project_id, "/notes"),
        json={"body": "x", "anchor": {"version_id": version_id, "block_id": blocks[0]["id"]}},
    )
    assert bare.status_code == 422 and bare.json()["error"]["code"] == "invalid_anchor"


def reparse(owner: Account, project_id: str, paper_id: str, version_id: str):
    response = owner.client.post(api(project_id, f"/papers/{paper_id}/versions/{version_id}/parse"))
    assert response.status_code == 202, response.text
    assert Worker().run_once() is True


def test_k7_located_follows_a_reparse_and_goes_stale_when_the_text_changes(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    block = blocks[0]
    note = annotate(owner, project_id, paper_id, version_id, block["id"], "keep").json()

    reparse(owner, project_id, paper_id, version_id)
    fresh = owner.client.get(
        api(project_id, f"/papers/{paper_id}/versions/{version_id}/blocks")
    ).json()["items"]
    assert {b["id"] for b in fresh}.isdisjoint({b["id"] for b in blocks}), "new job, new blocks"
    # A block of the earlier parse is no longer something to anchor to.
    old = annotate(owner, project_id, paper_id, version_id, block["id"], "late")
    assert old.status_code == 422 and old.json()["error"]["code"] == "invalid_anchor"
    after = owner.client.get(api(project_id, "/notes")).json()["items"][0]["anchor"]
    assert after["status"] == "located"
    match = next(b for b in fresh if b["content_hash"] == block["content_hash"])
    assert after["block_id"] == match["id"] and after["bbox"] == match["bbox"]

    # What the page said when the note was written no longer matches any block on it. The parsers
    # here are deterministic, so the earlier text is put in place directly in the database.
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE notes SET anchor = jsonb_set(anchor, '{content_hash}', "
                "to_jsonb(CAST(:h AS text)))"
            ),
            {"h": "sha256:" + "ab" * 32},
        )
    stale = owner.client.get(api(project_id, "/notes")).json()["items"][0]["anchor"]
    assert stale["status"] == "stale"
    assert stale["block_id"] is None and stale["bbox"] is None
    assert stale["text"] == block["text"] and stale["page_no"] == block["page_no"]

    # The same hash on another page is not a match.
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE notes SET anchor = jsonb_set(anchor, '{content_hash}', "
                "to_jsonb(CAST(:h AS text)))"
            ),
            {"h": block["content_hash"]},
        )
        conn.execute(text("UPDATE notes SET anchor = jsonb_set(anchor, '{page_no}', '99')"))
    assert owner.client.get(api(project_id, "/notes")).json()["items"][0]["anchor"]["status"] == (
        "stale"
    )
    assert note["id"]


def test_k7_a_version_without_a_current_parse_is_unparsed(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    annotate(owner, project_id, paper_id, version_id, blocks[0]["id"], "x")
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE paper_versions SET current_parse_job_id = NULL"))
    anchor = owner.client.get(api(project_id, "/notes")).json()["items"][0]["anchor"]
    assert anchor["status"] == "unparsed"
    assert anchor["block_id"] is None and anchor["bbox"] is None and anchor["text"]


def test_k8_a_new_version_leaves_old_annotations_on_the_old_version(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    annotate(owner, project_id, paper_id, version_id, blocks[0]["id"], "v1 remark")
    upload = owner.client.post(
        api(project_id, f"/papers/{paper_id}/versions"),
        params={"filename": "v2.pdf"},
        content=text_pdf("Second edition"),
        headers={"Content-Type": "application/pdf"},
    )
    assert upload.status_code == 202, upload.text
    assert Worker().run_once() is True
    detail = owner.client.get(api(project_id, f"/papers/{paper_id}")).json()
    assert detail["current_version"]["version_no"] == 2
    anchor = owner.client.get(api(project_id, "/notes")).json()["items"][0]["anchor"]
    assert anchor["version_no"] == 1 and anchor["version_id"] == version_id
    assert anchor["status"] == "located"


def test_k9_a_repeated_text_on_one_page_resolves_to_the_first_block(owner: Account):
    project_id = owner.new_project()["id"]
    paper_id, version_id, blocks = parsed_paper(owner, project_id)
    block = blocks[0]
    note = annotate(owner, project_id, paper_id, version_id, block["id"], "dup").json()
    # Put a second block with the same page and hash in the current parse, after the first.
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO paper_blocks (id, workspace_id, project_id, paper_version_id, "
                "parse_job_id, seq, page_no, kind, text, bbox, granularity, content_hash) "
                "SELECT :new, workspace_id, project_id, paper_version_id, parse_job_id, "
                "seq + 1000, page_no, kind, text, bbox, granularity, content_hash "
                "FROM paper_blocks WHERE id = :old"
            ),
            {"new": str(uuid.uuid4()), "old": block["id"]},
        )
    anchor = owner.client.get(api(project_id, "/notes")).json()["items"][0]["anchor"]
    assert anchor["status"] == "located" and anchor["block_id"] == block["id"]
    assert note["id"]


# --- K10, K11, K12: editing ----------------------------------------------------------------


def test_k10_two_edits_from_the_same_version_cannot_both_win(owner: Account):
    project = owner.new_project()["id"]
    note = owner.client.post(api(project, "/notes"), json={"body": "start"}).json()
    url = api(project, f"/notes/{note['id']}")
    results = []
    barrier = threading.Barrier(2)

    def edit(body: str) -> None:
        barrier.wait()
        results.append((body, owner.client.patch(url, json={"body": body, "expected_version": 1})))

    threads = [threading.Thread(target=edit, args=(name,)) for name in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    codes = sorted(r.status_code for _, r in results)
    assert codes == [200, 409], codes
    winner = next(body for body, r in results if r.status_code == 200)
    loser = next(r for _, r in results if r.status_code == 409)
    assert loser.json()["error"]["code"] == "version_conflict"
    assert loser.json()["error"]["details"]["current_version"] == 2
    stored = owner.client.get(api(project, "/notes")).json()["items"][0]
    assert stored["body"] == winner and stored["version"] == 2

    stale = owner.client.patch(url, json={"body": "late", "expected_version": 1})
    assert stale.status_code == 409
    assert owner.client.get(api(project, "/notes")).json()["items"][0]["body"] == winner


def test_k11_patch_changes_the_body_and_nothing_else(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    note = annotate(owner, project_id, paper_id, version_id, blocks[0]["id"], "first").json()
    url = api(project_id, f"/notes/{note['id']}")
    for extra in (
        {"anchor": {"version_id": version_id, "block_id": blocks[0]["id"]}},
        {"paper_id": paper_id},
        {"author_id": str(uuid.uuid4())},
        {"version": 5},
    ):
        response = owner.client.patch(url, json={"body": "x", "expected_version": 1, **extra})
        assert response.status_code == 422, extra
    unchanged = owner.client.get(api(project_id, "/notes")).json()["items"][0]
    assert unchanged["body"] == "first" and unchanged["version"] == 1

    ok = owner.client.patch(url, json={"body": "  second\nline  ", "expected_version": 1})
    assert ok.status_code == 200
    edited = ok.json()
    assert edited["body"] == "second\nline" and edited["version"] == 2
    assert edited["anchor"]["text"] == note["anchor"]["text"]
    assert edited["anchor"]["block_id"] == note["anchor"]["block_id"]
    assert edited["updated_at"] >= note["updated_at"] and edited["created_at"] == note["created_at"]


@pytest.mark.parametrize(
    "body",
    ["", "   \n", "x" * 20001, "bad\x00byte", "bell\x07", "esc\x1b[0m"],
    ids=["empty", "blank", "too-long", "nul", "bell", "escape"],
)
def test_k12_invalid_bodies_are_refused_and_nothing_is_stored(owner: Account, body):
    project = owner.new_project()["id"]
    response = owner.client.post(api(project, "/notes"), json={"body": body})
    assert response.status_code == 422, response.text
    assert count_notes() == 0
    note = owner.client.post(api(project, "/notes"), json={"body": "ok"}).json()
    patch = owner.client.patch(
        api(project, f"/notes/{note['id']}"), json={"body": body, "expected_version": 1}
    )
    assert patch.status_code == 422
    assert owner.client.get(api(project, "/notes")).json()["items"][0]["body"] == "ok"


def test_k12_limits_are_inclusive_and_text_marks_survive(owner: Account):
    project = owner.new_project()["id"]
    longest = "界" * 20000
    assert owner.client.post(api(project, "/notes"), json={"body": longest}).status_code == 201
    tricky = "line one\n\tindented <img src=x onerror=alert(1)> & more"
    made = owner.client.post(api(project, "/notes"), json={"body": tricky}).json()
    assert made["body"] == tricky


def test_k12_the_database_refuses_what_the_api_would(owner: Account):
    project = owner.new_project()["id"]
    from researchos.db import session_factory  # noqa: PLC0415

    with session_factory()() as db:
        ws = uuid.UUID(owner.workspace_id)
        note = Note(
            workspace_id=ws,
            project_id=uuid.UUID(project),
            author_id=uuid.UUID(owner.user["id"]),
            body="",
        )
        db.add(note)
        with pytest.raises(Exception, match="ck_note_body_present"):
            db.commit()
        db.rollback()
        db.add(
            Note(
                workspace_id=ws,
                project_id=uuid.UUID(project),
                author_id=uuid.UUID(owner.user["id"]),
                body="x" * 20001,
            )
        )
        with pytest.raises(Exception, match="ck_note_body_length"):
            db.commit()


# --- K13, K14, K15: consistency ------------------------------------------------------------


def test_k13_deleting_a_paper_removes_its_notes_and_only_those(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    other_paper, other_version, other_blocks = parsed_paper(owner, project_id, "Another paper")
    annotate(owner, project_id, paper_id, version_id, blocks[0]["id"], "a")
    owner.client.post(api(project_id, "/notes"), json={"body": "b", "paper_id": paper_id})
    kept_annotation = annotate(
        owner, project_id, other_paper, other_version, other_blocks[0]["id"], "kept"
    ).json()
    kept_project = owner.client.post(api(project_id, "/notes"), json={"body": "kept too"}).json()
    assert count_notes() == 4

    assert owner.client.delete(api(project_id, f"/papers/{paper_id}")).status_code == 204
    remaining = {n["id"] for n in owner.client.get(api(project_id, "/notes")).json()["items"]}
    assert remaining == {kept_annotation["id"], kept_project["id"]}
    with get_engine().connect() as conn:
        payload = conn.execute(
            text("SELECT payload FROM research_events WHERE event_type = 'paper.deleted'")
        ).scalar_one()
    assert payload["note_count"] == 2

    assert owner.client.delete(api(project_id, f"/papers/{other_paper}")).status_code == 204
    with get_engine().connect() as conn:
        last = conn.execute(
            text(
                "SELECT payload FROM research_events WHERE event_type = 'paper.deleted' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert last["note_count"] == 1
    assert [n["kind"] for n in owner.client.get(api(project_id, "/notes")).json()["items"]] == [
        "project"
    ]


def test_k14_events_say_what_happened_without_the_words(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    secret = "SECRET-BODY-4417"
    note = annotate(owner, project_id, paper_id, version_id, blocks[0]["id"], secret).json()
    url = api(project_id, f"/notes/{note['id']}")
    owner.client.patch(url, json={"body": secret + " edited", "expected_version": 1})
    owner.client.delete(url)
    events = owner.client.get(api(project_id, "/events")).json()
    note_events = [e for e in events if e["event_type"].startswith("note.")]
    assert sorted(e["event_type"] for e in note_events) == [
        "note.created",
        "note.deleted",
        "note.updated",
    ]
    quoted = blocks[0]["text"]
    for event in note_events:
        assert event["payload"] == {"kind": "annotation", "paper_id": paper_id}
        assert event["actor_id"] == owner.user["id"]
        dumped = str(event)
        assert secret not in dumped and quoted not in dumped


def test_k15_notes_are_read_back_from_the_database(owner: Account, setup):
    from researchos.db import session_factory  # noqa: PLC0415

    project_id, paper_id, version_id, blocks = setup
    made = annotate(owner, project_id, paper_id, version_id, blocks[0]["id"], "durable").json()
    with session_factory()() as db:
        row = db.get(Note, uuid.UUID(made["id"]))
        assert row is not None and row.body == "durable" and row.version == 1
        assert row.anchor["text"] == blocks[0]["text"]
    fresh = sign_up()  # another process would see the same rows; a new session and cookie jar
    assert fresh.client.get(api(project_id, "/notes")).status_code == 404
    listed = owner.client.get(
        api(
            project_id,
            "/notes",
        )
    ).json()
    assert listed["total"] == 1 and listed["items"][0]["body"] == "durable"


def test_listing_filters_by_paper_orders_newest_first_and_pages(owner: Account, setup):
    project_id, paper_id, version_id, blocks = setup
    ids = []
    for index in range(5):
        payload = {"body": f"n{index}"}
        if index % 2 == 0:
            payload["paper_id"] = paper_id
        ids.append(owner.client.post(api(project_id, "/notes"), json=payload).json()["id"])
    everything = owner.client.get(api(project_id, "/notes")).json()
    assert [n["id"] for n in everything["items"]] == ids[::-1] and everything["total"] == 5
    for_paper = owner.client.get(api(project_id, "/notes"), params={"paper_id": paper_id}).json()
    assert [n["body"] for n in for_paper["items"]] == ["n4", "n2", "n0"]
    assert {n["kind"] for n in for_paper["items"]} == {"paper"}
    assert {n["paper_id"] for n in for_paper["items"]} == {paper_id}
    page = owner.client.get(api(project_id, "/notes"), params={"limit": 2, "offset": 4}).json()
    assert [n["body"] for n in page["items"]] == ["n0"] and page["total"] == 5
    assert owner.client.get(api(project_id, "/notes"), params={"limit": 0}).status_code == 422
    assert owner.client.get(api(project_id, "/notes"), params={"limit": 201}).status_code == 422
