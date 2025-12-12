import sys
from abc import ABC
from typing import Any

from pydantic import BaseSettings, Field, ValidationError, validator


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


class VelaSettings(BaseSettings, Settings):
    # from CI
    event_name: str = Field(env="VELA_BUILD_EVENT")  # must be 'pull_request'
    ref: str = Field(env="VELA_BUILD_REF")
    repository: str = Field(env="VELA_REPO_FULL_NAME")
    base_ref: str = Field(env="VELA_BUILD_BASE_REF")
    pr_num: str = Field(env="VELA_BUILD_PULL_REQUEST")

    # from step config including secrets
    token: str = Field(env="PARAMETER_GITHUB_TOKEN")
    lockfile_path: str = Field(env="PARAMETER_LOCKFILE_PATH", default="poetry.lock")
    api_url: str = Field(env="PARAMETER_GITHUB_API_URL", default="https://api.github.com")


class GitHubActionsSettings(BaseSettings, Settings):
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
    def pr_num(self) -> str:  # type: ignore[override]
        # TODO: Validate early
        return self.ref.split("/")[2]
