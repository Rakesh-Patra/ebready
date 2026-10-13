"""Download a public GitHub commit as a bounded, safely extracted snapshot."""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ebkit.source_bundle import MAX_ARCHIVE, extract_zip, inspect


def public_snapshot(url: str, destination: Path) -> dict:
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", url.strip())
    if not match or match[2] in {".", ".."}:
        raise ValueError("Enter a public HTTPS GitHub repository URL without credentials or query parameters.")
    owner, repository = match.groups()
    destination = destination.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("GitHub snapshot output must be an empty directory; existing files are never overwritten.")
    api = f"https://api.github.com/repos/{owner}/{repository}"
    headers = {"User-Agent": "EBKit-Doctor", "Accept": "application/vnd.github+json"}

    def metadata(endpoint):
        with urlopen(Request(endpoint, headers=headers), timeout=20) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("GitHub metadata exceeds the response limit.")
            return json.loads(data)

    try:
        repo = metadata(api)
        if not isinstance(repo, dict) or repo.get("private") is not False or repo.get("archived") or not isinstance(repo.get("default_branch"), str) or not repo["default_branch"]:
            raise ValueError("Use an active public GitHub repository.")
        commit_data = metadata(f"{api}/commits/{quote(repo['default_branch'], safe='')}")
        commit = commit_data.get("sha", "") if isinstance(commit_data, dict) else ""
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("GitHub did not return a valid commit snapshot.")
        with tempfile.TemporaryDirectory(prefix="ebkit-github-") as temporary:
            archive = Path(temporary) / "source.zip"
            start = time.monotonic()
            with urlopen(Request(f"https://codeload.github.com/{owner}/{repository}/zip/{commit}", headers={"User-Agent": "EBKit-Doctor"}), timeout=20) as response, archive.open("wb") as output:
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > MAX_ARCHIVE or time.monotonic() - start > 60:
                        raise ValueError("GitHub snapshot exceeds the 25 MB / 60-second download limit.")
                    output.write(chunk)
            extract_zip(archive, destination)
        return {**inspect(destination), "repository": f"https://github.com/{owner}/{repository}", "commit": commit}
    except HTTPError as error:
        if error.code in {403, 429}:
            raise ValueError("GitHub request limit reached. Try again later.") from error
        raise ValueError("GitHub repository/commit could not be downloaded. It must be public.") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError("GitHub could not be reached or returned invalid metadata. Try again.") from error
    except zipfile.BadZipFile as error:
        raise ValueError("GitHub returned an invalid ZIP snapshot. Try again.") from error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--github", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(public_snapshot(args.github, Path(args.output))))
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
