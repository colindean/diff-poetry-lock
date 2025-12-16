import requests
from pydantic import BaseModel, Field, parse_obj_as
from requests import Response

from diff_poetry_lock.settings import Settings

MAGIC_COMMENT_IDENTIFIER = "<!-- posted by Github Action nborrmann/diff-poetry-lock -->\n\n"
MAGIC_BOT_USER_ID = 41898282


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
            print("No changes to lockfile detected")
            return

        url = f"{self.s.api_url}/repos/{self.s.repository}/issues/{self.s.pr_num}/comments"
        print(f"[DEBUG] Posting comment to: {url}")
        print(f"[DEBUG] PR number: {self.s.pr_num}")
        print(f"[DEBUG] Repository: {self.s.repository}")
        print(f"[DEBUG] API URL: {self.s.api_url}")
        print(f"[DEBUG] Token present: {'Yes' if self.s.token else 'No'}")
        print(f"[DEBUG] Comment length: {len(comment)} chars")
        
        r = self.session.post(
            url,
            headers={"Authorization": f"Bearer {self.s.token}", "Accept": "application/vnd.github+json"},
            json={"body": f"{MAGIC_COMMENT_IDENTIFIER}{comment}"},
            timeout=10,
        )
        print(f"[DEBUG] Response status code: {r.status_code}")
        print(f"[DEBUG] Response text: {r.text[:200]}")
        r.raise_for_status()
        print("[DEBUG] Comment posted successfully")

    def update_comment(self, comment_id: int, comment: str) -> None:
        r = self.session.patch(
            f"{self.s.api_url}/repos/{self.s.repository}/issues/comments/{comment_id}",
            headers={"Authorization": f"Bearer {self.s.token}", "Accept": "application/vnd.github+json"},
            json={"body": f"{MAGIC_COMMENT_IDENTIFIER}{comment}"},
            timeout=10,
        )
        r.raise_for_status()

    def list_comments(self) -> list[GithubComment]:
        all_comments, comments, page = [], None, 1
        while comments is None or len(comments) == 100:
            r = self.session.get(
                f"{self.s.api_url}/repos/{self.s.repository}/issues/{self.s.pr_num}/comments",
                params={"per_page": 100, "page": page},
                headers={"Authorization": f"Bearer {self.s.token}", "Accept": "application/vnd.github+json"},
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
            headers={"Authorization": f"Bearer {self.s.token}", "Accept": "application/vnd.github.raw"},
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
            headers={"Authorization": f"Bearer {self.s.token}", "Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()

    def find_pr_for_branch(self, branch_ref: str) -> str:
        """Find open PR number for a given branch ref (e.g., 'refs/heads/deps-update').
        Returns PR number as string, or empty string if not found."""
        # Extract branch name from ref
        branch = branch_ref.replace("refs/heads/", "")
        print(f"[DEBUG find_pr_for_branch] Looking for PR with head branch: {branch}")
        
        # Get organization from repository (owner/repo)
        org = self.s.repository.split("/")[0]
        head = f"{org}:{branch}"
        
        # Query GitHub API for open PRs with this head branch
        url = f"{self.s.api_url}/repos/{self.s.repository}/pulls"
        params = {"head": head, "state": "open"}
        print(f"[DEBUG find_pr_for_branch] API URL: {url}")
        print(f"[DEBUG find_pr_for_branch] Params: {params}")
        
        r = self.session.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.s.token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        print(f"[DEBUG find_pr_for_branch] Response status: {r.status_code}")
        r.raise_for_status()
        
        pulls = r.json()
        print(f"[DEBUG find_pr_for_branch] Found {len(pulls)} open PR(s)")
        
        if pulls and len(pulls) > 0:
            pr_num = str(pulls[0]["number"])
            print(f"[DEBUG find_pr_for_branch] Using PR #{pr_num}")
            return pr_num
        
        print("[DEBUG find_pr_for_branch] No open PR found")
        return ""

    def upsert_comment(self, existing_comment: GithubComment | None, comment: str | None) -> None:
        if existing_comment is None and comment is None:
            return

        if existing_comment is None and comment is not None:
            print("Posting diff to new comment.")
            self.post_comment(comment)

        elif existing_comment is not None and comment is None:
            print("Deleting existing comment.")
            self.delete_comment(existing_comment.id_)

        elif existing_comment is not None and comment is not None:
            if existing_comment.body == f"{MAGIC_COMMENT_IDENTIFIER}{comment}":
                print("Content did not change, not updating existing comment.")
            else:
                print("Updating existing comment.")
                self.update_comment(existing_comment.id_, comment)
