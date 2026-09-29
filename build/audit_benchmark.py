"""Audit saved benchmark evidence without contacting a model or using credentials."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def audit(run: Path, source: Path):
    manifest = json.loads((run / "manifest.json").read_text())
    report = json.loads((run / "report.json").read_text())
    results = report["results"]
    selected = manifest["selected"]
    assert len(selected) == len(set(selected)) == 100, "Expected 100 unique inputs"
    assert report["completed"] == report["selected"] == len(results) == 100
    assert {r["sample"] for r in results} == set(selected)
    assert report["source_unchanged"] and all(r["source_unchanged"] for r in results)
    digest = hashlib.sha256()
    for path in sorted(source.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    assert digest.hexdigest() == manifest["source_sha256"], "Source differs from measured run"
    retries = 0
    for result in results:
        case = run / result["sample"]
        saved = json.loads((case / "result_reprise.json").read_text())
        assert saved == result, f"Summary differs from case: {case.name}"
        events = [json.loads(line) for line in (case / "events.jsonl").read_text().splitlines()]
        calls = [{key: event[key] for key in (
            "function", "args", "timestamp_start", "timestamp_end", "outcome"
        )} for event in events if event["event"] == "tool_executed"]
        assert calls == result["actual_tool_calls"], f"Tool trace mismatch: {case.name}"
        assert result["passed"] == (result["evaluation"]["passed"] and not result["infrastructure_error"])
        assert result["attempts"][-1]["room"] == result["room"]
        assert all(a["infrastructure_error"] for a in result["attempts"][:-1]), "Performance failure retried"
        retries += len(result["attempts"]) - 1
    passed = sum(r["passed"] for r in results)
    assert passed == report["passed"]
    assert report["provisional_exact_pass_rate"] == passed / 100
    upstream = json.loads((run / "upstream_exact_report.json").read_text())
    assert upstream["total_scenarios"] == 100
    # The upstream tool scorer does not inspect session errors. Disclose any
    # difference instead of removing failed sessions from the local denominator.
    assert upstream["passed"] == sum(r["evaluation"]["passed"] for r in results)
    groups = {}
    for field in ("difficulty", "domain"):
        groups[field] = {
            key: {"passed": sum(r["passed"] for r in results if r[field] == key),
                  "total": sum(r[field] == key for r in results)}
            for key in sorted({r[field] for r in results})
        }
    return {
        "run": manifest["run_id"], "model": manifest["model"],
        "passed": passed, "total": 100, "upstream_exact_passed": upstream["passed"],
        "organizer_score": None, "source_sha256": manifest["source_sha256"],
        "infrastructure_failures": sum(bool(r["infrastructure_error"]) for r in results),
        "infrastructure_retries": retries,
        "failure_reasons": dict(Counter(
            "infrastructure" if r["infrastructure_error"] else
            "wrong_arguments" if r["evaluation"]["failure_reason"].startswith("Wrong arguments")
            else "wrong_tools" for r in results if not r["passed"])),
        "by_difficulty": groups["difficulty"], "by_domain": groups["domain"],
        "checks": "100 unique inputs; complete case files; matching tool traces; source digest verified",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src/reprise")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = audit(args.run, args.source)
    rendered = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered)
