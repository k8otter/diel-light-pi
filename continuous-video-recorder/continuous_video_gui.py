#!/usr/bin/env python3
"""Timed CSI-camera recording for Raspberry Pi 5. Run on the Pi desktop."""
import json
import math
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


@dataclass
class Settings:
    initials: str = "KO"
    date: str = ""  # Empty means the Pi's local date at recording start.
    trial: int = 1
    output: str = str(Path.home() / "Videos" / "experiments")
    duration_minutes: float = 35
    segment_minutes: float = 5
    fps: int = 25
    resolution: str = "1920x1080"
    bitrate_mbps: float = 6
    camera: int = 0
    preview: bool = False
    experimenter: str = ""
    project: str = ""
    location: str = ""
    organism: str = ""
    notes: str = ""

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.initials):
            raise ValueError("Initials must contain only letters, numbers, underscores or hyphens.")
        if self.date:
            if not re.fullmatch(r"\d{8}", self.date):
                raise ValueError("Date must be YYYYMMDD, or blank for today's date.")
            datetime.strptime(self.date, "%Y%m%d")
        for name, value, low, high in (
            ("Trial", self.trial, 1, 999999), ("Frame rate", self.fps, 20, 30),
            ("Duration (minutes)", self.duration_minutes, 0.01, 1440),
            ("Segment (minutes)", self.segment_minutes, 0.01, 1440),
            ("Bitrate (Mbps)", self.bitrate_mbps, 1, 50), ("Camera", self.camera, 0, 9),
        ):
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}.")
        if self.resolution not in ("1920x1080", "1280x720"):
            raise ValueError("Choose 1920x1080 or 1280x720.")
        if not self.output.strip():
            raise ValueError("Choose an output folder.")

    def prefix(self):
        date = self.date or datetime.now().strftime("%Y%m%d")
        return f"{self.initials}_{date}_trial{self.trial}"

    def estimated_bytes(self):
        return int(self.bitrate_mbps * 1_000_000 / 8 * self.duration_minutes * 60)


def camera_command(s):
    width, height = s.resolution.split("x")
    cmd = ["rpicam-vid", "--camera", str(s.camera), "--timeout", str(round(s.duration_minutes * 60000)),
           "--width", width, "--height", height, "--framerate", str(s.fps),
           "--bitrate", str(round(s.bitrate_mbps * 1_000_000)), "--codec", "libav",
           "--libav-format", "mpegts", "--intra", str(s.fps), "--output", "-"]
    if not s.preview:
        cmd.append("--nopreview")
    return cmd


def segment_command(s, folder):
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-n",
            "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0", "-c:v", "copy", "-an",
            "-f", "segment", "-segment_format", "mp4", "-segment_time", str(s.segment_minutes * 60),
            "-segment_start_number", "1", "-reset_timestamps", "1",
            "-segment_list", str(folder / "segments.csv"), "-segment_list_type", "csv",
            str(folder / f"{s.prefix()}_%03d.mp4")]


