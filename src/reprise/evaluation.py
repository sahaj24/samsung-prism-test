"""Offline evaluation only. Never imported by the voice agent.

Uses the pinned original streaming client and original exact-match scorer.
Results are provisional: no independent ASR, semantic judge or organizer normalization.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from .backend import FDB_COMMIT
from .config import ROOT, Settings


def read_events(room):
    path = ROOT / "runs" / room / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def scorer():
    path = ROOT / "third_party/fdb/v3/evaluate_pass_rate.py"
    spec = importlib.util.spec_from_file_location("fdb_pass_scorer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate_scenario_pass


def select_inputs(root, limit, seed, sample_ids=None):
    if sample_ids:
        selected = [root / name / "input.wav" for name in sample_ids]
        missing = [name for name, path in zip(sample_ids, selected) if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"Unknown released recording: {missing}")
        return selected
    # Deterministic stratification, chosen before any scores are observed.
    groups = defaultdict(list)
    for audio in sorted(root.glob("*/input.wav")):
        meta = json.loads((audio.parent / "metadata.json").read_text())
        groups[(meta["domain"], meta["difficulty"])].append(audio)
    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)
    selected = []
    while any(groups.values()):
        for key in sorted(groups):
            if groups[key]:
                selected.append(groups[key].pop())
    return selected[:limit] if limit else selected


def source_hash():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src/reprise").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def wait_for_livekit_dns(url, timeout=300):
    """Pause a run during a resolver outage instead of scoring every case as failed."""
    host = urlparse(url).hostname
    if not host:
        raise ValueError("LIVEKIT_URL has no hostname")
    deadline = time.monotonic() + timeout
    warned = False
    while True:
        try:
            await asyncio.get_running_loop().getaddrinfo(host, None)
            if warned:
                print("[network] LiveKit DNS recovered; resuming benchmark", flush=True)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"LiveKit DNS unavailable for {timeout}s; run paused: {host}") from exc
            if not warned:
                print(f"[network] waiting for LiveKit DNS: {host}", flush=True)
                warned = True
            await asyncio.sleep(5)


async def run_suite(args):
    settings = Settings.from_env()
    dataset = Path(args.dataset).resolve()
    selected = select_inputs(dataset, args.limit, args.seed, args.sample)
    if not selected:
        raise RuntimeError("No input.wav files found. Run ./reproduce.sh setup --data.")
    run_id = args.name or time.strftime("screen-%Y%m%d-%H%M%S")
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Run name must contain only letters, numbers, hyphens or underscores.")
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": run_id, "created_at": time.time(), "model": settings.model,
        "policy": settings.policy, "source_sha256": source_hash(),
        "upstream_commit": FDB_COMMIT, "seed": args.seed,
        "concurrency": args.concurrency, "selected": [p.parent.name for p in selected],
        "infrastructure_retries": args.infrastructure_retries,
        "scope": "development screening" if args.limit or args.sample else "full released dataset",
        "method": "Original streaming client + original exact-match tool scorer; provisional",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    evaluate = scorer()
    semaphore = asyncio.Semaphore(args.concurrency)
    results = []

    async def one(index, audio):
        async with semaphore:
            name = audio.parent.name
            sample = output / name
            sample.mkdir()
            attempts = []
            for attempt in range(args.infrastructure_retries + 1):
                await wait_for_livekit_dns(os.environ["LIVEKIT_URL"])
                room = f"{run_id}-{index:03d}-a{attempt}"
                attempt_audio = sample / f"output_reprise-attempt{attempt}.wav"
                attempt_log = sample / f"client-attempt{attempt}.log"
                error = None
                with attempt_log.open("w") as log:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, str(ROOT / "third_party/fdb/v3/livekit_inference.py"),
                        "-i", str(audio), "-o", str(attempt_audio),
                        "--room", room, cwd=ROOT, stdout=log, stderr=log,
                    )
                    try:
                        await asyncio.wait_for(proc.wait(), 150)
                        if proc.returncode:
                            error = f"client_exit_{proc.returncode}"
                    except asyncio.TimeoutError:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), 10)
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                        error = "client_timeout"
                events = read_events(room)
                session_errors = [e for e in events if e["event"] == "session_error"]
                if not events:
                    error = error or "agent_did_not_connect"
                elif session_errors:
                    error = error or "model_session_error"
                attempts.append({"room": room, "infrastructure_error": error})
                if not error or attempt == args.infrastructure_retries:
                    break
                print(f"[retry] {name}: {error}; attempt {attempt + 2}/{args.infrastructure_retries + 1}", flush=True)
                await asyncio.sleep(5)
            shutil.copyfile(attempt_log, sample / "client.log")
            if attempt_audio.exists():
                shutil.copyfile(attempt_audio, sample / "output_reprise.wav")
            calls = [{k: e[k] for k in ("function", "args", "timestamp_start", "timestamp_end", "outcome")}
                     for e in events if e["event"] == "tool_executed"]
            meta = json.loads((audio.parent / "metadata.json").read_text())
            judged = evaluate(meta, calls, use_llm=False)
            result = {
                "sample": name, "room": room, "id": meta["id"],
                "attempts": attempts,
                "example_id": meta["id"],
                "domain": meta["domain"], "difficulty": meta["difficulty"],
                "actual_tool_calls": calls, "evaluation": judged,
                "passed": judged["passed"] and not error, "infrastructure_error": error,
                "transcript": " ".join(e.get("text", "") for e in events
                                        if e["event"] == "conversation_item" and e.get("role") == "assistant"),
                "transcript_source": "provider output transcription, not independent ASR",
                "source_unchanged": source_hash() == manifest["source_sha256"],
            }
            (sample / "result_reprise.json").write_text(json.dumps(result, indent=2))
            (sample / "metadata.json").write_text(json.dumps(meta, indent=2))
            (sample / "events.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
            results.append(result)
            save_report(output, manifest, results)
            print(f"[{len(results)}/{len(selected)}] {name}: "
                  f"{'PASS' if result['passed'] else 'FAIL'}; "
                  f"{error or judged['failure_reason'] or 'all exact checks passed'}", flush=True)

    await asyncio.gather(*(one(i, p) for i, p in enumerate(selected)))
    report = save_report(output, manifest, results)
    print(json.dumps({k: report[k] for k in ("completed", "selected", "passed", "provisional_exact_pass_rate", "infrastructure_errors")}, indent=2))
    return output


def save_report(output, manifest, results):
    by_domain = {}
    for domain in sorted({r["domain"] for r in results}):
        subset = [r for r in results if r["domain"] == domain]
        by_domain[domain] = {"passed": sum(r["passed"] for r in subset), "completed": len(subset)}
    passed = sum(r["passed"] for r in results)
    report = {
        "official_score": None, "label": "PROVISIONAL — exact-match screening, no semantic judge",
        "selected": len(manifest["selected"]), "completed": len(results), "passed": passed,
        "provisional_exact_pass_rate": passed / len(results) if results else None,
        "selected_set_pass_rate_missing_as_fail": passed / len(manifest["selected"]),
        "infrastructure_errors": dict(Counter(r["infrastructure_error"] for r in results if r["infrastructure_error"])),
        "by_domain": by_domain,
        "source_unchanged": all(r["source_unchanged"] for r in results),
        "results": sorted(results, key=lambda r: r["sample"]),
    }
    temporary = output / "report.tmp"
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(output / "report.json")
    return report
