"""Source of definition data: versioned data that sets how bdf behaves.

The BDF ontology is definition data: it defines the columns, labels and units
bdf knows. Each version is published as a GitHub release of its upstream
repository. bdf bundles one version and can load others from a local cache,
fetching them from upstream when needed.

A source is given a function that downloads one version into an empty
directory and a function that opens a version directory. The source owns the
version name, the latest version, the bundle, the cache, the fetch into a
staging directory, the in-process memo, and the refresh command.
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import uuid
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Generic, TypeVar

import requests
from packaging.version import InvalidVersion, Version

from . import fetch as _fetch
from ._errors import BDFDefinitionError, BDFDefinitionWarning

T = TypeVar("T")

_VERSION_FILE = "VERSION"


class DefinitionSource(Generic[T]):
    """Resolve versions of definition data from the bundle, the cache, or upstream."""

    def __init__(
        self,
        name: str,
        *,
        bundled: Path,
        repo: str,
        tag_prefix: str,
        download: Callable[[str, Path, float], None],
        open: Callable[[Path], T],
    ) -> None:
        """Configure the source without any I/O.

        Args:
            name: Data name used in cache directory names and messages.
            bundled: Bundled version directory.
            repo: Upstream GitHub repository as ``owner/name``.
            tag_prefix: Joined to the normal version name to make the tag.
            download: ``download(tag, dest, timeout)`` writes every file of
                the version into the existing empty directory ``dest``.
            open: ``open(path)`` loads a version directory.
        """
        self.name = name
        self.bundled = bundled
        self.repo = repo
        self.tag_prefix = tag_prefix
        self.download = download
        self.open = open
        self._memo: dict[str, T] = {}
        self._latest: str | Exception | None = None

    @staticmethod
    def normalise(name: str) -> str:
        """Return the normal form of a version name.

        Args:
            name: Version name, with or without a leading ``v`` or spaces.

        Returns:
            The normal name, such as ``1.1.0``.

        Raises:
            BDFDefinitionError: If ``name`` is not a version.
        """
        try:
            return str(Version(name.strip()))
        except (InvalidVersion, TypeError, AttributeError) as exc:
            raise BDFDefinitionError(f"{name!r} is not a version") from exc

    def bundled_version(self) -> str | None:
        """Return the name in the bundle's VERSION file, or None if it has none."""
        return _version_in(self.bundled)

    def latest(self, *, refresh: bool = False, timeout: float = 30.0) -> str:
        """Return the version of upstream's latest GitHub release.

        Args:
            refresh: If True, ask upstream again instead of using the
                remembered answer.
            timeout: Request timeout in seconds.

        Returns:
            The normal name of the latest version.

        Raises:
            BDFDefinitionError: If upstream has no release, or its tag cannot
                be named.
            requests.HTTPError: On another 4xx or 5xx answer.
        """
        if self._latest is None or refresh:
            try:
                self._latest = self._ask_latest(timeout)
            except Exception as exc:
                self._latest = exc
        if isinstance(self._latest, Exception):
            raise self._latest
        return self._latest

    def write(self, dest: Path, version: str, *, timeout: float = 30.0) -> Path:
        """Fetch a version into ``dest`` by staging and swap.

        Args:
            dest: Version directory to write or replace.
            version: Version to fetch.
            timeout: Download timeout in seconds.

        Returns:
            ``dest``.

        Raises:
            BDFDefinitionError: If ``dest`` is not a version directory, or the
                fetch fails. ``dest`` is unchanged on failure.
        """
        version = self.normalise(version)
        tag = self.tag_prefix + version
        if dest.exists() and (not dest.is_dir() or (_version_in(dest) is None and any(dest.iterdir()))):
            raise BDFDefinitionError(f"Refusing to replace {dest}: it is not a {self.name} version directory")
        failure = f"Could not replace {dest} with {self.name} {version}"
        staging = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            staging.mkdir()
        except OSError as exc:
            raise BDFDefinitionError(f"{failure}: {exc}") from exc
        old: Path | None = None
        swapping = False
        try:
            try:
                self.download(tag, staging, timeout)
            except Exception as exc:
                raise BDFDefinitionError(f"Could not fetch {self.name} {version} (tag {tag!r}): {exc}") from exc
            (staging / _VERSION_FILE).write_text(f"{version}\n", encoding="utf-8")
            if dest.exists():
                old = dest.with_name(f".{dest.name}.old.{uuid.uuid4().hex}")
                dest.rename(old)
            swapping = True
            staging.rename(dest)
        except BaseException as exc:
            shutil.rmtree(staging, ignore_errors=True)
            if not isinstance(exc, OSError):
                raise
            # Another process can put the same version in place first.
            if swapping and _version_in(dest) == version:
                if old is not None:
                    shutil.rmtree(old, ignore_errors=True)
                return dest
            if old is not None:
                try:
                    if dest.exists():
                        shutil.rmtree(old, ignore_errors=True)
                    else:
                        old.rename(dest)
                except OSError as restore_exc:
                    raise BDFDefinitionError(
                        f"{failure}: {exc}; the previous version is left at {old}: {restore_exc}"
                    ) from exc
            raise BDFDefinitionError(f"{failure}: {exc}") from exc
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return dest

    def path(self, version: str, *, fetch: bool = True, refresh: bool = False, timeout: float = 30.0) -> Path:
        """Return the directory of a version: bundle, cache, or a fetch.

        Args:
            version: Version name.
            fetch: If False, raise instead of fetching.
            refresh: If True, fetch into the cache entry even when it exists.
            timeout: Download timeout in seconds.

        Returns:
            The version directory.

        Raises:
            BDFDefinitionError: If the version is unavailable.
        """
        version = self.normalise(version)
        local = None if refresh else self._local(version)
        if local is not None:
            return local
        if not fetch:
            raise BDFDefinitionError(f"{self.name} {version} is neither bundled nor cached, and fetching is off")
        return self._fetch_entry(version, timeout)

    def get(self, version: str, *, fetch: bool = True, refresh: bool = False, timeout: float = 30.0) -> T:
        """Open a version, memoised by normal name.

        Args:
            version: Version name.
            fetch: If False, raise instead of fetching.
            refresh: If True, drop the memo and fetch again.
            timeout: Download timeout in seconds.

        Returns:
            The opened version.

        Raises:
            BDFDefinitionError: If the version cannot be resolved or opened.
        """
        version = self.normalise(version)
        if refresh:
            self._memo.pop(version, None)
        elif version in self._memo:
            return self._memo[version]
        path = self.path(version, fetch=fetch, refresh=refresh, timeout=timeout)
        try:
            value = self.open(path)
        except Exception as exc:
            raise BDFDefinitionError(f"Could not open {self.name} {version} at {path}: {exc}") from exc
        self._memo[version] = value
        return value

    def get_latest(self, *, refresh: bool = False, timeout: float = 30.0) -> T:
        """Open the latest version, or the newest local final version.

        Args:
            refresh: If True, ask upstream and fetch again.
            timeout: Request and download timeout in seconds.

        Returns:
            The opened version.

        Warns:
            BDFDefinitionWarning: When it falls back to a local version.

        Raises:
            BDFDefinitionError: If it must fall back and no local version exists.
        """
        try:
            version = self.latest(refresh=refresh, timeout=timeout)
        except Exception as exc:
            if isinstance(exc, requests.HTTPError):
                status = getattr(exc.response, "status_code", None)
                reason = f"upstream refused the request (HTTP {status})"
            elif isinstance(exc, BDFDefinitionError):
                reason = str(exc)
            else:
                reason = f"upstream could not be reached ({exc})"
            return self._fall_back(f"Could not ask upstream for the latest version of {self.name}: {reason}", exc)
        try:
            return self.get(version, refresh=refresh, timeout=timeout)
        except BDFDefinitionError as exc:
            return self._fall_back(f"Could not fetch {version}, the latest version of {self.name}: {exc}", exc)

    def forget(self) -> None:
        """Clear the memo and the remembered latest version."""
        self._memo.clear()
        self._latest = None

    def cli(self, prog: str, argv: Sequence[str] | None = None) -> None:
        """Run the refresh command ``prog [VERSION]`` on the bundle.

        Args:
            prog: Command name for usage text.
            argv: Arguments to parse. Defaults to ``sys.argv[1:]``.
        """
        parser = argparse.ArgumentParser(
            prog=prog, description=f"Replace the bundled {self.name} with a version from upstream."
        )
        parser.add_argument("version", nargs="?", metavar="VERSION", help="Version to fetch (default: latest).")
        args = parser.parse_args(argv)
        dest = self.write(self.bundled, args.version or self.latest())
        print(f"Bundled {self.name} updated: {dest} (version {self.bundled_version()})")

    def _ask_latest(self, timeout: float) -> str:
        response = requests.get(
            f"https://api.github.com/repos/{self.repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if response.status_code == 404:
            raise BDFDefinitionError(f"{self.repo} has no release")
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise BDFDefinitionError(f"{self.repo} answered with no JSON release") from exc
        tag = body.get("tag_name") if isinstance(body, dict) else None
        if not isinstance(tag, str):
            raise BDFDefinitionError(f"{self.repo} answered with no release tag name")
        if not tag.startswith(self.tag_prefix):
            raise BDFDefinitionError(f"Cannot name the {self.repo} release tag {tag!r}")
        try:
            version = self.normalise(tag[len(self.tag_prefix) :])
        except BDFDefinitionError as exc:
            raise BDFDefinitionError(f"Cannot name the {self.repo} release tag {tag!r}") from exc
        if tag != self.tag_prefix + version:
            raise BDFDefinitionError(
                f"Cannot name the {self.repo} release tag {tag!r}: it is not {self.tag_prefix + version!r}"
            )
        return version

    def _entry(self, version: str) -> Path:
        return _fetch.cache_dir() / f"{self.name}-v{version}"

    def _local(self, version: str) -> Path | None:
        if self.bundled_version() == version:
            return self.bundled
        try:
            entry = self._entry(version)
        except OSError:
            return None
        if _version_in(entry) is not None:
            return entry
        return None

    def _fetch_entry(self, version: str, timeout: float) -> Path:
        try:
            entry = self._entry(version)
        except OSError as exc:
            raise BDFDefinitionError(f"Could not use the cache for {self.name} {version}: {exc}") from exc
        if entry.exists() and _version_in(entry) is None:
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except OSError as exc:
                raise BDFDefinitionError(
                    f"Could not remove {entry}, which holds no {self.name} {version}: {exc}"
                ) from exc
        return self.write(entry, version, timeout=timeout)

    def _local_finals(self) -> list[str]:
        dirs = [self.bundled]
        with contextlib.suppress(OSError):
            dirs += _fetch.cache_dir().glob(f"{self.name}-v*")
        finals = []
        for directory in dirs:
            name = _version_in(directory)
            try:
                if name is not None and not Version(name).is_prerelease:
                    finals.append(name)
            except InvalidVersion:
                continue
        return finals

    def _fall_back(self, failure: str, cause: BaseException) -> T:
        finals = self._local_finals()
        if not finals:
            raise BDFDefinitionError(f"{failure}. No local {self.name} version to fall back to") from cause
        version = max(finals, key=Version)
        warnings.warn(
            f"{failure}. Loading local version {version}, which can be out of date.",
            BDFDefinitionWarning,
            stacklevel=3,
        )
        return self.get(version, refresh=False)


def _version_in(directory: Path) -> str | None:
    """Return the name in a version directory's VERSION file, or None."""
    try:
        return (directory / _VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
