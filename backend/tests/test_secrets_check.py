"""Acceptance E5 in specs/000: the secret check catches what it claims to, in a real git repo."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

from tests.conftest import BACKEND_DIR

SCRIPT = BACKEND_DIR.parent / "scripts" / "check_secrets.py"
spec = importlib.util.spec_from_file_location("check_secrets", SCRIPT)
check_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_secrets)

# Assembled at runtime so this file does not trip the check it is testing.
FAKE_SECRETS = {
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "API key": "token = 'sk-" + "a1B2" * 8 + "'",
    "GitHub token": "ghp_" + "a1B2" * 9,
    "AWS access key": "AKIA" + "ABCD1234" * 2,
    "Hugging Face token": "hf_" + "a1B2" * 9,
    "database password": "postgresql+psycopg://admin:" + "hunter22" + "@db.internal/app",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".env\ndata/\n")
    return tmp_path


def scan(repo: Path) -> list[str]:
    return check_secrets.scan(repo, check_secrets.candidates(repo))


@pytest.mark.parametrize("label", FAKE_SECRETS)
def test_e5_secret_in_an_untracked_file_is_found(repo, label):
    (repo / "settings.py").write_text(f"x = 1\n{FAKE_SECRETS[label]}\n")
    assert scan(repo) == [f"settings.py:2: looks like a {label}"]


def test_e5_report_never_repeats_the_secret(repo):
    (repo / "settings.py").write_text(FAKE_SECRETS["API key"])
    assert "a1B2" not in "\n".join(scan(repo))


def test_e5_ignored_env_is_allowed_but_a_force_added_one_is_not(repo):
    (repo / ".env").write_text("DATABASE_URL=\n")
    assert scan(repo) == []
    subprocess.run(["git", "add", "-f", ".env"], cwd=repo, check=True)
    assert any(problem.startswith(".env: environment file") for problem in scan(repo))


@pytest.mark.parametrize(
    "name",
    [".env.local", "backend/.env", "deploy/server.pem", "data/pg/base", "samples/pdfs/a.pdf"],
)
def test_e5_forbidden_paths_are_reported(repo, name):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("harmless")
    # Forced, because the dangerous case is a file that got past .gitignore.
    subprocess.run(["git", "add", "-f", name], cwd=repo, check=True)
    assert any(problem.startswith(f"{name}:") for problem in scan(repo))


@pytest.mark.parametrize(
    "line",
    [
        "postgresql+psycopg://USER:PASSWORD@HOST:5432/researchos",
        'url = f"postgresql+psycopg://root:{password}@127.0.0.1:1/x"',
        "postgresql://app:$DB_PASSWORD@db/app",
        "postgresql+psycopg://user@localhost/researchos",
    ],
)
def test_e5_templates_are_not_reported(repo, line):
    (repo / "README.md").write_text(line)
    assert scan(repo) == []


def test_e5_example_env_is_allowed(repo):
    (repo / ".env.example").write_text("DATABASE_URL=\n")
    assert scan(repo) == []


def test_e5_this_repository_is_clean():
    root = BACKEND_DIR.parent
    assert check_secrets.scan(root, check_secrets.candidates(root)) == []
