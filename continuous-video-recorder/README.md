# Continuous video recorder for Raspberry Pi 5

A separate continuous-recording GUI inspired by the blue header, grouped experiment fields, settings files, and scrolling form in yashsondhi/diel-light-pi. The original Motion workflow is not required. This records video regardless of whether animals move. It does not control lighting, infrared lamps, or an IR-cut filter.

## Defaults

- 35 minutes total, 25 frames per second requested.
- 1920 × 1080 pixels, H.264 video inside MP4, no audio.
- 6 Mbps target bitrate, about 1.58 GB for 35 minutes before overhead. Actual size varies.
- Approximately five minutes per file. Files split at keyframes, so lengths and the final file count can differ slightly.
- One continuously running camera feeds FFmpeg, which packages the encoded video into consecutive files without re-encoding or restarting the camera between files.
- 20, 25, or 30 fps and 720p or 1080p are selectable. 720p is the fallback if 1080p cannot maintain the required frame rate.

Actual FPS depends on the camera, exposure, encoding load, and storage speed. The GUI supports CSI camera modules through rpicam and USB webcams through V4L2.

## 1. Install on the Raspberry Pi

Run these commands in a terminal **on the Pi**, not in Windows. No pip environment is required.

```bash
sudo apt update
sudo apt install python3-tk ffmpeg v4l-utils
# For CSI camera modules only
sudo apt install rpicam-apps
```

Copy/extract this folder onto the Pi, for example into `~/Desktop/continuous-video-recorder`.

## 2. Check the camera first

### USB webcam

```bash
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --list-formats-ext
ffplay -hide_banner -f v4l2 -i /dev/video0
```

Replace `/dev/video0` with your webcam's capture path. Put this path in **USB device path** in the GUI. Match the resolution and frame rate to a supported mode. Choose **mjpeg** for an advertised MJPG mode or **yuyv422** for YUYV. Start with **auto** if you have no format preference. Use the **10-second camera preview** button before recording. Close other apps using the camera, including Motion or any terminal preview, before starting.

### CSI camera module

Leave **USB device path** blank and select the CSI camera index.

```bash
rpicam-hello --list-cameras
rpicam-hello --timeout 10000
```


## 3. Launch the GUI

```bash
cd ~/Desktop/continuous-video-recorder
python3 continuous_video_gui.py
```

The GUI runs on the Pi desktop, including a remote desktop session. It does not run the Pi camera from your Windows computer directly.

## 4. Run a short trial before the experiment

1. Enter initials, a trial number, and an output folder. Enter a **Project ID / name** to include it in the trial folder and MP4 filenames. Spaces become underscores. Leave it blank to use the original naming format. Optional metadata fields are saved with the run.
2. Leave the date blank to use the Pi's local date when recording starts. Ensure its clock and timezone are correct. Or enter an explicit date such as `20261012`.
3. For the first test, set duration to **1 minute** and file length to **0.25 minutes**. Use a resolution and frame rate your camera supports, and start with 6 Mbps.
4. Use the 10-second camera preview to check framing and focus. Wait for it to finish.
5. Click **Start recording**. The GUI shows elapsed wall time and files created, not a measurement of actual capture FPS.
6. Wait for **Completed** before moving files. MP4 files still being recorded might not play until finalized.
7. Open the videos in order. Check detail, focus, motion, and visibility under the experiment's actual day/night illumination.
8. Run the verification command below. Check measured FPS and timestamp gaps before relying on the recordings for quantitative behavior analysis.
9. Change duration to **35 minutes**, file length to **5 minutes**, and select a new trial number for the experiment.

**Stop recording** ends the trial early and lets the final MP4 finish. Closing the GUI during recording asks whether to stop and finalize. Avoid powering off during recording; abrupt power loss may damage the current MP4.

## Naming and output

With initials `KO`, date `20261012`, and trial `1`, the selected output folder contains:

```
KO_20261012_Prey_Choice_trial1/
    KO_20261012_Prey_Choice_trial1_001.mp4
    KO_20261012_Prey_Choice_trial1_002.mp4
    ...
    recording.json
    segments.csv
    camera.log
    ffmpeg.log
```

The trial directory is reserved atomically. An existing trial directory causes an error instead of overwriting data. Increase the trial number, or choose another output folder. Date is fixed at trial start, even if recording crosses midnight. The JSON records settings, commands, local start/end time, completion status and errors. The CSV records segment start/end times on the source video timeline; each MP4's internal timestamps restart near zero.

**Save settings** saves a reusable JSON configuration. **Open settings** restores it. Defaults return when the program restarts unless you open your saved settings.

## Verify videos

Run after the trial completes:

```bash
python3 check_recording.py ~/Videos/experiments/KO_20261012_Prey_Choice_trial1
```

This reads the segment CSV and decodes each MP4 with ffprobe to report frame count, duration, measured frame rate and unusually large timestamp intervals, including at file boundaries. For a full 35-minute trial at 25 fps, approximately 52,500 frames are expected. Camera startup and the duration timer can produce a small difference. The requested FPS alone does not prove all frames were captured. Timestamp checks flag suspicious gaps; they cannot establish that every sensor exposure reached the encoder.

## Size and image quality

Approximate total video sizes for 35 minutes:

| Target bitrate | Approximate size |
| --- | --- |
| 4 Mbps | 1.05 GB |
| 6 Mbps (default) | 1.58 GB |
| 8 Mbps | 2.10 GB |

Changing resolution alone does not determine file size when a target bitrate is set. If compression hides tentacle movements or other small details, increase bitrate to 8 Mbps and test. If performance is poor, use 720p and test again. Use appropriate Pi 5 cooling and storage that can sustain the write rate. The recorder checks space before starting and while recording.

Exposure is automatic. This recorder does not supply illumination. In low light, confirm that actual capture remains fast enough and motion is clear. It does not burn timestamps or labels into the images.

## Troubleshooting

- **Missing rpicam-vid / ffmpeg**: install the packages in step 1.
- **Camera in use**: close other previews and recorders, including Motion.
- **Camera failed**: read `camera.log` in the trial folder.
- **Video writer failed**: read `ffmpeg.log`. Your rpicam build must support the libav encoder.
- **Trial already exists**: use a new trial number. Failed attempts also retain their logs in the reserved folder.
- **No graphical display**: launch on the Pi desktop or remote desktop.
- **Wrong date in filenames**: check the Pi clock/timezone or enter an explicit date.

## Validation and limits

The recording pipeline and MP4 splitting were tested locally with synthetic timestamped video, including frame retention across segment boundaries, early stop, output collision protection and process failure cleanup. No Raspberry Pi or Arducam hardware was available here. A short recording on your hardware is required to validate camera compatibility, real capture FPS and image quality.

## References

- Original GUI inspiration: https://github.com/yashsondhi/diel-light-pi/tree/master/activity-monitoring
- Raspberry Pi camera software: https://www.raspberrypi.com/documentation/computers/camera_software.html
- FFmpeg segment muxer: https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment
