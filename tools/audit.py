#!/usr/bin/python3
"""Check bundled Debian pins and every development pin against live advisories.

Sources: https://security-tracker.debian.org/tracker/data/json
https://docs.pypi.org/api/json/ (release-specific vulnerabilities).
System dependencies remain the operating system's security-update responsibility.
"""

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fetch_json(url: str) -> dict:
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--connect-timeout",
            "15",
            "--max-time",
            "60",
            url,
        ],
        capture_output=True,
        check=True,
        timeout=70,
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise TypeError("invalid advisory response")
    return data


def debian_findings(packages: list[dict], tracker: dict, suite: str) -> list[dict]:
    findings = []
    for package in packages:
        for advisory, record in tracker.get(package["source"], {}).items():
            release = record.get("releases", {}).get(suite)
            if release is None:
                continue
            fixed = release.get("fixed_version")
            if fixed == "0":  # Debian marks this source version unaffected.
                continue
            if (
                fixed
                and subprocess.run(
                    ["dpkg", "--compare-versions", package["version"], "ge", fixed],
                    check=False,
                ).returncode
                == 0
            ):
                continue
            if release.get("status") not in {"open", "undetermined", "resolved"}:
                raise ValueError("unknown Debian advisory status")
            findings.append(
                {
                    "ecosystem": "debian",
                    "name": package["name"],
                    "version": package["version"],
                    "id": advisory,
                    "fixed": fixed,
                    "status": release["status"],
                }
            )
    return findings


def review_findings(findings: list[dict], exceptions: list[dict]) -> list[dict]:
    today = datetime.date.today().isoformat()
    allowed = set()
    for exception in exceptions:
        if not exception.get("reason") or not exception.get("reviewer"):
            raise ValueError("each audit exception needs a reason and reviewer")
        if exception.get("expires", "") < today:
            raise ValueError("expired audit exception")
        allowed.add(
            tuple(exception[key] for key in ("ecosystem", "name", "version", "id"))
        )
    return [
        finding
        for finding in findings
        if tuple(finding[key] for key in ("ecosystem", "name", "version", "id"))
        not in allowed
    ]


def audit() -> int:
    lock = json.loads((ROOT / "packages.lock").read_text())
    tracker = fetch_json("https://security-tracker.debian.org/tracker/data/json")
    if len(tracker) < 100:
        raise ValueError("incomplete Debian security data")
    findings = debian_findings(lock["packages"], tracker, lock["suite"])
    pins = re.findall(
        r"^([A-Za-z0-9_.-]+)==(\S+)",
        (ROOT / "requirements-dev.lock").read_text(),
        re.MULTILINE,
    )
    if not pins:
        raise ValueError("no development pins found")
    for name, version in pins:
        data = fetch_json(f"https://pypi.org/pypi/{name}/{version}/json")
        if data["info"]["version"] != version or not isinstance(
            data["vulnerabilities"], list
        ):
            raise ValueError(f"invalid release metadata for {name}")
        for advisory in data["vulnerabilities"]:
            if not advisory.get("withdrawn"):
                findings.append(
                    {
                        "ecosystem": "pypi",
                        "name": name,
                        "version": version,
                        "id": advisory["id"],
                        "fixed": advisory.get("fixed_in", []),
                    }
                )
    exceptions = json.loads((ROOT / "audit_exceptions.json").read_text())
    failures = review_findings(findings, exceptions)
    report = {
        "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "debian_packages": len(lock["packages"]),
        "pypi_packages": len(pins),
        "findings": findings,
        "unreviewed": failures,
    }
    (ROOT / "tmp").mkdir(exist_ok=True)
    (ROOT / "tmp/audit.json").write_text(json.dumps(report, indent=2) + "\n")
    for finding in findings:
        print(
            f"{finding['name']} {finding['version']}: {finding['id']} (fixed: {finding['fixed']})"
        )
    print(
        f"Audit: {len(lock['packages'])} Debian, {len(pins)} PyPI pins; "
        f"{len(failures)} unreviewed findings."
    )
    return bool(failures)


def main() -> int:
    try:
        return audit()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"audit incomplete: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
