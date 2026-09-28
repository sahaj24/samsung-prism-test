import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

from .backend import FDB_COMMIT
from .config import ROOT, Settings, credential


def setup(data=False):
    folder = ROOT / "third_party/fdb"
    if not folder.exists():
        folder.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "https://github.com/DanielLin94144/Full-Duplex-Bench.git", str(folder)], check=True)
        subprocess.run(["git", "-C", str(folder), "checkout", FDB_COMMIT], check=True)
    actual = subprocess.check_output(["git", "-C", str(folder), "rev-parse", "HEAD"], text=True).strip()
    if actual != FDB_COMMIT:
        raise RuntimeError("Existing benchmark checkout is not the pinned commit. Preserve it and use a fresh checkout.")
    if data:
        import gdown
        directory = ROOT / "data"
        directory.mkdir(exist_ok=True)
        archive = directory / "fdb_v3.zip"
        if not archive.exists():
            gdown.download(id="1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz", output=str(archive), quiet=False)
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.filename.startswith("__MACOSX/"):
                    continue
                target = (directory / info.filename).resolve()
                if not target.is_relative_to(directory.resolve()):
                    raise RuntimeError("Unsafe path in dataset archive")
                zf.extract(info, directory)
    print("Pinned benchmark ready.")


async def doctor():
    settings = Settings.from_env()
    from google import genai
    from google.genai import types
    from livekit import api
    from livekit.agents import llm
    from livekit.plugins.google.utils import create_tools_config

    from .agent import make_tool
    from .catalog import TOOLS
    key, mode = credential()
    checks = {"python": sys.version.split()[0], "model": settings.model,
              "ffmpeg": bool(shutil.which("ffmpeg")), "benchmark_tools": (ROOT/"third_party/fdb/v3/mock_apis.py").exists(),
              "audio_count": len(list((ROOT/"data/fdb_v3_data_released").glob("*/input.wav"))),
              "official_judge_key_present": bool(os.getenv("OPENAI_API_KEY"))}
    declarations, _ = create_tools_config(llm.ToolContext([make_tool(t, None) for t in TOOLS]),
        tool_behavior=types.Behavior.NON_BLOCKING, use_parameters_json_schema=False)
    checks["tool_schemas"] = len(TOOLS)
    client = genai.Client(api_key=key, http_options={"api_version": "v1beta" if mode == "ephemeral" else "v1alpha"})
    try:
        async with asyncio.timeout(30):
            async with client.aio.live.connect(model=settings.model,
                config=types.LiveConnectConfig(response_modalities=["AUDIO"], tools=declarations)):
                checks["gemini_live_with_tools"] = "accepted"
    except Exception as exc:
        checks["gemini_live_with_tools"] = type(exc).__name__
    finally:
        await client.aio.aclose()
    try:
        async with api.LiveKitAPI() as lk:
            await lk.room.list_rooms(api.ListRoomsRequest())
        checks["livekit"] = "accepted"
    except Exception as exc:
        checks["livekit"] = type(exc).__name__
    print(json.dumps(checks, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Reprise voice agent and reproducible evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("setup")
    install.add_argument("--data", action="store_true")
    commands.add_parser("doctor")
    commands.add_parser("agent")
    commands.add_parser("demo")
    commands.add_parser("test")
    all_cmd = commands.add_parser("all", help="Install, check, start agent and evaluate in one command")
    all_cmd.add_argument("--limit", type=int, default=0)
    all_cmd.add_argument("--seed", type=int, default=20260927)
    all_cmd.add_argument("--concurrency", type=int, choices=[1, 2, 3], default=1)
    all_cmd.add_argument("--name")
    all_cmd.add_argument("--sample", action="append", help="Run a named released recording; repeat to select several")
    all_cmd.add_argument("--infrastructure-retries", type=int, choices=range(4), default=2)
    evaluate = commands.add_parser("evaluate", help="Provisional exact-match screening; start agent first")
    evaluate.add_argument("--dataset", default=str(ROOT/"data/fdb_v3_data_released"))
    evaluate.add_argument("--limit", type=int, default=0)
    evaluate.add_argument("--seed", type=int, default=20260927)
    evaluate.add_argument("--concurrency", type=int, choices=[1, 2, 3], default=1)
    evaluate.add_argument("--name")
    evaluate.add_argument("--sample", action="append", help="Run a named released recording; repeat to select several")
    evaluate.add_argument("--infrastructure-retries", type=int, choices=range(4), default=2)
    args = parser.parse_args()
    Settings.from_env()
    if args.command == "setup":
        setup(args.data)
    elif args.command == "doctor":
        asyncio.run(doctor())
    elif args.command == "agent":
        os.execv(sys.executable, [sys.executable, "-m", "reprise.agent", "start"])
    elif args.command == "demo":
        import uvicorn
        uvicorn.run("reprise.demo_server:app", host="127.0.0.1",
                    port=int(os.getenv("REPRISE_DEMO_PORT", "8844")))
    elif args.command == "test":
        raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT))
    elif args.command == "evaluate":
        from .evaluation import run_suite
        asyncio.run(run_suite(args))
    elif args.command == "all":
        from .evaluation import run_suite
        setup(data=True)
        asyncio.run(doctor())
        if subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT):
            raise SystemExit("Local behavior tests failed.")
        worker_log = ROOT / "runs/one-command-worker.log"
        worker_log.parent.mkdir(exist_ok=True)
        with worker_log.open("w") as log:
            worker = subprocess.Popen([sys.executable, "-m", "reprise.agent", "start"],
                                      cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if worker.poll() is not None:
                        raise RuntimeError("Agent exited before registering. Check runs/one-command-worker.log")
                    if '"message": "registered worker"' in worker_log.read_text(errors="replace"):
                        break
                    time.sleep(.5)
                else:
                    raise TimeoutError("Agent did not register within 90 seconds")
                args.dataset = str(ROOT / "data/fdb_v3_data_released")
                output = asyncio.run(run_suite(args))
                if os.getenv("OPENAI_API_KEY"):
                    benchmark = ROOT / "third_party/fdb/v3"
                    subprocess.run([sys.executable, str(benchmark/"evaluate_pass_rate.py"),
                        "--benchmark", str(benchmark/"benchmark_data_v2.json"),
                        "--results-dir", str(output), "--provider", "reprise",
                        "--output", str(output/"upstream_semantic_pass_rate.json"),
                        "--use-llm"], check=True, cwd=ROOT)
            finally:
                worker.terminate()
                try:
                    worker.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()


if __name__ == "__main__":
    main()
