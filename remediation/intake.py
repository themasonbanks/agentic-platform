"""Validate review deliveries before adding work to the local durable queue."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import PurePosixPath
import re
import sqlite3
from typing import Protocol


@dataclass(frozen=True)
class Review:
    head_sha: str
    head_repository: str
    branch: str
    open: bool
    commit_sha: str
    author_id: int
    author_is_bot: bool
    path: str
    line: int | None
    body: str
    outdated: bool
    resolved: bool


class ReviewSource(Protocol):
    def current_review(self, repository: str, pr: int, comment: int) -> Review:
        """Fetch the current PR, comment and thread from the authenticated API."""


def disposition(review: Review) -> str:
    path = PurePosixPath(review.path)
    if path.is_absolute() or ".." in path.parts or "\\" in review.path:
        return "unsafe_path"
    if not review.path or review.line is None or review.line < 1:
        return "manual_review"
    sensitive = {".github", ".codex", "terraform", "infrastructure", "migrations"}
    if sensitive.intersection(path.parts) or re.search(
        r"(?i)(auth|permission|secret|credential|policy|security)", review.path
    ):
        return "security_review"
    if re.search(
        r"(?i)(ignore.{0,30}instructions|exfiltrat|curl\s|wget\s|eval\(|"
        r"security|vulnerab|credential|secret|privilege)", review.body
    ):
        return "manual_review"
    return "candidate"


class Intake:
    def __init__(self, db: sqlite3.Connection, secret: bytes,
                 repositories: dict[str, int], bot_id: int, source: ReviewSource):
        if len(secret) < 32 or bot_id <= 0 or not repositories:
            raise ValueError("Explicit webhook secret, bot identity and repositories required")
        self.db, self.secret = db, secret
        self.repositories, self.bot_id, self.source = dict(repositories), bot_id, source
        db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                repository TEXT NOT NULL, pr INTEGER NOT NULL,
                sha TEXT NOT NULL, comment INTEGER NOT NULL,
                path TEXT NOT NULL, line INTEGER,
                status TEXT NOT NULL, reason TEXT NOT NULL,
                UNIQUE(repository, pr, sha, comment)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, job INTEGER NOT NULL,
                repository TEXT NOT NULL, pr INTEGER NOT NULL,
                status TEXT NOT NULL, deadline INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_running_per_pr
                ON attempts(repository, pr) WHERE status = 'running';
        """)

    def receive(self, event: str, signature: str, body: bytes) -> str:
        if len(body) > 1_048_576:
            raise ValueError("Delivery too large")
        expected = "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid signature")
        if event != "pull_request_review_comment":
            return "ignored_event"
        payload = json.loads(body)
        if payload.get("action") not in {"created", "edited"}:
            return "ignored_action"
        repository = payload["repository"]["full_name"]
        if self.repositories.get(repository) != payload.get("installation", {}).get("id"):
            return "ignored_repository"
        pr, comment = payload["pull_request"]["number"], payload["comment"]["id"]
        if type(pr) is not int or pr <= 0 or type(comment) is not int or comment <= 0:
            raise ValueError("Invalid review identifiers")
        review = self.source.current_review(repository, pr, comment)
        if review.author_id != self.bot_id or not review.author_is_bot:
            return "ignored_author"
        if not review.open or review.outdated or review.resolved:
            return "ignored_stale"
        if not re.fullmatch(r"[0-9a-f]{40}", review.head_sha):
            raise ValueError("Invalid head SHA")
        if review.commit_sha != review.head_sha or review.head_repository != repository:
            return "ignored_stale"
        if review.branch in {"main", "master"}:
            return "manual_branch"
        reason = disposition(review)
        status = "queued" if reason == "candidate" else "needs_human"
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO jobs(repository,pr,sha,comment,path,line,status,reason) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (repository, pr, review.head_sha, comment, review.path,
                 review.line, status, reason),
            )
        return status if cursor.rowcount else "duplicate"

    def claim_dry_run(self, job: int, now: int, timeout: int = 300) -> dict | None:
        """Reserve one bounded dry run; expired reservations require human recovery."""
        if not 1 <= timeout <= 300:
            raise ValueError("Dry-run timeout must be between one and 300 seconds")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT repository,pr,sha,comment,path,line,status FROM jobs WHERE id=?",
                (job,),
            ).fetchone()
            if not row or row[6] != "queued":
                self.db.rollback()
                return None
            repository, pr, sha, comment, path, line, _ = row
            review = self.source.current_review(repository, pr, comment)
            if (not review.open or review.outdated or review.resolved
                    or review.head_sha != sha or review.commit_sha != sha
                    or review.head_repository != repository
                    or review.author_id != self.bot_id or not review.author_is_bot
                    or review.path != path or review.line != line
                    or review.branch in {"main", "master"}
                    or disposition(review) != "candidate"):
                self.db.execute("UPDATE jobs SET status='needs_human',reason='changed_review' WHERE id=?", (job,))
                self.db.commit()
                return None
            attempts = self.db.execute(
                "SELECT status,deadline FROM attempts WHERE repository=? AND pr=?",
                (repository, pr),
            ).fetchall()
            if len(attempts) >= 3 or any(s == "running" and d <= now for s, d in attempts):
                self.db.execute("UPDATE jobs SET status='needs_human',reason='retry_or_lease_limit' WHERE id=?", (job,))
                self.db.commit()
                return None
            if any(s == "running" for s, _ in attempts):
                self.db.rollback()
                return None
            cursor = self.db.execute(
                "INSERT INTO attempts(job,repository,pr,status,deadline) VALUES(?,?,?,'running',?)",
                (job, repository, pr, now + timeout),
            )
            self.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
            self.db.commit()
            return {"attempt": cursor.lastrowid, "repository": repository, "pr": pr,
                    "branch": review.branch,
                    "head_sha": sha, "comment": comment, "path": path, "line": line,
                    "deadline": now + timeout, "mode": "dry_run", "spend_limit": 0}
        except BaseException:
            self.db.rollback()
            raise

    def finish_dry_run(self, attempt: int, now: int, successful: bool) -> None:
        with self.db:
            row = self.db.execute(
                "SELECT job,deadline FROM attempts WHERE id=? AND status='running'", (attempt,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown or finished attempt")
            job, deadline = row
            status = "dry_run_complete" if successful and now < deadline else "needs_human"
            self.db.execute("UPDATE attempts SET status=? WHERE id=?", (status, attempt))
            self.db.execute("UPDATE jobs SET status=?,reason=? WHERE id=?", (status, status, job))
