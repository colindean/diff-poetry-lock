import requests
from loguru import logger
from pydantic import BaseModel, Field, parse_obj_as
from requests import Response

from diff_poetry_lock.settings import PrLookupConfigurable, Settings

MAGIC_COMMENT_IDENTIFIER = "<!-- posted by target/diff-poetry-lock -->\n\n"


class GithubComment(BaseModel):
    class GithubUser(BaseModel):
        id_: int = Field(alias="id")

    body: str
    id_: int = Field(alias="id")
    user: GithubUser

    def is_diff_comment(self) -> bool:
        return self.body.startswith(MAGIC_COMMENT_IDENTIFIER)


class RepoFileRetrievalError(BaseException):
    def __init__(self, repo: str, branch: str) -> None:
        msg = f"Error accessing a file in repo [{repo}] on branch [{branch}]"
        super().__init__(msg)


class GithubApi:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = requests.session()
        if isinstance(self.s, PrLookupConfigurable):
            self.s.set_pr_lookup_service(self)

    def post_comment(self, comment: str) -> None:
        if not comment:
            logger.info("No changes to lockfile detected")
            return

        if not self.s.pr_num:
            logger.warning("No PR number available; skipping comment post")
            return

        logger.debug("Posting comment to PR #{}", self.s.pr_num)
        r = self.session.post(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/{self.s.pr_num}/comments",
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
            json={"body": f"{MAGIC_COMMENT_IDENTIFIER}{comment}"},
            timeout=10,
        )
        logger.debug("Response status: {}", r.status_code)
        r.raise_for_status()

    def update_comment(self, comment_id: int, comment: str) -> None:
        logger.debug("Updating comment {}", comment_id)
        r = self.session.patch(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/comments/{comment_id}",
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
            json={"body": f"{MAGIC_COMMENT_IDENTIFIER}{comment}"},
            timeout=10,
        )
        logger.debug("Response status: {}", r.status_code)
        r.raise_for_status()

    def list_comments(self) -> list[GithubComment]:
        if not self.s.pr_num:
            logger.warning("No PR number available; returning empty comment list")
            return []

        logger.debug("Fetching comments for PR #{}", self.s.pr_num)
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
        logger.debug("Found %d comments", len(all_comments))
        return [c for c in all_comments if c.is_diff_comment()]

    def get_file(self, ref: str) -> Response:
        logger.debug("Fetching {} from ref {}", self.s.lockfile_path, ref)

        r = self.session.get(
            f"{self.s.api_url}/repos/{self.s.repository}/contents/{self.s.lockfile_path}",
            params={"ref": ref},
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github.raw"},
            timeout=10,
            stream=True,
        )
        logger.debug("Response status: {}", r.status_code)

        if r.status_code == 404:
            raise FileNotFoundError(self.s.lockfile_path) from RepoFileRetrievalError(self.s.repository, ref)
        r.raise_for_status()
        return r

    def delete_comment(self, comment_id: int) -> None:
        logger.debug("Deleting comment {}", comment_id)
        r = self.session.delete(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/comments/{comment_id}",
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
        )
        logger.debug("Response status: {}", r.status_code)
        r.raise_for_status()

    def find_pr_for_branch(self, branch_ref: str) -> str:
        """Find open PR number for a given branch ref (e.g., 'refs/heads/deps-update').
        Returns PR number as string, or empty string if not found."""
        branch = branch_ref.replace("refs/heads/", "")
        logger.debug("Looking for open PR for branch {}", branch)

        org = self.s.repository.split("/")[0]
        head = f"{org}:{branch}"

        r = self.session.get(
            f"{self.s.api_url}/repos/{self.s.repository}/pulls",
            params={"head": head, "state": "open"},
            headers={"Authorization": f"token {self.s.token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        logger.debug("Response status: {}", r.status_code)
        r.raise_for_status()

        pulls = r.json()
        if pulls and len(pulls) > 0:
            pr_num = str(pulls[0]["number"])
            logger.debug("Found open PR #{}", pr_num)
            return pr_num

        logger.debug("No open PR found for branch {}", branch)
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
