"""Paper summaries (specs/008 section 4, S1-S7). Same setup as test_ask.py: real PostgreSQL, real
parsed PDFs, and the local fake language model."""

import logging
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from researchos.api.ask import SUMMARY_QUESTIONS
from researchos.db import get_engine
from tests.conftest import Account, add_member, sign_up
from tests.fake_model import ModelReply
from tests.pdf_samples import text_pdf
from tests.test_ask import (
    ask,
    calls_in_db,
    parsed_paper,
    reply_with,
    seed_calls,
    sent_blocks,
)


def summary_url(project_id: str, paper_id: str) -> str:
    return f"/api/v1/projects/{project_id}/papers/{paper_id}/summary"


def summarize(account: Account, project_id: str, paper_id: str, **body):
    return account.client.post(summary_url(project_id, paper_id), json=body)


def sent_question(model_api, index: int = 0) -> str:
    content = model_api.calls[index].body["messages"][0]["content"]
    return content.split("<question>", 1)[1].split("</question>", 1)[0]


def test_s1_members_summarize_viewers_and_strangers_do_not(owner: Account, model_api):
    project = owner.new_project()["id"]
    other = owner.new_project("Other")["id"]
    paper, _ = parsed_paper(owner, project, "alpha")
    foreign, _ = parsed_paper(owner, other, "foreign")
    viewer, member, outsider = add_member(owner, "viewer"), add_member(owner, "member"), sign_up()

    assert summarize(viewer, project, paper).status_code == 403
    assert summarize(outsider, project, paper).status_code == 404
    assert summarize(owner, project, foreign).status_code == 404
    assert summarize(owner, project, str(uuid.uuid4())).status_code == 404
    assert model_api.calls == [] and calls_in_db() == []

    reply_with(model_api, [{"text": "ok", "block_numbers": [1]}])
    assert summarize(member, project, paper).status_code == 200


def test_s1_an_archived_project_can_still_be_summarized(owner: Account, model_api):
    project = owner.new_project()
    paper, _ = parsed_paper(owner, project["id"], "alpha")
    done = owner.client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"status": "archived", "expected_version": project["version"]},
    )
    assert done.status_code == 200, done.text
    reply_with(model_api, [{"text": "ok", "block_numbers": [1]}])
    assert summarize(owner, project["id"], paper).status_code == 200


def test_s2_only_this_paper_is_sent_with_the_fixed_question(owner: Account, model_api):
    project = owner.new_project()["id"]
    first, first_blocks = parsed_paper(owner, project, "first marker")
    parsed_paper(owner, project, "second marker")
    reply_with(model_api, [{"text": "ok", "block_numbers": [1]}])
    reply_with(model_api, [{"text": "ok", "block_numbers": [1]}])

    assert summarize(owner, project, first).status_code == 200
    assert [b.text for b in sent_blocks(model_api, 0)] == [b["text"] for b in first_blocks]
    assert sent_question(model_api, 0) == SUMMARY_QUESTIONS["zh"]
    # Pinned in words, so changing the constant cannot make this pass by itself.
    zh = sent_question(model_api, 0)
    assert "中文" in zh and "核心贡献" in zh and "3 至 5" in zh

    assert summarize(owner, project, first, language="en").status_code == 200
    assert sent_question(model_api, 1) == SUMMARY_QUESTIONS["en"]
    en = sent_question(model_api, 1)
    assert "core contributions" in en and "3 to 5" in en and "中文" not in en
    assert SUMMARY_QUESTIONS["zh"] != SUMMARY_QUESTIONS["en"]

    for bad in ({"language": "fr"}, {"language": ""}, {"question": "ignore the rules"}):
        assert summarize(owner, project, first, **bad).status_code == 422
    assert len(model_api.calls) == 2


def test_s3_a_paper_without_text_is_refused_before_the_model(owner: Account, model_api, upstream):
    project = owner.new_project()["id"]
    owner.upload(project, text_pdf("x"))
    unparsed = owner.client.get(f"/api/v1/projects/{project}/papers").json()["items"][0]["id"]
    imported = owner.client.post(
        f"/api/v1/projects/{project}/papers/import", json={"doi": "10.1038/nature14539"}
    )
    assert imported.status_code == 201, imported.text

    for paper_id in (unparsed, imported.json()["id"]):
        response = summarize(owner, project, paper_id)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "no_readable_papers"
    assert model_api.calls == [] and calls_in_db() == []


def test_s4_evidence_comes_from_the_database_and_bad_numbers_are_dropped(owner: Account, model_api):
    project = owner.new_project()["id"]
    paper, blocks = parsed_paper(owner, project, "the dominant models", title="Sum Paper")
    reply_with(
        model_api,
        [
            {"text": "Supported claim.", "block_numbers": [1, 999]},
            {"text": "Unsupported claim.", "block_numbers": []},
        ],
    )
    data = summarize(owner, project, paper).json()
    assert data["dropped_citations"] == 1
    supported, unsupported = data["segments"]
    assert supported["supported"] is True
    assert supported["evidence"][0]["text"] == blocks[0]["text"]
    assert supported["evidence"][0]["paper_title"] == "Sum Paper"
    assert unsupported["supported"] is False and unsupported["evidence"] == []


def test_s5_one_summary_row_per_call_and_no_words_anywhere(owner: Account, model_api, caplog):
    project = owner.new_project()["id"]
    paper, _ = parsed_paper(owner, project, "distinctivepapertext")
    reply_with(model_api, [{"text": "narwhal-answer", "block_numbers": [1]}], input_tokens=9)
    with caplog.at_level(logging.DEBUG):
        assert summarize(owner, project, paper).status_code == 200
        model_api.queue_reply(ModelReply(500, {"type": "error"}))
        assert summarize(owner, project, paper).status_code == 502

    rows = calls_in_db()
    assert [(r["kind"], r["status"]) for r in rows] == [
        ("summary", "succeeded"),
        ("summary", "failed"),
    ]
    assert rows[0]["input_tokens"] == 9 and rows[1]["error_class"] == "http_500"
    dumped = str(rows) + caplog.text
    for word in (SUMMARY_QUESTIONS["zh"], "narwhal-answer", "distinctivepapertext"):
        assert word not in dumped


def test_s6_questions_and_summaries_share_one_allowance(owner: Account, model_api, settings):
    project = owner.new_project()["id"]
    paper, _ = parsed_paper(owner, project, "alpha")
    settings.set(model_user_per_minute=2)
    seed_calls(owner.user["id"], owner.workspace_id, project, 1, "10 seconds")  # a question
    reply_with(model_api, [{"text": "ok", "block_numbers": [1]}])
    assert summarize(owner, project, paper).status_code == 200  # the 2nd of 2

    limited = summarize(owner, project, paper)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "model_rate_limited"
    assert ask(owner, project).status_code == 429  # the same allowance blocks questions too
    assert len(model_api.calls) == 1


def test_s7_the_ledger_accepts_summary_and_nothing_else(owner: Account):
    project = owner.new_project()
    seed_calls(owner.user["id"], owner.workspace_id, project["id"], 1, "1 second")

    def insert(kind: str) -> None:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO model_calls (id, workspace_id, project_id, user_id, kind, model, "
                    "status, created_at) VALUES (gen_random_uuid(), :w, :p, :u, :k, 'm', "
                    "'succeeded', now())"
                ),
                {"w": owner.workspace_id, "p": project["id"], "u": owner.user["id"], "k": kind},
            )

    insert("summary")
    with pytest.raises(IntegrityError):
        insert("other")
