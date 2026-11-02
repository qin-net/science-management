"""Citation export (specs/006 section 4, E1-E10). Real PostgreSQL; files are read back with
independent parsers (bibtexparser, rispy), not with the code that wrote them."""

import re
import uuid

import bibtexparser
import rispy
from sqlalchemy import text

from researchos.db import get_engine
from tests.conftest import Account, add_member, sign_up
from tests.pdf_samples import text_pdf


def export_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/papers/export"


def new_paper(account: Account, project_id: str, **fields) -> str:
    """A paper with the given metadata; abstract and venue have no PATCH, so they go to the DB."""
    uploaded = account.upload(project_id, text_pdf(uuid.uuid4().hex))
    assert uploaded.status_code == 202, uploaded.text
    paper_id = uploaded.json()["id"]
    direct = {k: fields.pop(k) for k in ("abstract", "venue") if k in fields}
    patch = {"title": None, "authors": [], **fields}
    done = account.client.patch(f"/api/v1/projects/{project_id}/papers/{paper_id}", json=patch)
    assert done.status_code == 200, done.text
    if direct:
        sets = ", ".join(f"{k} = :{k}" for k in direct)
        with get_engine().begin() as conn:
            conn.execute(
                text(f"UPDATE papers SET {sets} WHERE id = :id"), {**direct, "id": paper_id}
            )
    return paper_id


def bib(account: Account, project_id: str, **params):
    response = account.client.get(export_url(project_id), params={"format": "bibtex", **params})
    assert response.status_code == 200, response.text
    library = bibtexparser.parse_string(response.text)
    assert not library.failed_blocks, response.text
    return response, library.entries


def ris(account: Account, project_id: str, **params):
    response = account.client.get(export_url(project_id), params={"format": "ris", **params})
    assert response.status_code == 200, response.text
    return response, rispy.loads(response.text)


def field(entry, name: str) -> str:
    return entry.fields_dict[name].value


FULL = {
    "title": "Attention Is All You Need",
    "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
    "year": 2017,
    "doi": "10.5555/3295222.3295349",
    "arxiv_id": "1706.03762",
    "venue": "NeurIPS",
    "abstract": "The dominant sequence transduction models are based on recurrent networks.",
}


# --- E1: who may export ---------------------------------------------------------------------


def test_e1_members_export_and_strangers_do_not(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, **FULL)
    for role in ("viewer", "member"):
        who = add_member(owner, role)
        assert len(bib(who, project)[1]) == 1
        assert len(ris(who, project)[1]) == 1
    outsider = sign_up()
    assert outsider.client.get(export_url(project), params={"format": "bibtex"}).status_code == 404
    unknown = owner.client.get(export_url(str(uuid.uuid4())), params={"format": "bibtex"})
    assert unknown.status_code == 404


def test_e1_an_archived_project_can_still_be_exported(owner: Account):
    project = owner.new_project()
    new_paper(owner, project["id"], **FULL)
    done = owner.client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"status": "archived", "expected_version": project["version"]},
    )
    assert done.status_code == 200, done.text
    assert len(bib(owner, project["id"])[1]) == 1


# --- E2, E3: the fields survive a round trip through an independent parser -----------------


def test_e2_bibtex_reads_back_with_every_field(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, **FULL)
    response, entries = bib(owner, project)
    entry = entries[0]
    assert entry.entry_type == "misc"
    assert field(entry, "title") == "{Attention Is All You Need}"
    assert field(entry, "author") == "Ashish Vaswani and Noam Shazeer and Niki Parmar"
    assert field(entry, "year") == "2017"
    assert field(entry, "howpublished") == "NeurIPS"
    assert field(entry, "doi") == "10.5555/3295222.3295349"
    assert field(entry, "eprint") == "1706.03762"
    assert field(entry, "archiveprefix") == "arXiv"
    assert field(entry, "url") == "https://doi.org/10.5555/3295222.3295349"
    assert field(entry, "abstract") == FULL["abstract"]
    assert entry.key == "vaswani2017attention"


