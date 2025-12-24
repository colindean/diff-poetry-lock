import logging
import os
import sys
from abc import ABC
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseSettings, Field, PrivateAttr, ValidationError, validator

logger = logging.getLogger(__name__)


class PrLookupService(Protocol):
    def find_pr_for_branch(self, branch_ref: str) -> str: ...


@runtime_checkable
class PrLookupConfigurable(Protocol):
    def set_pr_lookup_service(self, service: PrLookupService) -> None: ...


class Settings(ABC):
    # from CI
    event_name: str
    ref: str
    repository: str
    base_ref: str
    pr_num: str

    # from step config including secrets
    token: str
    lockfile_path: str
    api_url: str

    sigil_envvar: ClassVar[str]
    """The envvar in this will always be present when this settings is valid."""

    @classmethod
    def matches_env(cls, env: dict[str, str]) -> bool:
        """Check whether this CI's identifying env var is present."""
        return any(key == cls.sigil_envvar for key in env)


class VelaSettings(BaseSettings, Settings):
    sigil_envvar: ClassVar[str] = "VELA_REPO_FULL_NAME"
    _pr_lookup_service: PrLookupService | None = PrivateAttr(default=None)
    _pr_num_cached: str = PrivateAttr(default="")

    # from CI
    event_name: str = Field(env="VELA_BUILD_EVENT")
    ref: str = Field(env="VELA_BUILD_REF")
    repository: str = Field(env="VELA_REPO_FULL_NAME")
    base_ref: str = Field(default="", env=None)  # Calculated from VELA_REPO_BRANCH in __init__

    # Helper field for calculation
    repo_branch: str = Field(env="VELA_REPO_BRANCH")

    # from step config including secrets
    token: str = Field(env="PARAMETER_GITHUB_TOKEN")
    lockfile_path: str = Field(env="PARAMETER_LOCKFILE_PATH", default="poetry.lock")
    api_url: str = Field(env="PARAMETER_GITHUB_API_URL", default="https://api.github.com")

    def __init__(self, **values: Any) -> None:  # noqa: ANN401
        super().__init__(**values)
        # Calculate base_ref from repo_branch
        self.base_ref = f"refs/heads/{self.repo_branch}"
        logger.debug("VelaSettings calculated base_ref=%s from repo_branch=%s", self.base_ref, self.repo_branch)
        logger.debug("VelaSettings ref=%s", self.ref)
        logger.debug("VelaSettings event_name=%s", self.event_name)

    def set_pr_lookup_service(self, service: PrLookupService) -> None:
        self._pr_lookup_service = service

    @property
    def pr_num(self) -> str:  # type: ignore[override]
        if self._pr_num_cached:
            return self._pr_num_cached

        if self._pr_lookup_service is None:
            logger.debug("PR lookup requested before service configured; returning empty string")
            return ""

        logger.debug("VelaSettings.pr_num looking up PR for branch %s", self.ref)
        pr_num = self._pr_lookup_service.find_pr_for_branch(self.ref)
        self._pr_num_cached = pr_num
        if pr_num:
            logger.debug("VelaSettings.pr_num found PR #%s", pr_num)
        else:
            logger.debug("VelaSettings.pr_num found no open PR")
        return pr_num


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
                logger.exception(str(e1))
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
    def pr_num(self) -> str:  # type: ignore[override]
        # TODO: Validate early
        return self.ref.split("/")[2]


_CI_SETTINGS_CANDIDATES: list[type[Settings]] = [GitHubActionsSettings, VelaSettings]


class CiNotImplemented(BaseException):
    def __init__(self) -> None:
        sigils = [candidate.sigil_envvar for candidate in _CI_SETTINGS_CANDIDATES]
        msg = f"Unable to determine CI environment. Your CI may be unsupported. Looked for {sigils}."
        super().__init__(msg)


def find_settings_for_environment() -> type[Settings] | None:
    env = dict(os.environ)
    return next((item for item in _CI_SETTINGS_CANDIDATES if item.matches_env(env)), None)


def determine_and_load_settings() -> Settings:
    if settings_type := find_settings_for_environment():
        try:
            return settings_type()
        except Exception:
            logger.exception("Error loading settings for %s", settings_type.__name__)
            raise

    raise CiNotImplemented
