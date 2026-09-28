"""Create a clean, reproducible local submission bundle."""
import hashlib
import json
import os
import zipfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)
OUT = DIST / "Reprise_Submission.zip"
RUN = os.getenv("REPRISE_PACKAGE_RUN", "released-v8")

paths = [
    ROOT / ".env.example", ROOT / ".gitignore", ROOT / ".python-version",
    ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "reproduce.sh",
    ROOT / "README.md", ROOT / "demo/index.html", ROOT / "demo/app.js",
    ROOT / "demo/package.json", ROOT / "demo/package-lock.json",
    DIST / "Reprise_Theme05_Submission.pptx", DIST / "Reprise_recorded_demo.mp4",
    ROOT / "runs" / RUN / "manifest.json", ROOT / "runs" / RUN / "report.json",
    ROOT / "runs" / f"{RUN}.log",
    ROOT / "runs/extension-question.wav", ROOT / "runs/extension-response.wav",
]
paths += sorted((ROOT / "src/reprise").glob("*.py"))
paths += sorted((ROOT / "tests").glob("*.py"))
paths += sorted((ROOT / "docs").glob("*.md"))
paths += sorted((ROOT / "docs").glob("*.svg"))
paths += sorted((ROOT / "build").glob("*.py"))
paths += sorted((ROOT / "build").glob("*.mjs"))
for case in sorted((ROOT / "runs" / RUN).iterdir()):
    if case.is_dir():
        paths += [case / "events.jsonl", case / "result_reprise.json", case / "client.log"]
upstream_score = ROOT / "runs" / RUN / "upstream_exact_report.json"
if upstream_score.exists():
    paths.append(upstream_score)
for report_name in ("upstream_tool_report.json", "timing_proxy_report.json"):
    report_path = ROOT / "runs" / RUN / report_name
    if report_path.exists():
        paths.append(report_path)
extension_rooms = sorted((ROOT / "runs").glob("reprise-demo-*/events.jsonl"))
if extension_rooms:
    paths.append(extension_rooms[-1])
missing = [str(p.relative_to(ROOT)) for p in paths if not p.is_file()]
if missing:
    raise RuntimeError(f"Missing submission artifacts: {missing}")

secrets = [value.encode() for name, value in dotenv_values(ROOT / ".env.local").items()
           if value and (name.endswith(("KEY", "SECRET")) or name == "LIVEKIT_URL")
           and len(value) >= 8]
checksums = {}
with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as bundle:
    for path in paths:
        data = path.read_bytes()
        if any(value in data for value in secrets):
            if path.suffix != ".log":
                raise RuntimeError(f"A private credential appears in {path.relative_to(ROOT)}")
            for value in secrets:
                data = data.replace(value, b"[REDACTED]")
        relative = path.relative_to(ROOT)
        if relative.parts[:2] == ("runs", RUN):
            relative = Path("evidence") / RUN / Path(*relative.parts[2:])
        elif relative.parts[:1] == ("runs",):
            relative = Path("evidence") / relative.name
        else:
            relative = Path(*relative.parts[1:]) if relative.parts[:1] == ("dist",) else relative
        name = str(Path("Reprise") / relative)
        bundle.writestr(name, data)
        checksums[name] = hashlib.sha256(data).hexdigest()
    bundle.writestr("Reprise/SHA256SUMS.json", json.dumps(checksums, indent=2))

with zipfile.ZipFile(OUT) as bundle:
    names = bundle.namelist()
    if len(names) != len(set(names)):
        raise RuntimeError("Duplicate archive paths")
    if bundle.testzip():
        raise RuntimeError("Archive integrity error")
print(json.dumps({"package": str(OUT), "files": len(paths),
                  "bytes": OUT.stat().st_size, "evidence_run": RUN}, indent=2))
