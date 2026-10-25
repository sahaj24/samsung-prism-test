"""Offline evaluation only. Never imported by the voice agent.

Uses the pinned original streaming client and original exact-match scorer.
Results are provisional: no independent ASR, semantic judge or organizer normalization.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from .backend import FDB_COMMIT
from .config import ROOT, Settings, source_digest
from .metrics import summarize_events


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


async def collect_trace(room, timeout=15, interval=.1):
    """Bounded drain; incomplete traces stay explicitly incomplete."""
    deadline = time.monotonic() + timeout
    while True:
        events = read_events(room)
        closed = any(e.get("event") == "session_closed" for e in events)
        if closed or time.monotonic() >= deadline:
            return events, closed
        await asyncio.sleep(interval)


def scorer():
    path = ROOT / "third_party/fdb/v3/evaluate_pass_rate.py"
    spec = importlib.util.spec_from_file_location("fdb_pass_scorer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate_scenario_pass


def select_inputs(root, limit, seed, sample_ids=None):
    if sample_ids:
        selected = [root / name / "input.wav" for name in sample_ids]
        missing = [name for name, path in zip(sample_ids, selected, strict=True) if not path.is_file()]
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
    return source_digest()


def worker_configuration_errors(events, settings, manifest):
    started = next((e for e in events if e.get("event") == "session_started"), None)
    if not started:
        return ["session_started_missing"]
    expected = {"model": settings.model, "policy": settings.policy, "mode": "benchmark",
                "source_sha256": manifest["source_sha256"]}
    if "vad_silence_ms" in manifest:
        expected["vad_silence_ms"] = manifest["vad_silence_ms"]
    return [key for key, value in expected.items() if started.get(key) != value]


def execution_confirmed(calls):
    """None means legacy/provider telemetry omitted outcomes; never invent success."""
    if any(c.get("outcome") not in {None, "success", "completed", "ok"} for c in calls):
        return False
    return True if all(c.get("outcome") is not None for c in calls) else None


def calls_from_events(events):
    """Include every dispatch, even if export precedes its terminal outcome."""
    fields = ("function", "args", "timestamp_start", "timestamp_end", "outcome", "duration_s",
              "proposal_id", "provider_call_id", "request_id", "trace_id")
    calls, pending = [], []
    for event in events:
        if event.get("event") == "tool_dispatch":
            pending.append(event)
        elif event.get("event") == "tool_executed":
            calls.append({key: event[key] for key in fields if key in event})
            for index, dispatch in enumerate(pending):
                same = (dispatch.get("proposal_id") == event.get("proposal_id")
                        if dispatch.get("proposal_id") and event.get("proposal_id") else
                        dispatch.get("function") == event.get("function") and dispatch.get("args") == event.get("args"))
                if same:
                    pending.pop(index)
                    break
    for dispatch in pending:
        call = {key: dispatch[key] for key in fields if key in dispatch}
        call.update(outcome="unknown", evidence="dispatched_without_terminal_event")
        if dispatch.get("wall_time") is not None:
            call["timestamp_start"] = dispatch["wall_time"]
        calls.append(call)
    return sorted(calls, key=lambda call: call.get("timestamp_start", float("inf")))


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
        "settings": asdict(settings), "python": platform.python_version(),
        "vad_silence_ms": int(os.getenv("REPRISE_VAD_SILENCE_MS", "650")),
        "judge": {"enabled": False, "method": "exact"},
        "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "input_sha256": {p.parent.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in selected},
        "metadata_sha256": {p.parent.name: hashlib.sha256((p.parent / "metadata.json").read_bytes()).hexdigest()
                            for p in selected},
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
                events, trace_closed = await collect_trace(room)
                (sample / f"events-attempt{attempt}.jsonl").write_text(
                    "".join(json.dumps(e) + "\n" for e in events))
                session_errors = [e for e in events if e["event"] == "session_error"]
                if not events:
                    error = error or "agent_did_not_connect"
                elif session_errors:
                    error = error or "model_session_error"
                configuration_errors = worker_configuration_errors(events, settings, manifest)
                if events and configuration_errors:
                    error = error or "agent_configuration_mismatch"
                attempts.append({"room": room, "infrastructure_error": error, "trace_closed": trace_closed,
                                 "configuration_errors": configuration_errors})
                if not error or (events and configuration_errors) or attempt == args.infrastructure_retries:
                    break
                print(f"[retry] {name}: {error}; attempt {attempt + 2}/{args.infrastructure_retries + 1}", flush=True)
                await asyncio.sleep(5)
            shutil.copyfile(attempt_log, sample / "client.log")
            if attempt_audio.exists():
                shutil.copyfile(attempt_audio, sample / "output_reprise.wav")
            calls = calls_from_events(events)
            meta = json.loads((audio.parent / "metadata.json").read_text())
            judged = evaluate(meta, calls, use_llm=False)
            result = {
                "sample": name, "room": room, "id": meta["id"],
                "attempts": attempts,
                "example_id": meta["id"],
                "domain": meta["domain"], "difficulty": meta["difficulty"],
                "actual_tool_calls": calls, "evaluation": judged,
                "passed": judged["passed"] and not error and execution_confirmed(calls) is True,
                "upstream_label_passed": judged["passed"], "execution_confirmed": execution_confirmed(calls),
                "infrastructure_error": error,
                "transcript": " ".join(e.get("text", "") for e in events
                                        if e["event"] == "conversation_item" and e.get("role") == "assistant"),
                "transcript_source": "provider output transcription, not independent ASR",
                "source_unchanged": source_hash() == manifest["source_sha256"],
                "trace_closed": trace_closed, "trace_metrics": summarize_events(events),
                "trace_file": "events.jsonl",
                "in_flight_dispatches": sum(c.get("evidence") == "dispatched_without_terminal_event" for c in calls),
                "model": settings.model, "evaluation_method": "pinned exact, no semantic judge",
                "observed_session": next((e for e in events if e.get("event") == "session_started"), None),
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
        "execution_outcome_failures": sum(r.get("execution_confirmed") is False for r in results),
        "by_domain": by_domain,
        "source_unchanged": all(r["source_unchanged"] for r in results),
        "results": sorted(results, key=lambda r: r["sample"]),
    }
    temporary = output / "report.tmp"
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(output / "report.json")
    return report


def _load_evaluator(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_results(results_dir, *, provider='reprise', benchmark=None, upstream=None, use_llm=False):
    """Score saved results only. Semantic judging requires an explicit flag and key.

    Scorer artifacts are separate from recordings and historical results. Never
    rewrite input results, manufacture ASR fields, or treat API completion as voice latency.
    """
    if use_llm and not os.getenv('OPENAI_API_KEY'):
        raise ValueError('--use-llm requires OPENAI_API_KEY and may consume paid judging credits.')
    if not provider.replace('_', '').replace('-', '').isalnum():
        raise ValueError('Provider must contain only letters, numbers, hyphens or underscores.')
    results_dir = Path(results_dir).resolve()
    upstream = Path(upstream or ROOT / 'third_party/fdb/v3')
    benchmark = Path(benchmark or upstream / 'benchmark_data_v2.json')
    if not (upstream / 'evaluate_pass_rate.py').is_file():
        raise FileNotFoundError('Pinned FDB evaluators missing. Run setup first.')
    # Verify the upstream checkout rather than trusting a directory name.
    actual_commit = subprocess.check_output(
        ['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if actual_commit != FDB_COMMIT:
        raise ValueError('Evaluator checkout does not match the pinned upstream commit.')
    exact = _load_evaluator(upstream / 'evaluate_pass_rate.py', 'reprise_pass_scorer')
    tools = _load_evaluator(upstream / 'evaluate_tool_calls.py', 'reprise_tool_scorer')
    labels = json.loads(benchmark.read_text())
    scenarios = {s['id']: s for s in labels['scenarios']}
    cases, unknown = [], []
    for path in sorted(results_dir.rglob(f'result_{provider}.json')):
        data = json.loads(path.read_text())
        identifier = data.get('example_id', data.get('id'))
        if identifier not in scenarios:
            unknown.append(str(path.relative_to(results_dir)))
            continue
        scenario = scenarios[identifier]
        calls = data.get('actual_tool_calls', [])
        trace_name = data.get('trace_file', 'events.jsonl' if provider == 'reprise' else '')
        if trace_name and Path(trace_name).name != trace_name:
            raise ValueError('Trace file must be beside its result file.')
        trace_path = path.parent / trace_name if trace_name else None
        if trace_path and trace_path.is_file():
            events = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
            if any(e.get('event') in {'tool_dispatch', 'tool_executed'} for e in events):
                calls = calls_from_events(events)
        transcript = data.get('transcript', '')
        passed = exact.evaluate_scenario_pass(scenario, calls, transcript=transcript,
                                              result_data=data, use_llm=use_llm)
        continuous = tools.evaluate_scenario(scenario, calls, transcript=transcript,
                                             result_data=data, use_llm=use_llm)
        confirmed = execution_confirmed(calls)
        if data.get('in_flight_dispatches', 0):
            confirmed = False
        cases.append({'sample': str(path.parent.relative_to(results_dir)),
                      'passed': passed['passed'] and not data.get('infrastructure_error') and confirmed is not False,
                      'execution_confirmed': confirmed,
                      'trace_closed': data.get('trace_closed'),
                      'infrastructure_error': data.get('infrastructure_error'),
                      'pass_checks': passed, 'metrics': continuous})
    manifest_path = results_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    selected = len(manifest.get('selected', [])) or len(cases)
    def average(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None
    passed = sum(c['passed'] for c in cases)
    metric_rows = [c['metrics'] for c in cases]
    spoken = [c for c in metric_rows if c['turn_take_success']]
    return {
        'schema_version': 2, 'provider': provider, 'model': manifest.get('model'),
        'upstream_commit': actual_commit, 'source_sha256': manifest.get('source_sha256'),
        'benchmark_sha256': hashlib.sha256(benchmark.read_bytes()).hexdigest(),
        'method': 'upstream semantic gpt-4o' if use_llm else 'upstream exact',
        'semantic_judging_enabled': use_llm,
        'semantic_limitations': 'Pinned upstream may fall back to exact on judge errors; no semantic equivalence guarantee.' if use_llm else None,
        'official_score': None, 'completed': len(cases), 'selected': selected,
        'exact_passed' if not use_llm else 'semantic_passed': passed,
        'upstream_label_passed': sum(c['pass_checks']['passed'] for c in cases),
        'execution_outcome_failures': sum(c['execution_confirmed'] is False for c in cases),
        'execution_outcome_unavailable': sum(c['execution_confirmed'] is None for c in cases),
        'trace_incomplete_samples': sum(c['trace_closed'] is False for c in cases),
        'completed_pass_rate': passed / len(cases) if cases else None,
        'selected_set_pass_rate_missing_as_fail': passed / selected if selected else None,
        'tool_selection_f1_all': average(c['metrics']['tool_selection_acc']['score'] for c in metric_rows),
        'argument_accuracy_all': average(c['metrics']['argument_acc']['score'] for c in metric_rows),
        'tool_selection_f1_spoken': average(c['metrics']['tool_selection_acc']['score'] for c in spoken),
        'argument_accuracy_spoken': average(c['metrics']['argument_acc']['score'] for c in spoken),
        'spoken_samples': len(spoken),
        'response_quality': average(c['metrics']['response_qual']['score'] for c in metric_rows),
        'audio_latency_samples': sum(c['latency'].get('available', False) for c in metric_rows),
        'infrastructure_errors': dict(Counter(c['infrastructure_error'] for c in cases if c['infrastructure_error'])),
        'unmatched_results': unknown, 'results': cases,
    }
