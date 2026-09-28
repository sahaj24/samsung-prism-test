"""Time-based replay of real benchmark and extension audio, with actual tool events."""
import json
import math
import os
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/video"
OUT = ROOT / "dist/Reprise_recorded_demo.mp4"
RUN = os.environ.get("REPRISE_VIDEO_RUN", "released-v8")
WORK.mkdir(parents=True, exist_ok=True)
OUT.parent.mkdir(exist_ok=True)
FONT = "/System/Library/Fonts/HelveticaNeue.ttc"


def f(size, bold=False):
    return ImageFont.truetype(FONT, size, index=1 if bold else 0)


def command(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path):
    output = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                      "format=duration", "-of", "default=nw=1:nk=1",
                                      str(path)], text=True)
    return float(output.strip())


def levels(path):
    with wave.open(str(path)) as w:
        width, rate, channels = w.getsampwidth(), w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    else:
        raise ValueError(f"Unsupported WAV width: {width}")
    data = data.reshape(-1, channels).mean(axis=1)
    step = rate // 4
    values = np.array([np.sqrt(np.mean(data[i:i + step] ** 2))
                       for i in range(0, len(data), step)])
    return np.clip(values / max(0.005, np.percentile(values, 97)), 0, 1)


def frame(title, caption, second, total):
    im = Image.new("RGB", (1280, 720), "#F7F9F6")
    d = ImageDraw.Draw(im)
    d.text((64, 32), "REPRISE  /  SAMSUNG THEME 05", font=f(20, True), fill="#3159D9")
    d.text((1210, 32), f"{second:02d} / {total:02d} s", anchor="ra", font=f(19), fill="#71818A")
    d.text((64, 106), title, font=f(41, True), fill="#132B36")
    d.text((66, 168), caption, font=f(20), fill="#71818A")
    return im, d


def panel(d, y, label, values, second, color):
    d.rounded_rectangle((64, y, 1215, y + 142), radius=18,
                        fill="white", outline="#DDE6E3", width=2)
    d.text((90, y + 14), label, font=f(20, True), fill="#132B36")
    middle = y + 88
    d.line((90, middle, 1185, middle), fill="#D1DCDA", width=2)
    for i, amplitude in enumerate(values):
        x = 91 + int(i * 1092 / len(values))
        height = 4 + int(amplitude * 44)
        d.line((x, middle - height, x, middle + height),
               fill=color if i / 4 <= second else "#C7D3D5", width=2)
    x = 91 + int(min(1, second / (len(values) / 4)) * 1092)
    d.line((x, y + 58, x, y + 120), fill="#132B36", width=3)


