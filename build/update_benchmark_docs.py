"""Publish audited benchmark facts to the README, dashboard and evidence files."""
import argparse
import json
import re
import shutil
from pathlib import Path

from audit_benchmark import audit

ROOT = Path(__file__).resolve().parents[1]


def history(selected: str):
    lines = ["# Benchmark run history", "", "These are local development runs on the released recordings. Reusing this public set for development does not establish performance on unseen requests or an organizer ranking.", "",
             "| Full run | Exact passes | Infrastructure failures | Infrastructure retries |", "| --- | ---: | ---: | ---: |"]
    runs = sorted((ROOT / "runs").glob("released-v*/report.json"),
                  key=lambda p: int(p.parent.name.removeprefix("released-v")))
    for path in runs:
        result = json.loads(path.read_text())
        if result.get("completed") != 100 or result.get("selected") != 100:
            continue
        name = path.parent.name
        failures = sum(bool(r["infrastructure_error"]) for r in result["results"])
        retries = (sum(len(r["attempts"]) - 1 for r in result["results"])
                   if all("attempts" in r for r in result["results"]) else "not recorded")
        destination = ROOT / "evidence" / name
        destination.mkdir(parents=True, exist_ok=True)
        for filename in ("manifest.json", "report.json", "upstream_exact_report.json"):
            if (path.parent / filename).exists():
                shutil.copyfile(path.parent / filename, destination / filename)
        label = name + (" — published code" if name == selected else "")
        lines.append(f"| [{label}](../evidence/{name}/report.json) | {result['passed']}/100 | {failures} | {retries} |")
    lines += ["", "The selected release's source digest is checked against its manifest before packaging. Infrastructure failures stay in the denominator. Retries are allowed only after infrastructure errors, not after a normal failed task.", "",
              "## Focused checks", "", "The five-recording `targeted-v10` check passed 5/5 after apartment-search and order-ID fixes. It deliberately selected four failures and one control case, so it is not an estimate of a full benchmark score. Earlier focused checks passed 6/10 (`targeted-v9a`) and 3/10 (`targeted-v9b`).", "",
              "`released-v10` was stopped after 14 of 100 cases (9 passes) when review found that an earlier order ID could override a later O-to-zero correction. Its partial result is not used as a full score. The correction was covered by a new test before `released-v11` started.", ""]
    experiment = ROOT / "runs/released-v11/report.json"
    if experiment.exists():
        partial = json.loads(experiment.read_text())
        if partial["completed"] < partial["selected"]:
            lines += [f"`released-v11` was stopped at the participant's request to publish promptly: {partial['completed']}/100 recordings completed, {partial['passed']} passed. Its experimental code is not the published release. The published code is the fully measured `{selected}` version.", ""]
    (ROOT / "docs/benchmark-history.md").write_text("\n".join(lines))


