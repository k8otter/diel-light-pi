#!/usr/bin/env python3
"""Read-only verification of finalized MP4 segments using frame timestamps."""
import argparse
import csv
import json
import subprocess
from pathlib import Path


def inspect(folder):
    metadata = json.loads((folder / "recording.json").read_text())
    fps = metadata["settings"]["fps"]
    total = 0
    previous_last = None
    gaps = []
    with (folder / "segments.csv").open(newline="") as handle:
        segments = list(csv.reader(handle))
    if not segments:
        raise ValueError("No finalized segments are listed.")
    for filename, start, end in segments:
        path = folder / Path(filename).name
        result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                                 "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)],
                                check=True, capture_output=True, text=True)
        pts = [float(frame["best_effort_timestamp_time"]) for frame in json.loads(result.stdout)["frames"]
               if "best_effort_timestamp_time" in frame]
        if not pts:
            raise ValueError(f"No frame timestamps in {path.name}.")
        deltas = [b - a for a, b in zip(pts, pts[1:])]
        local_gaps = sum(delta > 1.5 / fps for delta in deltas)
        measured = (len(pts) - 1) / (pts[-1] - pts[0]) if len(pts) > 1 and pts[-1] > pts[0] else 0
        print(f"{path.name}: {len(pts)} frames, {float(end)-float(start):.3f} s, {measured:.2f} fps, {local_gaps} large intervals")
        # Segment list supplies the original timeline, while each MP4 resets PTS.
        first_global = float(start)
        last_global = first_global + pts[-1] - pts[0]
        if previous_last is not None:
            delta = first_global - previous_last
            if delta > 1.5 / fps or delta <= 0:
                gaps.append((path.name, delta))
        previous_last = last_global
        total += len(pts)
        if deltas and any(delta <= 0 for delta in deltas):
            print("  WARNING: non-increasing frame timestamps.")
    print(f"Total frames: {total}; requested FPS: {fps}; run status: {metadata['status']}")
    for name, delta in gaps:
        print(f"WARNING: suspicious file-boundary interval before {name}: {delta:.6f} s")
    if not gaps:
        print("No suspicious file-boundary intervals detected.")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial_folder", type=Path)
    args = parser.parse_args()
    try:
        inspect(args.trial_folder.expanduser())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Verification failed: {exc}\n")