def finish_process(proc, timeout=15):
    if proc is None:
        return
    if proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def record(s, stop, report):
    """One camera process feeds one segmenter. No restarts between MP4 files."""
    s.validate()
    for executable in ("rpicam-vid", "ffmpeg"):
        if not shutil.which(executable):
            raise RuntimeError(f"Missing {executable}. Follow the installation steps in README.md.")
    base = Path(s.output).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    if "%" in str(base):
        raise ValueError("The output folder cannot contain % (used for video numbering).")
    needed = int(s.estimated_bytes() * 1.3) + 200_000_000
    if shutil.disk_usage(base).free < needed:
        raise RuntimeError(f"Allow at least {needed / 1e9:.1f} GB of free space for this run.")
    folder = base / s.prefix()
    folder.mkdir()  # Atomic reservation. Never overwrite or mix trials.
    cam_cmd, seg_cmd = camera_command(s), segment_command(s, folder)
    metadata = {"settings": asdict(s), "prefix": s.prefix(), "started_local": datetime.now().astimezone().isoformat(),
                "status": "starting", "camera_command": cam_cmd, "segment_command": seg_cmd}
    metadata_file = folder / "recording.json"
    metadata_file.write_text(json.dumps(metadata, indent=2) + "\n")
    camera = muxer = None
    started = time.monotonic()
    error = None
    forced = False
    try:
        with (folder / "camera.log").open("wb") as cam_log, (folder / "ffmpeg.log").open("wb") as ff_log:
            try:
                camera = subprocess.Popen(cam_cmd, stdout=subprocess.PIPE, stderr=cam_log, start_new_session=True)
                muxer = subprocess.Popen(seg_cmd, stdin=camera.stdout, stdout=subprocess.DEVNULL,
                                         stderr=ff_log, start_new_session=True)
                camera.stdout.close()  # FFmpeg owns the read side; propagate failure to producer.
                metadata["status"] = "recording"
                metadata_file.write_text(json.dumps(metadata, indent=2) + "\n")
                while camera.poll() is None:
                    if muxer.poll() is not None:
                        raise RuntimeError("Video writer stopped early. See ffmpeg.log in the trial folder.")
                    if stop.is_set():
                        report(f"Stopping and finishing MP4 files — {folder}")
                        finish_process(camera)
                        break
                    elapsed = time.monotonic() - started
                    if elapsed > s.duration_minutes * 60 + 45:
                        forced = True
                        raise RuntimeError("Camera exceeded the requested duration plus startup allowance.")
                    if shutil.disk_usage(base).free < 100_000_000:
                        raise RuntimeError("Recording stopped because less than 100 MB of storage remains.")
                    count = len(list(folder.glob("*.mp4")))
                    report(f"Recording • {elapsed / 60:.1f} min elapsed • {count} files • {folder}")
                    stop.wait(0.5)
                if not stop.is_set() and camera.returncode != 0:
                    raise RuntimeError("Camera recording failed. See camera.log in the trial folder.")
                try:
                    muxer.wait(timeout=30)  # EOF flushes the final MP4 trailer.
                except subprocess.TimeoutExpired:
                    forced = True
                    raise RuntimeError("Video writer did not finish. Check the final MP4 and ffmpeg.log.")
                if muxer.returncode != 0:
                    raise RuntimeError("MP4 writer failed. See ffmpeg.log in the trial folder.")
                if not list(folder.glob("*.mp4")):
                    raise RuntimeError("No video was recorded. See the trial logs.")
            finally:
                finish_process(camera)
                if camera is not None and camera.stdout is not None:
                    camera.stdout.close()
                # Give the writer a chance to finalize after camera errors or manual stop.
                if muxer is not None and muxer.poll() is None:
                    try:
                        muxer.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        forced = True
                        finish_process(muxer)
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        metadata.update(status="failed" if error else ("stopped" if stop.is_set() else "completed"),
                        ended_local=datetime.now().astimezone().isoformat(),
                        elapsed_wall_seconds=round(time.monotonic() - started, 3), error=error,
                        forced_shutdown=forced,
                        camera_exit_code=camera.returncode if camera else None,
                        ffmpeg_exit_code=muxer.returncode if muxer else None)
        metadata_file.write_text(json.dumps(metadata, indent=2) + "\n")
    return folder