def publish(run: Path):
    summary = audit(run, ROOT / "src/reprise")
    report = json.loads((run / "report.json").read_text())
    score = summary["passed"]
    (ROOT / "docs/benchmark-results.json").write_text(json.dumps(summary, indent=2) + "\n")
    (run / "audit.json").write_text(json.dumps(summary, indent=2) + "\n")
    evidence = ROOT / "evidence" / run.name
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "report.json", "upstream_exact_report.json", "audit.json"):
        shutil.copyfile(run / name, evidence / name)
    difficulties = summary["by_difficulty"]
    breakdown = ", ".join(f"{key} {difficulties[key]['passed']}/{difficulties[key]['total']}"
                          for key in ("easy", "medium", "hard"))
    section = f'''## The measured result

![Local exact-match result: {score} of 100 released recordings passed. {breakdown}.](docs/benchmark-summary.svg)

**{score}/100 recordings passed** in `{run.name}`. We played all 100 released human recordings through Gemini Live and LiveKit, then checked the saved calls with the [original Full-Duplex-Bench v3 exact-match scorer](https://github.com/DanielLin94144/Full-Duplex-Bench/tree/main/v3). Every recording stays in the denominator, including service failures.

| Check | Result |
| --- | ---: |
| Strict tool-task passes | **{score} / 100** |
| Completed recordings | 100 / 100 |
| Cases ending in an infrastructure failure | {summary['infrastructure_failures']} |
| Retries after infrastructure errors | {summary['infrastructure_retries']} |
| Easy tasks | {difficulties['easy']['passed']} / {difficulties['easy']['total']} |
| Medium tasks | {difficulties['medium']['passed']} / {difficulties['medium']['total']} |
| Hard tasks | {difficulties['hard']['passed']} / {difficulties['hard']['total']} |

The agent used `gemini-3.8-live`, two concurrent rooms, the `instant` mock-tool latency profile, and one unchanged source version. The upstream code is pinned to `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`. The [full report](evidence/{run.name}/report.json), [original scorer output](evidence/{run.name}/upstream_exact_report.json), and [case-by-case results](docs/benchmark-cases.md) are available to inspect.

**This is a local exact-match result. The organizer's official score is unknown.** The check measures tool selection and literal arguments; the upstream matcher skips dynamic result references. It does not measure spoken response quality or the contest's normalized benchmark component. Fresh cloud-model runs can differ. The 85/100 target has {'been reached in this run' if score >= 85 else 'not been reached'}; no leaderboard rank is claimed.

See the [run comparison](docs/benchmark-history.md) for the earlier full runs. Timing figures from older runs are not presented as measurements of this version.

'''
    readme = (ROOT / "README.md").read_text()
    start, end = readme.index("## The measured result"), readme.index("## Verify the saved score")
    readme = readme[:start] + section + readme[end:]
    readme = re.sub(r'evidence/released-v\d+', f'evidence/{run.name}', readme)
    start = readme.index("The original scorer should print")
    end = readme.index("## Run the voice benchmark again", start)
    readme = readme[:start] + f'''The original scorer should print **100 scenarios, {summary['upstream_exact_passed']} passed, {summary['upstream_exact_passed']}.0%**. This rechecks saved tool calls without contacting Gemini or LiveKit.

To also check the source digest, case coverage, recorded calls, and retry history:

```sh
python3 build/audit_benchmark.py "$reprise_verify_dir/Reprise/evidence/{run.name}"
```

The audit should report `{score}` passes and `100` total. The packaged code must match the measured source digest. A fresh hosted run is a new measurement, so it need not return an identical score.

''' + readme[end:]
    if "The recorded video and deck are the earlier" not in readme:
        readme = readme.replace("In this GitHub checkout, the final assets", "The recorded video and deck are the earlier `released-v8` demonstration (74/100); the current score and code are documented above. In this GitHub checkout, the final assets")
    (ROOT / "README.md").write_text(readme)
    html = (ROOT / "demo/index.html").read_text()
    html = re.sub(r'(id="score">)\d+(<span>/100</span>)', rf'\g<1>{score}\2', html)
    (ROOT / "demo/index.html").write_text(html)
    svg = (ROOT / "docs/benchmark-summary.svg").read_text()
    svg = re.sub(r'Reprise local benchmark: \d+ of 100 recordings passed',
                 f'Reprise local benchmark: {score} of 100 recordings passed', svg)
    svg = re.sub(r'<desc id="description">.*?</desc>',
                 f'<desc id="description">Local exact-match passes: {score}/100. {breakdown}. Organizer score pending.</desc>', svg)
    svg = re.sub(r'(font-size="56">)\d+', rf'\g<1>{score}', svg)
    svg = re.sub(r'(x="48" y="131" width=")[0-9.]+(" height="18" rx="9" fill="#6B5948")',
                 rf'\g<1>{684 * score / 100:.2f}\2', svg)
    for key, y in (("easy", 211), ("medium", 249), ("hard", 287)):
        group = difficulties[key]
        svg = re.sub(rf'(x="215" y="{y}" width=")[0-9.]+(" height="10" rx="5" fill="#6B5948")',
                     rf'\g<1>{440 * group["passed"] / group["total"]:.2f}\2', svg)
        svg = re.sub(rf'(x="728" y="{y + 14}"[^>]*>)\d+ / \d+',
                     rf'\g<1>{group["passed"]} / {group["total"]}', svg)
    (ROOT / "docs/benchmark-summary.svg").write_text(svg)
    rows = ["# Recorded benchmark cases", "", f"Run: `{run.name}`. Local strict passes: **{score}/100**. Organizer score pending.", "",
            "Each row is one released recording. Failed infrastructure attempts remain disclosed in the full JSON report.", "",
            "| Recording | Difficulty | Result | Reason |", "| --- | --- | --- | --- |"]
    for case in report["results"]:
        reason = case["infrastructure_error"] or case["evaluation"]["failure_reason"] or "All exact tool checks passed"
        rows.append(f'| `{case["sample"]}` | {case["difficulty"]} | {"Pass" if case["passed"] else "Fail"} | {reason} |')
    (ROOT / "docs/benchmark-cases.md").write_text("\n".join(rows) + "\n")
    history(run.name)
    print(json.dumps({"run": run.name, "passed": score, "evidence": str(evidence)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    publish(parser.parse_args().run.resolve())