def test_e3_ris_reads_back_with_every_field(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, **FULL)
    new_paper(owner, project, title="Second paper", authors=["Ada Lovelace"], year=1843)
    response, records = ris(owner, project)
    assert len(records) == 2
    newest, full = records  # newest first
    assert newest["primary_title"] == "Second paper" and newest["authors"] == ["Ada Lovelace"]
    assert full["type_of_reference"] == "GEN"
    assert full["primary_title"] == "Attention Is All You Need"
    assert full["authors"] == FULL["authors"]
    assert full["year"] == "2017"
    assert full["secondary_title"] == "NeurIPS"
    assert full["doi"] == "10.5555/3295222.3295349"
    assert full["accession_number"] == "1706.03762"
    assert full["urls"] == ["https://doi.org/10.5555/3295222.3295349"]
    assert full["notes_abstract"] == FULL["abstract"]


# --- E4: nothing a source says can change the file's structure ------------------------------

NASTY = {
    "title": "Braces } and { @article{evil, title = {x}}\nER  - \nTY  - GEN",
    "authors": ["A} B", "C\\D {", "李雷", "José Álvarez"],
    "abstract": (
        "line1\r\nTY  - JOUR\nER  - \n& % _ $ # \\ ^ ~ } { <img src=x onerror=alert(1)> 数据"
    ),
}


def test_e4_hostile_text_cannot_end_a_record_or_start_another(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, year=2020, **NASTY)
    new_paper(owner, project, **FULL)

    response, entries = bib(owner, project)
    assert len(entries) == 2 and {e.entry_type for e in entries} == {"misc"}
    nasty = next(e for e in entries if "evil" in field(e, "title"))
    assert "李雷" in field(nasty, "author") and "José Álvarez" in field(nasty, "author")
    assert "<img src=x onerror=alert(1)>" in field(nasty, "abstract")  # text, kept as text
    # Braces and backslashes are written as commands, so no raw one can unbalance the record.
    for name in ("title", "author", "abstract"):
        value = field(nasty, name)
        if name == "title":  # the outer pair keeps the capitalisation
            assert value.startswith("{") and value.endswith("}")
            value = value[1:-1]
        bare = re.sub(r"\\text[a-z]+\{\}", "", value)
        assert "{" not in bare and "}" not in bare, (name, value)

    response, records = ris(owner, project)
    assert len(records) == 2
    assert response.text.count("ER  - ") == 2 and response.text.count("TY  - ") == 2
    odd = next(r for r in records if "evil" in r["primary_title"])
    assert odd["primary_title"] == "Braces } and { @article{evil, title = {x}} ER - TY - GEN"
    assert odd["authors"] == NASTY["authors"]
    assert "TY - JOUR" in odd["notes_abstract"]  # folded into the abstract line, not a new record


def test_e4_a_doi_with_braces_does_not_break_bibtex(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, title="Odd DOI", doi="10.1234/a}b{c\\d")
    new_paper(owner, project, title="Fine one", authors=["X Y"])
    response, entries = bib(owner, project)
    assert len(entries) == 2
    odd = next(e for e in entries if field(e, "title") == "{Odd DOI}")
    assert "doi" not in odd.fields_dict
    assert "}" not in field(odd, "url") and "{" not in field(odd, "url")
    assert "10.1234/a%7Db%7Bc%5Cd" in field(odd, "url")
    # RIS keeps the stored value, which has no structure to break.
    _, records = ris(owner, project)
    assert next(r for r in records if r["primary_title"] == "Odd DOI")["doi"] == "10.1234/a}b{c\\d"


# --- E5: missing means missing --------------------------------------------------------------


