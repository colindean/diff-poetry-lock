import sys
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseSettings, Field, PrivateAttr, ValidationError, root_validator, validator
from pydantic.errors import PydanticValueError


class Settings(ABC):
    # from CI
    event_name: str
    ref: str
    repository: str
    base_ref: str

    # from step config including secrets
    token: str
    lockfile_path: str
    api_url: str

    sigil_envvar: ClassVar[str]
    """The envvar in this will always be present when this settings is valid."""

    @property
    @abstractmethod
    def pr_num(self) -> str | None:
        """Return PR number if one exists."""


_PR_NUM_UNSET = object()
# Sentinel so we can distinguish "not looked up yet" from "looked up but no PR found".


class VelaSettings(BaseSettings, Settings):
    sigil_envvar: ClassVar[str] = "VELA_REPO_FULL_NAME"
    _pr_num_cached: str | None | object = PrivateAttr(default_factory=lambda: _PR_NUM_UNSET)

    # from CI
    event_name: str = Field(env="VELA_BUILD_EVENT")
    ref: str = Field(env="VELA_BUILD_REF")
    repository: str = Field(env="VELA_REPO_FULL_NAME")
    base_ref: str = Field(default="", env=None)  # Calculated from VELA_REPO_BRANCH compute_base_ref

    # Helper field for calculation
    repo_branch: str = Field(env="VELA_REPO_BRANCH")

    # from step config including secrets
    token: str = Field(env="PARAMETER_GITHUB_TOKEN")
    lockfile_path: str = Field(env="PARAMETER_LOCKFILE_PATH", default="poetry.lock")
    api_url: str = Field(env="PARAMETER_GITHUB_API_URL", default="https://api.github.com")

    @root_validator(skip_on_failure=True)
    def compute_base_ref(cls, values: dict[str, Any]) -> dict[str, Any]:
        repo_branch = values.get("repo_branch")
        if repo_branch:
            values["base_ref"] = f"refs/heads/{repo_branch}"
            print(f"[DEBUG VelaSettings] Calculated base_ref: {values['base_ref']} from repo_branch: {repo_branch}")
        else:
            values["base_ref"] = ""
        print(f"[DEBUG VelaSettings] ref: {values.get('ref')}")
        print(f"[DEBUG VelaSettings] event_name: {values.get('event_name')}")
        return values

    @property
    def pr_num(self) -> str | None:
        """Lazy PR lookup with cached result."""
        if self._pr_num_cached is _PR_NUM_UNSET:
            from diff_poetry_lock.github import GithubApi

            print(f"[DEBUG VelaSettings.pr_num] Looking up PR for branch: {self.ref}")
            try:
                api = GithubApi(self)
                cached = api.find_pr_for_branch(self.ref) or None
                self._pr_num_cached = cached
                if cached:
                    print(f"[DEBUG VelaSettings.pr_num] Found PR #{cached}")
                else:
                    print("[DEBUG VelaSettings.pr_num] No open PR found")
            except Exception as e:
                print(f"[DEBUG VelaSettings.pr_num] Error looking up PR: {e}")
                self._pr_num_cached = None

        return self._pr_num_cached if isinstance(self._pr_num_cached, str) else None


class GitHubActionsSettings(BaseSettings, Settings):
    sigil_envvar: ClassVar[str] = "github_repository"

    # from CI
    event_name: str = Field(env="github_event_name")  # must be 'pull_request'
    ref: str = Field(env="github_ref")
    repository: str = Field(env="github_repository")
    base_ref: str = Field(env="github_base_ref")

    # from step config including secrets
    token: str = Field(env="input_github_token")
    lockfile_path: str = Field(env="input_lockfile_path", default="poetry.lock")
    api_url: str = Field(env="github_api_url", default="https://api.github.com")

    def __init__(self, **values: Any) -> None:  # noqa: ANN401
        try:
            super().__init__(**values)
        except ValidationError as ex:
            if e1 := next(e.exc for e in ex.raw_errors if e.loc_tuple() == ("event_name",)):  # type: ignore[union-attr]
                # event_name is not 'pull_request' - we fail early
                print(str(e1), file=sys.stderr)
                sys.exit(0)
            raise

    @validator("event_name")
    @classmethod
    def event_must_be_pull_request(cls, v: str) -> str:
        if v != "pull_request":
            msg = "This Github Action can only be run in the context of a pull request"
            raise ValueError(msg)
        return v

    @property
    # todo: Avoid this MyPy error by having Pydantic compute the field
    def pr_num(self) -> str:
        # TODO: Validate early
        return self.ref.split("/")[2]


_CI_SETTINGS_CANDIDATES: list[type[Settings]] = [VelaSettings, GitHubActionsSettings]


class CiNotImplemented(BaseException):
    def __init__(self) -> None:
        sigils = [candidate.sigil_envvar for candidate in _CI_SETTINGS_CANDIDATES]
        msg = f"Unable to determine CI environment. Your CI may be unsupported. Looked for {sigils}."
        super().__init__(msg)


def find_settings_for_environment() -> type[Settings] | None:
    import os

    env_keys_lower = {key.lower() for key in os.environ}

    def valid(item: type[Settings]) -> bool:
        sigil_var = getattr(item, "sigil_envvar", "")
        if sigil_var and sigil_var.lower() not in env_keys_lower:
            return False

        try:
            item()
            return True  # noqa: TRY300
        except PydanticValueError:
            return False

    return next((item for item in _CI_SETTINGS_CANDIDATES if valid(item)), None)


def determine_and_load_settings() -> Settings:
    if settings_type := find_settings_for_environment():
        try:
            return settings_type()
        except Exception as e:
            print(f"[DEBUG] Error loading settings: {e}")
            print(f"[DEBUG] Settings type: {settings_type}")
            import traceback
            traceback.print_exc()
            raise

    raise CiNotImplemented