def encode_frames(name, folder, audio_inputs, length):
    output = WORK / f"{name}.mp4"
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", "1", "-i", str(folder / "%04d.png")]
    for audio in audio_inputs:
        args += ["-i", str(audio)]
    if len(audio_inputs) == 2:
        args += ["-filter_complex",
                 "[1:a]volume=0.9[u];[2:a]volume=1.25[a];"
                 "[u][a]amix=inputs=2:duration=longest:dropout_transition=0[m]",
                 "-map", "0:v", "-map", "[m]"]
    else:
        args += ["-map", "0:v", "-map", "1:a"]
    args += ["-t", str(length), "-r", "24", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", str(output)]
    command(*args)
    return output


def replay(name, title, source, response, events, offset=0, caption=None):
    length = min(duration(source), duration(response))
    user, agent = levels(source), levels(response)
    trace = [json.loads(line) for line in events.read_text().splitlines()]
    folder = WORK / name
    folder.mkdir(exist_ok=True)
    for second in range(math.ceil(length)):
        im, d = frame(title, caption or "Recorded benchmark speech and agent output; actions from the execution trace",
                      second, math.ceil(length))
        panel(d, 230, "USER AUDIO", user, second, "#3159D9")
        panel(d, 390, "AGENT AUDIO", agent, second, "#25AB80")
        d.rounded_rectangle((64, 550, 1215, 673), radius=18,
                            fill="white", outline="#DDE6E3", width=2)
        d.text((90, 568), "EXECUTED ACTIONS", font=f(20, True), fill="#132B36")
        actions = [e for e in trace if e.get("event") == "tool_executed"
                   and e.get("elapsed_s", 999) - offset <= second]
        label = "   •   ".join(e["function"] + "(" + ", ".join(map(str, e.get("args", {}).values())) + ")"
                               for e in actions[-3:]) if actions else "Listening and resolving the request…"
        d.text((90, 614), label[:111], font=f(19), fill="#3159D9")
        im.save(folder / f"{second:04d}.png")
    return encode_frames(name, folder, [source, response], length)


def narration(name, title, lines):
    audio = WORK / f"{name}.aiff"
    command("say", "-v", "Samantha", "-r", "145", "-o", str(audio), " ".join(lines))
    length = duration(audio)
    folder = WORK / name
    folder.mkdir(exist_ok=True)
    for second in range(math.ceil(length)):
        im, d = frame(title, "Synthetic framing narration; demonstration audio comes from real agent sessions",
                      second, math.ceil(length))
        d.rounded_rectangle((64, 245, 1215, 590), radius=18, fill="white", outline="#DDE6E3")
        for i, line in enumerate(lines):
            d.text((92, 296 + i * 78), line[:92], font=f(27, i == 0),
                   fill="#132B36" if i == 0 else "#3159D9")
        d.rounded_rectangle((92, 638, 1180, 646), radius=4, fill="#DDE6E3")
        d.rounded_rectangle((92, 638, 92 + int(1088 * second / length), 646),
                            radius=4, fill="#3159D9")
        im.save(folder / f"{second:04d}.png")
    return encode_frames(name, folder, [audio], length)


report = json.loads((ROOT / "runs" / RUN / "report.json").read_text())
def benchmark(sample, title):
    item = next(r for r in report["results"] if r["sample"] == sample)
    if not item["passed"]:
        raise RuntimeError(f"Video example must be an actually passing run: {sample}")
    case = ROOT / "runs" / RUN / sample
    return replay(sample, title,
                  ROOT / "data/fdb_v3_data_released" / sample / "input.wav",
                  case / "output_reprise.wav", case / "events.jsonl")


clips = [
    narration("intro", "Hear the agent handle corrections", [
        "This replay uses the benchmark's real recorded speech.",
        "Agent replies and tool actions came from running sessions.",
        "Only this framing narration is synthetic.",
        "Watch how changed requests still lead to the right final actions.",
    ]),
    benchmark("travel_21_69a9cf80f4d7668d5c815038",
              "Flight booking and visa update after hesitations"),
    benchmark("ecommerce_13_61517db6a7589569521b2356",
              "Cart and order actions after false starts"),
    narration("transition", "Beyond the benchmark: washer help", [
        "The next voice-only use case also ran end to end.",
        "A synthetic test voice corrects the washer code from four C to five C.",
        "The reply you hear is the recorded Gemini agent response.",
    ]),
]
extension_events = sorted((ROOT / "runs").glob("reprise-demo-*/events.jsonl"))[-1]
clips.append(replay("washer-extension", "Washer help: 4C corrected to 5C",
                    ROOT / "runs/extension-question.wav",
                    ROOT / "runs/extension-response.wav", extension_events, offset=7,
                    caption="Synthetic test voice and recorded agent output; actions from the execution trace"))
clips.append(narration("outro", "Reproduce and inspect the result", [
    "The reproduction command tests all one hundred recordings.",
    "Each case preserves audio and its actual tool execution trace.",
    "The organizer rerun determines the official score and ranking.",
    "The local score in the report is an exact-match development measure, not the final judge score.",
]))
concat = WORK / "concat.txt"
concat.write_text("".join(f"file '{clip}'\n" for clip in clips))
filters = []
for index in range(len(clips)):
    filters.extend((f"[{index}:v]setpts=PTS-STARTPTS[v{index}]",
                    f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[a{index}]"))
filters.append("".join(f"[v{i}][a{i}]" for i in range(len(clips)))
               + f"concat=n={len(clips)}:v=1:a=1[v][a]")
concat_args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
for clip in clips:
    concat_args.extend(("-i", str(clip)))
concat_args.extend(("-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(OUT)))
command(*concat_args)
total = duration(OUT)
if not 180 <= total <= 300:
    raise RuntimeError(f"Demo must be 3–5 minutes; got {total:.1f}s")
print(json.dumps({"video": str(OUT), "duration_s": round(total, 1),
                  "source_run": RUN, "real_agent_audio": True,
                  "synthetic_narration_disclosed": True}))