def test_e5_missing_fields_are_left_out_and_titleless_papers_are_skipped(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, title="Only a title")
    new_paper(owner, project, title=None)  # an upload with no usable title
    response, entries = bib(owner, project)
    assert response.headers["X-Export-Count"] == "1" and response.headers["X-Export-Skipped"] == "1"
    assert set(entries[0].fields_dict) == {"title"}
    for junk in ("None", "Unknown", "{}", "null", "nan"):
        assert junk not in response.text
    response, records = ris(owner, project)
    assert set(records[0]) == {"type_of_reference", "primary_title"}
    assert response.text == "TY  - GEN\r\nT1  - Only a title\r\nER  - \r\n"


def test_e5_nothing_to_export_is_an_error_not_an_empty_file(owner: Account):
    empty = owner.new_project()["id"]
    for fmt in ("bibtex", "ris"):
        response = owner.client.get(export_url(empty), params={"format": fmt})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "nothing_to_export"
    untitled = owner.new_project()["id"]
    new_paper(owner, untitled, title=None)
    response = owner.client.get(export_url(untitled), params={"format": "bibtex"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "nothing_to_export"


# --- E6: cite keys --------------------------------------------------------------------------


def test_e6_cite_keys_are_unique_plain_and_stable(owner: Account):
    project = owner.new_project()["id"]
    for _ in range(3):
        new_paper(owner, project, title="Deep Learning", authors=["Yann LeCun"], year=2015)
    new_paper(owner, project, title="Deep Learning Again", authors=["LeCun, Yann"], year=2015)
    new_paper(owner, project, title="No authors, no year")
    new_paper(owner, project, title="Übergrößen", authors=["José Álvarez"], year=2001)
    new_paper(owner, project, title="数据", authors=["李雷"], year=2002)
    new_paper(owner, project, title="!!", authors=["Zed"], year=2003)

    _, first = bib(owner, project)
    keys = [e.key for e in first]
    assert len(keys) == len(set(keys)) == 8
    assert all(k and k.isascii() and k.isalnum() and k == k.lower() for k in keys)
    assert (
        keys.count("anonndauthors") == 1 and "alvarez2001ubergroen" in keys
    )  # ß has no ASCII form
    assert "anon2002paper" in keys and "zed2003paper" in keys
    assert sorted(k for k in keys if k.startswith("lecun2015deep")) == [
        "lecun2015deep",
        "lecun2015deepa",
        "lecun2015deepb",
        "lecun2015deepc",
    ]
    _, again = bib(owner, project)
    assert [e.key for e in again] == keys  # same data, same keys


# --- E7, E8: choosing papers, and nothing from elsewhere ------------------------------------


def test_e7_paper_ids_choose_and_a_foreign_id_refuses_everything(owner: Account):
    project = owner.new_project()["id"]
    other = owner.new_project("Other")["id"]
    a = new_paper(owner, project, title="Alpha", authors=["A A"])
    b = new_paper(owner, project, title="Beta", authors=["B B"])
    new_paper(owner, project, title="Gamma", authors=["C C"])
    foreign = new_paper(owner, other, title="Elsewhere")

    response, entries = bib(owner, project, paper_id=[a, b])
    assert [field(e, "title") for e in entries] == ["{Beta}", "{Alpha}"]
    assert response.headers["X-Export-Count"] == "2"

    refused = owner.client.get(
        export_url(project), params={"format": "bibtex", "paper_id": [a, foreign]}
    )
    assert refused.status_code == 404 and "Alpha" not in refused.text
    missing = owner.client.get(
        export_url(project), params={"format": "ris", "paper_id": [str(uuid.uuid4())]}
    )
    assert missing.status_code == 404


def test_e7_bad_parameters_are_422(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, title="Alpha")
    url = export_url(project)
    assert owner.client.get(url).status_code == 422
    assert owner.client.get(url, params={"format": "endnote"}).status_code == 422
    assert owner.client.get(url, params={"format": "ris", "paper_id": "nope"}).status_code == 422


def test_e7_more_than_the_limit_is_refused(owner: Account):
    project = owner.new_project()
    workspace_id, project_id, user_id = owner.workspace_id, project["id"], owner.user["id"]
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO papers (id, workspace_id, project_id, title, authors, "
                "metadata_provenance, created_by, created_at, updated_at) "
                "SELECT gen_random_uuid(), :w, :p, 'Paper ' || n, '[]'::jsonb, '{}'::jsonb, "
                ":u, now(), now() FROM generate_series(1, 501) AS n"
            ),
            {"w": workspace_id, "p": project_id, "u": user_id},
        )
    response = owner.client.get(export_url(project_id), params={"format": "bibtex"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "too_many"
    some = owner.client.get(
        export_url(project_id), params={"format": "bibtex", "paper_id": [str(uuid.uuid4())]}
    )
    assert some.status_code == 404


def test_e8_other_projects_and_workspaces_never_leak_in(owner: Account):
    project = owner.new_project()["id"]
    mine = new_paper(owner, project, title="Mine")
    sibling = owner.new_project("Sibling")["id"]
    new_paper(owner, sibling, title="Sibling paper")
    stranger = sign_up()
    theirs = stranger.new_project("Theirs")["id"]
    new_paper(stranger, theirs, title="Stranger paper")

    response, entries = bib(owner, project)
    assert [field(e, "title") for e in entries] == ["{Mine}"]
    assert "Sibling" not in response.text and "Stranger" not in response.text
    cross = stranger.client.get(export_url(theirs), params={"format": "bibtex", "paper_id": [mine]})
    assert cross.status_code == 404 and "Mine" not in cross.text


# --- E9: headers and line endings -----------------------------------------------------------


def test_e9_headers_and_line_endings(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, **FULL)
    response, _ = bib(owner, project)
    assert response.headers["content-type"] == "application/x-bibtex; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="references.bib"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-export-count"] == "1" and response.headers["x-export-skipped"] == "0"
    assert "\r" not in response.text

    response, _ = ris(owner, project)
    assert response.headers["content-type"] == "application/x-research-info-systems; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="references.ris"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text.count("\r\n") == response.text.count("\n")
    assert response.text.endswith("ER  - \r\n")


# --- E10: links come from identifiers, never from a source's URL ----------------------------


def test_e10_links_are_built_from_identifiers_only(owner: Account):
    project = owner.new_project()["id"]
    a = new_paper(owner, project, title="With arXiv only", arxiv_id="2101.00001")
    new_paper(owner, project, title="No identifiers")
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE papers SET source_links = CAST(:l AS jsonb) WHERE project_id = :p"),
            {
                "l": '{"open_access_url": "javascript:alert(1)", "landing_url": "data:x,y"}',
                "p": project,
            },
        )
    response, entries = bib(owner, project)
    assert "javascript" not in response.text and "data:" not in response.text
    by_title = {field(e, "title"): e for e in entries}
    assert field(by_title["{With arXiv only}"], "url") == "https://arxiv.org/abs/2101.00001"
    assert "url" not in by_title["{No identifiers}"].fields_dict
    _, records = ris(owner, project)
    assert {r["primary_title"]: r.get("urls") for r in records} == {
        "With arXiv only": ["https://arxiv.org/abs/2101.00001"],
        "No identifiers": None,
    }
    assert a  # the id was used above


def test_e6_a_suffix_never_lands_on_another_papers_own_key(owner: Account):
    project = owner.new_project()["id"]
    new_paper(owner, project, title="Deep Learning", authors=["Yann LeCun"], year=2015)
    new_paper(owner, project, title="Deep Nets", authors=["Yann LeCun"], year=2015)
    # Newest first, so this one is keyed before the two above and takes "lecun2015deepa" itself.
    new_paper(owner, project, title="Deepa Rao", authors=["Yann LeCun"], year=2015)
    _, entries = bib(owner, project)
    keys = [e.key for e in entries]
    assert len(keys) == len(set(keys)) == 3
    assert keys[0] == "lecun2015deepa"
