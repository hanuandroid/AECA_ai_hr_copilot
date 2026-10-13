"""Tests for the dependency advisory gate. Run: python -m pytest .github/scripts -q"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import advisory_gate as gate  # noqa: E402

TODAY = date(2026, 10, 13)


def npm_report(*advisories: tuple[str, str, str]) -> dict:
    return {"vulnerabilities": {name: {"via": [{"name": name, "severity": severity,
                                                 "url": f"https://github.com/advisories/{ghsa}", "title": "t"}]}
                                for name, severity, ghsa in advisories}}


def exception(**overrides) -> dict:
    base = {"ecosystem": "npm", "package": "postcss", "scope": "runtime", "advisories": ["GHSA-aaaa"],
            "status": "APPROVED", "owner": "sec-team", "approved_by": "cto", "expires": "2026-12-01",
            "rationale": "r", "compensating_controls": "c"}
    return {**base, **overrides}


def run(tmp_path: Path, *, npm_all=None, npm_runtime=None, pip_runtime=None, pip_dev=None, exceptions=()) -> int:
    files = {"npm-all": npm_all or {}, "npm-runtime": npm_runtime or {}, "pip-runtime": pip_runtime or {},
             "pip-dev": pip_dev or {}, "exceptions": {"exceptions": list(exceptions)}}
    for name, content in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(content))
    argv = [arg for name in files for arg in (f"--{name}", str(tmp_path / f"{name}.json"))]
    return gate.main(argv, today=TODAY)


def test_no_findings_passes(tmp_path):
    assert run(tmp_path) == 0


def test_moderate_and_low_are_reported_but_pass(tmp_path, capsys):
    report = npm_report(("a", "moderate", "GHSA-m"), ("b", "low", "GHSA-l"))
    assert run(tmp_path, npm_all=report, npm_runtime=report) == 0
    assert "GHSA-m" in capsys.readouterr().out


def test_high_without_exception_fails(tmp_path):
    report = npm_report(("postcss", "high", "GHSA-aaaa"))
    assert run(tmp_path, npm_all=report, npm_runtime=report) == 1


def test_high_with_valid_exception_passes(tmp_path):
    report = npm_report(("postcss", "high", "GHSA-aaaa"))
    assert run(tmp_path, npm_all=report, npm_runtime=report, exceptions=[exception()]) == 0


def test_critical_fails_even_with_an_exception(tmp_path):
    report = npm_report(("postcss", "critical", "GHSA-aaaa"))
    assert run(tmp_path, npm_all=report, npm_runtime=report, exceptions=[exception()]) == 1


@pytest.mark.parametrize("override", [
    {"status": "PROPOSED"}, {"owner": ""}, {"approved_by": " "}, {"rationale": ""}, {"compensating_controls": ""},
    {"expires": "2026-10-12"}, {"expires": "2028-01-01"}, {"expires": "soon"}, {"advisories": []},
    {"advisories": ["*"]}, {"package": "*"}, {"scope": "runtime-and-build"},
    {"advisories": ["GHSA-other"]}, {"package": "postcss-nested"}, {"scope": "build"}, {"ecosystem": "pip"},
])
def test_invalid_or_non_matching_exceptions_do_not_excuse_a_high(tmp_path, override):
    report = npm_report(("postcss", "high", "GHSA-aaaa"))
    assert run(tmp_path, npm_all=report, npm_runtime=report, exceptions=[exception(**override)]) == 1


def test_npm_scope_is_build_when_absent_from_the_runtime_report(tmp_path):
    findings = gate.npm_findings(npm_report(("braces", "high", "GHSA-b"), ("postcss", "high", "GHSA-p")),
                                 npm_report(("postcss", "high", "GHSA-p")))
    assert {(f.package, f.scope) for f in findings} == {("braces", "build"), ("postcss", "runtime")}


def test_pip_findings_have_unknown_severity_and_block_until_excepted(tmp_path):
    dev = {"dependencies": [{"name": "pytest", "version": "8.4.2",
                             "vulns": [{"id": "PYSEC-1", "aliases": ["CVE-1", "GHSA-x"], "description": "d"}]}]}
    assert run(tmp_path, pip_dev=dev) == 1
    by_alias = exception(ecosystem="pip", package="pytest", scope="build", advisories=["CVE-1"])
    assert run(tmp_path, pip_dev=dev, exceptions=[by_alias]) == 0


def test_pip_scope_runtime_vs_build(tmp_path):
    runtime = {"dependencies": [{"name": "Jinja2", "version": "1", "vulns": [{"id": "PYSEC-R", "aliases": []}]}]}
    dev = {"dependencies": runtime["dependencies"] + [{"name": "pytest", "version": "1", "vulns": [{"id": "PYSEC-D"}]}]}
    findings = gate.pip_findings(runtime, dev)
    assert {(f.package, f.scope) for f in findings} == {("jinja2", "runtime"), ("pytest", "build")}


def test_committed_exceptions_file_is_well_formed_and_not_self_approved():
    data = json.loads((Path(__file__).parents[1] / "security" / "advisory-exceptions.json").read_text())
    for raw in data["exceptions"]:
        assert raw["advisories"] and "*" not in json.dumps(raw["advisories"]) and raw["package"] != "*"
        if raw["status"] == "APPROVED":  # approval must name real people; never filled in by tooling
            assert raw["owner"].strip() and raw["approved_by"].strip()
