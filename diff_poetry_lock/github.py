from enum import Enum
from urllib.parse import urlparse

import requests
from loguru import logger
from pydantic import BaseModel, Field, parse_obj_as
from requests import Response

from diff_poetry_lock.settings import PrLookupConfigurable, Settings
from diff_poetry_lock.utils import get_nested

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
        self._ref_hash_cache: dict[str, str] = {}
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
            headers=GithubApi.Headers.JSON.headers(self.s.token),
            json={"body": f"{MAGIC_COMMENT_IDENTIFIER}{comment}"},
            timeout=10,
        )
        logger.debug("Response status: {}", r.status_code)
        r.raise_for_status()

    def update_comment(self, comment_id: int, comment: str) -> None:
        logger.debug("Updating comment {}", comment_id)
        r = self.session.patch(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/comments/{comment_id}",
            headers=GithubApi.Headers.JSON.headers(self.s.token),
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
                headers=GithubApi.Headers.JSON.headers(self.s.token),
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
            headers=GithubApi.Headers.RAW.headers(self.s.token),
            timeout=10,
            stream=True,
        )
        logger.debug("Response status: {}", r.status_code)

        if r.status_code == 404:
            raise FileNotFoundError(self.s.lockfile_path) from RepoFileRetrievalError(self.s.repository, ref)
        r.raise_for_status()
        return r

    def resolve_commit_hashes(self, head_ref: str, base_ref: str) -> tuple[str, str]:
        cached_head_hash = self._ref_hash_cache.get(head_ref)
        cached_base_hash = self._ref_hash_cache.get(base_ref)
        if cached_head_hash and cached_base_hash:
            logger.debug("Using cached commit hashes for head_ref {} and base_ref {}", head_ref, base_ref)
            return cached_head_hash, cached_base_hash

        owner, repo_name = self.s.repository.split("/", maxsplit=1)
        query = (
            "query($owner:String!, $name:String!, $head:String!, $base:String!){"
            " repository(owner:$owner, name:$name){"
            "  head:ref(qualifiedName:$head){ target { ... on Commit { oid } } }"
            "  base:ref(qualifiedName:$base){ target { ... on Commit { oid } } }"
            " }"
            "}"
        )
        variables = {
            "owner": owner,
            "name": repo_name,
            "head": self._qualified_ref(head_ref),
            "base": self._qualified_ref(base_ref),
        }

        try:
            r = self.session.post(
                self.graphql_url(),
                headers=GithubApi.Headers.JSON.headers(self.s.token),
                json={"query": query, "variables": variables},
                timeout=10,
            )
            logger.debug("GraphQL response status: {}", r.status_code)
            r.raise_for_status()
            response_json = r.json()

            repo_data = response_json.get("data", {}).get("repository", {})
            resolved_head_hash = str(get_nested(repo_data, ("head", "target", "oid")) or "").strip()
            resolved_base_hash = str(get_nested(repo_data, ("base", "target", "oid")) or "").strip()
            if resolved_head_hash:
                self._ref_hash_cache[head_ref] = resolved_head_hash
            if resolved_base_hash:
                self._ref_hash_cache[base_ref] = resolved_base_hash

        except (requests.RequestException, ValueError, TypeError):
            logger.exception("Failed to resolve commit hashes via GraphQL")

        resolved_head_hash = self._ref_hash_cache.get(head_ref, head_ref)
        resolved_base_hash = self._ref_hash_cache.get(base_ref, base_ref)
        if resolved_head_hash == head_ref or resolved_base_hash == base_ref:
            logger.warning("Could not resolve one or more commit hashes, falling back to provided refs")
        return resolved_head_hash, resolved_base_hash

    def graphql_url(self) -> str:
        parsed = urlparse(self.s.api_url)
        if parsed.path.endswith("/api/v3"):
            graphql_path = f"{parsed.path.removesuffix('/api/v3')}/api/graphql"
            return f"{parsed.scheme}://{parsed.netloc}{graphql_path}"

        return f"{self.s.api_url.rstrip('/')}/graphql"

    @staticmethod
    def _qualified_ref(ref: str) -> str:
        if ref.startswith("refs/"):
            return ref
        return f"refs/heads/{ref}"

    def delete_comment(self, comment_id: int) -> None:
        logger.debug("Deleting comment {}", comment_id)
        r = self.session.delete(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/comments/{comment_id}",
            headers=GithubApi.Headers.JSON.headers(self.s.token),
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
            headers=GithubApi.Headers.JSON.headers(self.s.token),
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

    class Headers(Enum):
        """Enum for github api content types."""

        JSON = "application/vnd.github+json"
        RAW = "application/vnd.github.raw"

        def headers(self, token: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {token}", "Accept": self.value}

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