class App:
    def __init__(self, root):
        self.root = root
        self.running = False
        self.closing = False
        self.stop = threading.Event()
        self.pending_status = "Ready to configure"
        self.result = None
        self.preview_process = None
        self.vars = {}
        root.title("Continuous Video Recording")
        root.geometry("800x760")
        root.minsize(620, 600)
        root.configure(bg="#eef2f5")
        style = ttk.Style(root)
        style.configure("Section.TLabelframe", background="white")
        style.configure("Section.TLabelframe.Label", foreground="#17324d", font=("Segoe UI", 10, "bold"))
        style.configure("Body.TLabel", background="white", foreground="#273746")
        header = tk.Frame(root, bg="#17324d", padx=24, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="Continuous Video Recording", bg="#17324d", fg="white", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(header, text="Set up a trial, record continuously, and save numbered MP4 files.", bg="#17324d", fg="#c9d8e5").pack(anchor="w")
        tools = ttk.Frame(root, padding=(18, 10))
        tools.pack(fill="x")
        self.controls = []
        for text, command in (("Open settings", self.load), ("Save settings", self.save), ("10-second camera preview", self.preview)):
            button = ttk.Button(tools, text=text, command=command)
            button.pack(side="left", padx=(0, 8))
            self.controls.append(button)
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=18)
        canvas = tk.Canvas(container, bg="#eef2f5", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        form = ttk.Frame(canvas)
        item = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
        defaults = asdict(Settings())
        groups = (("Trial details", (("initials", "Initials *"), ("date", "Date YYYYMMDD (blank = today)"),
                  ("trial", "Trial number *"), ("experimenter", "Experimenter"), ("project", "Project"),
                  ("location", "Location"), ("organism", "Organism"), ("notes", "Notes"), ("output", "Output folder *"))),
                  ("Recording settings", (("duration_minutes", "Duration (minutes)"), ("segment_minutes", "File length (minutes)"),
                  ("fps", "Frames per second"), ("resolution", "Resolution"), ("bitrate_mbps", "Target bitrate (Mbps)"),
                  ("camera", "Camera index"))))
        for title, fields in groups:
            group = ttk.LabelFrame(form, text=title, style="Section.TLabelframe", padding=14)
            group.pack(fill="x", pady=(0, 10))
            group.columnconfigure(1, weight=1)
            for row, (key, label) in enumerate(fields):
                ttk.Label(group, text=label, style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 15))
                var = tk.StringVar(value=str(defaults[key]))
                self.vars[key] = var
                if key in ("fps", "resolution"):
                    control = ttk.Combobox(group, textvariable=var, state="readonly", values=(20, 25, 30) if key == "fps" else ("1920x1080", "1280x720"))
                else:
                    control = ttk.Entry(group, textvariable=var)
                control.grid(row=row, column=1, sticky="ew", pady=4)
                self.controls.append(control)
                if key == "output":
                    browse = ttk.Button(group, text="Browse", command=self.browse)
                    browse.grid(row=row, column=2, padx=(8, 0))
                    self.controls.append(browse)
                var.trace_add("write", lambda *args: self.update_estimate())
        self.preview_var = tk.BooleanVar(value=False)
        check = ttk.Checkbutton(form, text="Show camera preview during recording (separate window)", variable=self.preview_var)
        check.pack(anchor="w", pady=5)
        self.controls.append(check)
        self.estimate = tk.StringVar()
        ttk.Label(form, textvariable=self.estimate, wraplength=650).pack(anchor="w", pady=8)
        footer = ttk.Frame(root, padding=18)
        footer.pack(fill="x")
        self.status = tk.StringVar(value=self.pending_status)
        ttk.Label(footer, textvariable=self.status, wraplength=720).pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(footer, text="Start recording", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(footer, text="Stop recording", command=self.stop.set, state="disabled")
        self.stop_button.pack(side="left", padx=10)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.update_estimate()
        root.after(200, self.poll)

    def settings(self):
        values = {key: var.get().strip() for key, var in self.vars.items()}
        for key in ("trial", "fps", "camera"):
            values[key] = int(values[key])
        for key in ("duration_minutes", "segment_minutes", "bitrate_mbps"):
            values[key] = float(values[key])
        values["preview"] = self.preview_var.get()
        s = Settings(**values)
        s.validate()
        return s

    def update_estimate(self):
        if not hasattr(self, "estimate"):
            return
        try:
            s = self.settings()
            self.estimate.set(f"First file • {s.prefix()}_001.mp4\nEstimated video size • {s.estimated_bytes() / 1e9:.2f} GB (actual size varies)")
        except (ValueError, TypeError):
            self.estimate.set("Complete the settings to preview the filename and estimated size.")

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.vars["output"].set(folder)

    def save(self):
        try:
            s = self.settings()
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Recorder settings", "*.json")])
            if path:
                Path(path).write_text(json.dumps(asdict(s), indent=2) + "\n")
                self.pending_status = f"Settings saved • {path}"
        except (ValueError, OSError) as exc:
            messagebox.showerror("Settings", str(exc))

    def load(self):
        path = filedialog.askopenfilename(filetypes=[("Recorder settings", "*.json")])
        if not path:
            return
        try:
            s = Settings(**json.loads(Path(path).read_text()))
            s.validate()
            for key, var in self.vars.items():
                var.set(str(getattr(s, key)))
            self.preview_var.set(s.preview)
            self.pending_status = f"Settings loaded • {path}"
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showerror("Settings", str(exc))

    def preview(self):
        try:
            s = self.settings()
            if not shutil.which("rpicam-hello"):
                raise RuntimeError("Missing rpicam-hello. Install rpicam-apps first.")
            if self.preview_process and self.preview_process.poll() is None:
                return
            self.preview_process = subprocess.Popen(["rpicam-hello", "--camera", str(s.camera), "--timeout", "10000"])
            self.pending_status = "Camera preview open for 10 seconds. Errors appear in the launching terminal."
        except (ValueError, RuntimeError, OSError) as exc:
            messagebox.showerror("Camera preview", str(exc))

    def set_busy(self, busy):
        self.running = busy
        for control in self.controls:
            control.configure(state="disabled" if busy else ("readonly" if isinstance(control, ttk.Combobox) else "normal"))
        self.start_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def start(self):
        if self.preview_process and self.preview_process.poll() is None:
            messagebox.showinfo("Preview", "Wait for the 10-second preview to finish before recording.")
            return
        try:
            s = self.settings()
            # Freeze date once for this run, including a possible midnight crossing.
            s.date = s.date or datetime.now().strftime("%Y%m%d")
            self.stop.clear()
            self.set_busy(True)
            self.pending_status = "Starting camera…"
            threading.Thread(target=self.worker, args=(s,), daemon=True).start()
        except ValueError as exc:
            messagebox.showerror("Recording settings", str(exc))

    def worker(self, s):
        try:
            folder = record(s, self.stop, lambda text: setattr(self, "pending_status", text))
            self.result = (True, f"{'Stopped' if self.stop.is_set() else 'Completed'} • {folder}")
        except Exception as exc:
            self.result = (False, str(exc))

    def poll(self):
        self.status.set(self.pending_status)
        if self.result is not None:
            ok, text = self.result
            self.result = None
            self.set_busy(False)
            self.pending_status = text
            self.status.set(text)
            if not ok and not self.closing:
                messagebox.showerror("Recording failed", text)
        if self.closing and not self.running:
            self.root.destroy()
            return
        self.root.after(200, self.poll)

    def close(self):
        if self.running:
            if not messagebox.askyesno("Recording in progress", "Stop recording, finish the video files, and close?"):
                return
            self.closing = True
            self.stop.set()
            self.pending_status = "Finishing video files before closing…"
        else:
            if self.preview_process:
                finish_process(self.preview_process)
            self.root.destroy()


if __name__ == "__main__":
    App(tk.Tk())
    tk.mainloop()
