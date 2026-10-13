"""Dependency advisory gate for CI (production hardening Phase 2, B-20). Standard library only.

Policy:
- CRITICAL: always fails. Exceptions are not accepted for critical findings.
- HIGH, and any finding whose severity is unknown (pip-audit reports none): fails unless a valid exception exists.
- MODERATE / LOW: reported, never fail.

Every finding is labelled RUNTIME (shipped: requirements.txt, npm --omit=dev) or BUILD (build/test-time only:
requirements-dev.txt extras, npm devDependencies).

A valid exception names one ecosystem, one package, one scope and explicit advisory ids (no wildcards), and has a
non-empty owner, approved_by, rationale and compensating_controls, status APPROVED, and an expiry date that is in the
future but no more than MAX_EXCEPTION_DAYS ahead. Anything else is reported as invalid and ignored.

Usage:
  python advisory_gate.py --exceptions advisory-exceptions.json \
      --npm-all npm-all.json --npm-runtime npm-runtime.json --pip-runtime pip-runtime.json --pip-dev pip-dev.json
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

MAX_EXCEPTION_DAYS = 180
REQUIRED_TEXT = ("owner", "approved_by", "rationale", "compensating_controls")
BLOCKING = {"critical", "high", "unknown"}


@dataclass(frozen=True)
class Finding:
    ecosystem: str
    package: str
    advisory: str
    aliases: frozenset[str]
    severity: str
    scope: str  # runtime | build
    title: str = ""

    @property
    def ids(self) -> frozenset[str]:
        return self.aliases | {self.advisory}


@dataclass
class Exception_:
    raw: dict
    errors: list[str] = field(default_factory=list)

    def matches(self, finding: Finding) -> bool:
        return (self.raw.get("ecosystem") == finding.ecosystem and self.raw.get("package") == finding.package
                and self.raw.get("scope") == finding.scope and bool(set(self.raw.get("advisories", [])) & finding.ids))


def _advisory_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1] if url else "unknown"


def npm_findings(all_report: dict, runtime_report: dict) -> list[Finding]:
    def advisories(report: dict) -> dict[tuple[str, str], dict]:
        found = {}
        for vulnerability in report.get("vulnerabilities", {}).values():
            for via in vulnerability.get("via", []):
                if isinstance(via, dict):  # string entries are derived (a dependency of a vulnerable package)
                    found[(via.get("name", "?"), _advisory_id(via.get("url", "")))] = via
        return found

    runtime = advisories(runtime_report)
    return [Finding("npm", name, advisory, frozenset(), (via.get("severity") or "unknown").lower(),
                    "runtime" if (name, advisory) in runtime else "build", via.get("title", ""))
            for (name, advisory), via in sorted(advisories(all_report).items())]


def pip_findings(runtime_report: dict, dev_report: dict) -> list[Finding]:
    def vulns(report: dict) -> dict[tuple[str, str], tuple[dict, dict]]:
        return {(dep["name"].lower(), vuln["id"]): (dep, vuln)
                for dep in report.get("dependencies", []) for vuln in dep.get("vulns", [])}

    runtime = vulns(runtime_report)
    every = {**vulns(dev_report), **runtime}
    # pip-audit reports no severity: treat as "unknown", which blocks like HIGH until reviewed.
    return [Finding("pip", name, advisory, frozenset(vuln.get("aliases", [])), "unknown",
                    "runtime" if (name, advisory) in runtime else "build", (vuln.get("description") or "")[:90])
            for (name, advisory), (_, vuln) in sorted(every.items())]


def load_exceptions(data: dict, today: date) -> list[Exception_]:
    loaded = []
    for raw in data.get("exceptions", []):
        item = Exception_(raw)
        for key in REQUIRED_TEXT:
            if not str(raw.get(key, "")).strip():
                item.errors.append(f"missing {key}")
        if raw.get("status") != "APPROVED":
            item.errors.append(f"status is {raw.get('status')!r}, not APPROVED")
        advisories = raw.get("advisories") or []
        if not advisories:
            item.errors.append("no advisory ids")
        if any("*" in str(value) for value in [raw.get("package", ""), *advisories]) or not raw.get("package"):
            item.errors.append("wildcard or missing package/advisory (blanket exceptions are not allowed)")
        if raw.get("scope") not in ("runtime", "build") or raw.get("ecosystem") not in ("npm", "pip"):
            item.errors.append("ecosystem must be npm|pip and scope runtime|build")
        try:
            expires = date.fromisoformat(str(raw.get("expires")))
            if expires < today:
                item.errors.append(f"expired on {expires}")
            elif expires > today + timedelta(days=MAX_EXCEPTION_DAYS):
                item.errors.append(f"expiry more than {MAX_EXCEPTION_DAYS} days ahead")
        except ValueError:
            item.errors.append("expires must be an ISO date")
        loaded.append(item)
    return loaded


def evaluate(findings: list[Finding], exceptions: list[Exception_]) -> tuple[list[tuple[Finding, str]], bool]:
    rows, failed = [], False
    for finding in findings:
        if finding.severity == "critical":
            verdict, failed = "FAIL (critical: no exceptions)", True
        elif finding.severity in BLOCKING:
            matching = [item for item in exceptions if item.matches(finding)]
            valid = [item for item in matching if not item.errors]
            if valid:
                verdict = f"EXCEPTED until {valid[0].raw['expires']} (owner {valid[0].raw['owner']})"
            else:
                failed = True
                verdict = "FAIL" + (f" (exception not valid: {'; '.join(matching[0].errors)})" if matching else "")
        else:
            verdict = "report only"
        rows.append((finding, verdict))
    return rows, failed


def render(rows: list[tuple[Finding, str]], exceptions: list[Exception_]) -> str:
    lines = ["| ecosystem | package | advisory | severity | scope | verdict |", "|---|---|---|---|---|---|"]
    lines += [f"| {f.ecosystem} | {f.package} | {f.advisory} | {f.severity} | {f.scope.upper()} | {v} |" for f, v in rows]
    if not rows:
        lines.append("| - | - | - | - | - | no known advisories |")
    for item in exceptions:
        state = "valid" if not item.errors else "INVALID: " + "; ".join(item.errors)
        lines.append(f"\nexception {item.raw.get('ecosystem')}/{item.raw.get('package')} "
                     f"{item.raw.get('advisories')}: {state}")
    return "\n".join(lines)


def _read(path: Path | None) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("--exceptions", "--npm-all", "--npm-runtime", "--pip-runtime", "--pip-dev"):
        parser.add_argument(name, type=Path, required=name == "--exceptions")
    args = parser.parse_args(argv)
    today = today or date.today()

    findings = (npm_findings(_read(args.npm_all), _read(args.npm_runtime))
                + pip_findings(_read(args.pip_runtime), _read(args.pip_dev)))
    exceptions = load_exceptions(_read(args.exceptions), today)
    rows, failed = evaluate(findings, exceptions)
    report = render(rows, exceptions) + f"\n\nResult: {'FAIL' if failed else 'PASS'}\n"
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Dependency advisories\n\n" + report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
