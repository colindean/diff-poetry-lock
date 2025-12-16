import sys
import tempfile
from operator import attrgetter
from pathlib import Path

import pydantic
from poetry.core.packages.package import Package
from poetry.packages import Locker

from diff_poetry_lock.github import GithubApi
from diff_poetry_lock.settings import Settings, determine_and_load_settings


def load_packages(filename: Path = Path("poetry.lock")) -> list[Package]:
    l_merged = Locker(Path(filename), local_config={})
    return l_merged.locked_repository().packages


@pydantic.dataclasses.dataclass(config={"arbitrary_types_allowed": True})
class PackageSummary:
    name: str
    old_version: str | None = None
    new_version: str | None = None

    def not_changed(self) -> bool:
        return self.new_version == self.old_version

    def changed(self) -> bool:
        return not self.not_changed()

    def updated(self) -> bool:
        return self.new_version is not None and self.old_version is not None and self.changed()

    def added(self) -> bool:
        return self.new_version is not None and self.old_version is None

    def removed(self) -> bool:
        return self.new_version is None and self.old_version is not None

    def summary_line(self) -> str:
        if self.updated():
            return f"Updated **{self.name}** ({self.old_version} -> {self.new_version})"
        if self.added() and self.new_version is not None:
            return f"Added **{self.name}** ({self.new_version})"
        if self.removed() and self.old_version is not None:
            return f"Removed **{self.name}** ({self.old_version})"

        if self.new_version is None:
            msg = "Inconsistent State"
            raise ValueError(msg)

        return f"Not changed **{self.name}** ({self.new_version})"


def diff(old_packages: list[Package], new_packages: list[Package]) -> list[PackageSummary]:
    merged: dict[str, PackageSummary] = {}
    for package in old_packages:
        merged[package.pretty_name] = PackageSummary(name=package.pretty_name, old_version=package.full_pretty_version)
    for package in new_packages:
        if package.pretty_name not in merged:
            merged[package.pretty_name] = PackageSummary(
                name=package.pretty_name,
                new_version=package.full_pretty_version,
            )
        else:
            merged[package.pretty_name].new_version = package.full_pretty_version

    return list(merged.values())


def post_comment(api: GithubApi, comment: str | None) -> None:
    existing_comments = api.list_comments()

    if len(existing_comments) > 1:
        print("Found more than one existing comment, only updating first comment", file=sys.stderr)

    existing_comment = existing_comments[0] if existing_comments else None
    api.upsert_comment(existing_comment, comment)


def format_comment(packages: list[PackageSummary]) -> str | None:
    added = sorted([p for p in packages if p.added()], key=attrgetter("name"))
    removed = sorted([p for p in packages if p.removed()], key=attrgetter("name"))
    updated = sorted([p for p in packages if p.updated()], key=attrgetter("name"))
    not_changed = sorted([p for p in packages if p.not_changed()], key=attrgetter("name"))

    if len(added + removed + updated) == 0:
        return None

    comment = f"### Detected {len(added + removed + updated)} changes to dependencies in Poetry lockfile\n\n"
    comment += "\n".join(p.summary_line() for p in added + removed + updated)
    comment += (
        f"\n\n*({len(added)} added, {len(removed)} removed, {len(updated)} updated, {len(not_changed)} not changed)*"
    )

    return comment


def load_lockfile(api: GithubApi, ref: str) -> list[Package]:
    r = api.get_file(ref)
    with tempfile.NamedTemporaryFile(mode="wb", delete=True) as f:
        for chunk in r.iter_content(chunk_size=1024):
            f.write(chunk)
        f.flush()

        return load_packages(Path(f.name))


def main() -> None:
    settings = determine_and_load_settings()
    print(settings)
    do_diff(settings)


def do_diff(settings: Settings) -> None:
    api = GithubApi(settings)
    
    print(f"[DEBUG do_diff] Starting diff with base_ref={settings.base_ref}, ref={settings.ref}")
    print(f"[DEBUG do_diff] Initial pr_num: '{settings.pr_num}'")
    
    # If pr_num is empty, try to find it
    if not settings.pr_num:
        print("[DEBUG do_diff] PR number not set, attempting to find PR for branch...")
        settings.pr_num = api.find_pr_for_branch(settings.ref)
        if settings.pr_num:
            print(f"[DEBUG do_diff] Found PR #{settings.pr_num} for branch {settings.ref}")
        else:
            print(f"[DEBUG do_diff] No open PR found for branch {settings.ref} - will show diff in logs only")
            # Continue with diff but skip posting comment
    else:
        print(f"[DEBUG do_diff] Using provided PR #{settings.pr_num}")
    
    print("[DEBUG do_diff] Loading base lockfile...")
    base_packages = load_lockfile(api, settings.base_ref)
    print(f"[DEBUG do_diff] Loaded {len(base_packages)} base packages")
    
    print("[DEBUG do_diff] Loading head lockfile...")
    head_packages = load_lockfile(api, settings.ref)
    print(f"[DEBUG do_diff] Loaded {len(head_packages)} head packages")

    print("[DEBUG do_diff] Computing diff...")
    packages = diff(base_packages, head_packages)
    summary = format_comment(packages)
    
    if summary:
        print(f"[DEBUG do_diff] Generated summary with {len(summary)} characters")
        print("=== DIFF SUMMARY ===")
        print(summary)
        print("====================")
    else:
        print("[DEBUG do_diff] No changes detected")
    
    if settings.pr_num:
        print(f"[DEBUG do_diff] Posting comment to PR #{settings.pr_num}")
        post_comment(api, summary)
    else:
        print("[DEBUG do_diff] Skipping comment post (no PR number available)")


if __name__ == "__main__":
    main()
