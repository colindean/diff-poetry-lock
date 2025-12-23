import logging

import requests
from pydantic import BaseModel, Field, parse_obj_as
from requests import Response

from diff_poetry_lock.settings import Settings

MAGIC_COMMENT_IDENTIFIER = "<!-- posted by Github Action nborrmann/diff-poetry-lock -->\n\n"
MAGIC_BOT_USER_ID = 41898282

logger = logging.getLogger(__name__)


class GithubComment(BaseModel):
    class GithubUser(BaseModel):
        id_: int = Field(alias="id")

    body: str
    id_: int = Field(alias="id")
    user: GithubUser

    def is_bot_comment(self) -> bool:
        return self.body.startswith(MAGIC_COMMENT_IDENTIFIER) and self.user.id_ == MAGIC_BOT_USER_ID


class RepoFileRetrievalError(BaseException):
    def __init__(self, repo: str, branch: str) -> None:
        msg = f"Error accessing a file in repo [{repo}] on branch [{branch}]"
        super().__init__(msg)


class GithubApi:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = requests.session()

    def post_comment(self, comment: str) -> None:
        if not comment:
            logger.info("No changes to lockfile detected")
            return

        if not self.s.pr_num:
            logger.debug("No PR number available; skipping comment post")
            return

        url = f"{self.s.api_url}/repos/{self.s.repository}/issues/{self.s.pr_num}/comments"
        logger.debug("Posting comment to %s", url)
        logger.debug("PR number: %s", self.s.pr_num)
        logger.debug("Repository: %s", self.s.repository)
        logger.debug("API URL: %s", self.s.api_url)
        logger.debug("Token present: %s", "Yes" if self.s.token else "No")
        logger.debug("Comment length: %s chars", len(comment))
        
        r = self.session.post(
            url,
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
            json={"body": f"{MAGIC_COMMENT_IDENTIFIER}{comment}"},
            timeout=10,
        )
        logger.debug("Response status code: %s", r.status_code)
        logger.debug("Response text: %s", r.text[:200])
        r.raise_for_status()
        logger.debug("Comment posted successfully")

    def update_comment(self, comment_id: int, comment: str) -> None:
        r = self.session.patch(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/comments/{comment_id}",
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
            json={"body": f"{MAGIC_COMMENT_IDENTIFIER}{comment}"},
            timeout=10,
        )
        r.raise_for_status()

    def list_comments(self) -> list[GithubComment]:
        if not self.s.pr_num:
            logger.debug("No PR number available; returning empty comment list")
            return []

        all_comments, comments, page = [], None, 1
        while comments is None or len(comments) == 100:
            r = self.session.get(
                f"{self.s.api_url}/repos/{self.s.repository}/issues/{self.s.pr_num}/comments",
                params={"per_page": 100, "page": page},
                headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
                timeout=10,
            )
            r.raise_for_status()
            comments = parse_obj_as(list[GithubComment], r.json())
            all_comments.extend(comments)
            page += 1
        return [c for c in all_comments if c.is_bot_comment()]

    def get_file(self, ref: str) -> Response:
        r = self.session.get(
            f"{self.s.api_url}/repos/{self.s.repository}/contents/{self.s.lockfile_path}",
            params={"ref": ref},
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github.raw"},
            timeout=10,
            stream=True,
        )
        if r.status_code == 404:
            raise FileNotFoundError(self.s.lockfile_path) from RepoFileRetrievalError(self.s.repository, ref)
        r.raise_for_status()
        return r

    def delete_comment(self, comment_id: int) -> None:
        r = self.session.delete(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/comments/{comment_id}",
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()

    def find_pr_for_branch(self, branch_ref: str) -> str:
        """Find open PR number for a given branch ref (e.g., 'refs/heads/deps-update').
        Returns PR number as string, or empty string if not found."""
        # Extract branch name from ref
        branch = branch_ref.replace("refs/heads/", "")
        logger.debug("[find_pr_for_branch] Looking for PR with head branch %s", branch)
        
        # Get organization from repository (owner/repo)
        org = self.s.repository.split("/")[0]
        head = f"{org}:{branch}"
        
        # Query GitHub API for open PRs with this head branch
        url = f"{self.s.api_url}/repos/{self.s.repository}/pulls"
        params = {"head": head, "state": "open"}
        logger.debug("[find_pr_for_branch] API URL: %s", url)
        logger.debug("[find_pr_for_branch] Params: %s", params)
        logger.debug("[find_pr_for_branch] Token length: %d", len(self.s.token) if self.s.token else 0)
        logger.debug("[find_pr_for_branch] Token type check: Bearer token present: %s", bool(self.s.token))
        
        r = self.session.get(
            url,
            params=params,
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        logger.debug("[find_pr_for_branch] Response status: %s", r.status_code)
        logger.debug("[find_pr_for_branch] Response headers: %s", dict(r.headers))
        logger.debug("[find_pr_for_branch] Response body: %s", r.text[:500] if r.status_code >= 400 else "OK")
        r.raise_for_status()
        
        pulls = r.json()
        logger.debug("[find_pr_for_branch] Found %s open PR(s)", len(pulls))
        
        if pulls and len(pulls) > 0:
            pr_num = str(pulls[0]["number"])
            logger.debug("[find_pr_for_branch] Using PR #%s", pr_num)
            return pr_num
        
        logger.debug("[find_pr_for_branch] No open PR found")
        return ""

    def upsert_comment(self, existing_comment: GithubComment | None, comment: str | None) -> None:
        if existing_comment is None and comment is None:
            return

        if existing_comment is None and comment is not None:
            logger.info("Posting diff to new comment.")
            self.post_comment(comment)

        elif existing_comment is not None and comment is None:
            logger.info("Deleting existing comment.")
            self.delete_comment(existing_comment.id_)

        elif existing_comment is not None and comment is not None:
            if existing_comment.body == f"{MAGIC_COMMENT_IDENTIFIER}{comment}":
                logger.debug("Content did not change, not updating existing comment.")
            else:
                logger.info("Updating existing comment.")
                self.update_comment(existing_comment.id_, comment)
