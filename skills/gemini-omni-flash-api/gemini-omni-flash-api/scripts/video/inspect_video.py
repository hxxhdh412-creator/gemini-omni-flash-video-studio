#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys


def get_ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def get_ffprobe_exe():
    return shutil.which("ffprobe")


def format_size(size_bytes):
    """Formats file size in bytes to a human-readable string."""
    try:
        size_bytes = int(size_bytes)
    except (ValueError, TypeError):
        return "Unknown size"

    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def parse_fps(fps_str):
    """Parses fractional frame rates like '30/1' or '24000/1001' into floats."""
    if not fps_str:
        return "Unknown"
    if "/" in fps_str:
        try:
            num, den = map(float, fps_str.split("/"))
            if den != 0:
                val = num / den
                if val.is_integer():
                    return f"{int(val)} fps"
                return f"{val:.2f} fps"
        except (ValueError, ZeroDivisionError):
            pass
    try:
        val = float(fps_str)
        if val.is_integer():
            return f"{int(val)} fps"
        return f"{val:.2f} fps"
    except ValueError:
        return fps_str


def inspect_via_ffmpeg_stderr(file_path, raw=False):
    """Fallback video inspector using ffmpeg -i when ffprobe is not installed."""
    ffmpeg_exe = get_ffmpeg_exe()
    if not ffmpeg_exe:
        raise RuntimeError("Neither ffprobe nor ffmpeg (or imageio_ffmpeg) is available.")

    proc = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-i", file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    stderr = proc.stderr or ""
    if raw:
        return {"ffmpeg_stderr": stderr}

    size_bytes = os.path.getsize(file_path)
    size_str = format_size(size_bytes)

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if dur_match:
        h, m, s = float(dur_match.group(1)), float(dur_match.group(2)), float(dur_match.group(3))
        duration_val = h * 3600.0 + m * 60.0 + s
        duration_str = f"{duration_val:.2f}s"
    else:
        duration_val = 0.0
        duration_str = "Unknown"

    br_match = re.search(r"bitrate:\s*(\d+)\s*kb/s", stderr)
    bitrate_kbps = f"{br_match.group(1)} kbps" if br_match else "Unknown"

    vid_match = re.search(
        r"Stream #\d+:\d+.*?Video:\s*([a-zA-Z0-9_]+).*?,\s*(\d+)x(\d+).*?(?:,\s*(\d+(?:\.\d+)?)\s*fps)?",
        stderr,
    )
    has_video = bool(vid_match)
    video_info = {}
    if vid_match:
        codec = vid_match.group(1).upper()
        width = int(vid_match.group(2))
        height = int(vid_match.group(3))
        fps_raw = vid_match.group(4)
        fps = parse_fps(fps_raw) if fps_raw else "24 fps"
        video_info = {
            "resolution": f"{width}x{height}",
            "width": width,
            "height": height,
            "fps": fps,
            "codec": codec,
            "duration": str(duration_val),
        }

    aud_match = re.search(
        r"Stream #\d+:\d+.*?Audio:\s*([a-zA-Z0-9_]+),\s*(\d+)\s*Hz,\s*([a-zA-Z0-9_.]+)",
        stderr,
    )
    has_audio = bool(aud_match)
    audio_info = {}
    if aud_match:
        acodec = aud_match.group(1).upper()
        sr = float(aud_match.group(2)) / 1000.0
        ch_desc = aud_match.group(3).lower()
        channels = 2 if "stereo" in ch_desc else (1 if "mono" in ch_desc else ch_desc)
        audio_info = {
            "codec": acodec,
            "channels": channels,
            "sample_rate": f"{sr:.1f} kHz",
        }

    return {
        "file_name": os.path.basename(file_path),
        "file_size": size_str,
        "size_bytes": str(size_bytes),
        "duration": duration_str,
        "duration_seconds": duration_val,
        "bitrate": bitrate_kbps,
        "has_video": has_video,
        "video": video_info,
        "has_audio": has_audio,
        "audio": audio_info,
    }


def inspect_video(file_path, raw=False):
    """Runs ffprobe (or falls back to ffmpeg / imageio_ffmpeg) on the video file and returns parsed metadata."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ffprobe_exe = get_ffprobe_exe()
    if not ffprobe_exe:
        return inspect_via_ffmpeg_stderr(file_path, raw=raw)

    cmd = [
        ffprobe_exe,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        file_path,
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    data = json.loads(result.stdout)

    if raw:
        return data

    fmt = data.get("format", {})
    duration = fmt.get("duration")
    size_bytes = fmt.get("size")
    bitrate = fmt.get("bit_rate")

    size_str = format_size(size_bytes) if size_bytes else "Unknown"

    try:
        duration_val = float(duration) if duration else 0.0
        duration_str = f"{duration_val:.2f}s"
    except ValueError:
        duration_str = "Unknown"
        duration_val = None

    try:
        bitrate_kbps = f"{int(float(bitrate) / 1000)} kbps" if bitrate else "Unknown"
    except ValueError:
        bitrate_kbps = "Unknown"

    video_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]

    has_video = len(video_streams) > 0
    has_audio = len(audio_streams) > 0

    video_info = {}
    if has_video:
        v = video_streams[0]
        width = v.get("width")
        height = v.get("height")
        codec = v.get("codec_name", "Unknown").upper()
        r_fps = parse_fps(v.get("r_frame_rate"))
        avg_fps = parse_fps(v.get("avg_frame_rate"))

        fps = r_fps if r_fps != "0 fps" and r_fps != "Unknown" else avg_fps

        video_info = {
            "resolution": f"{width}x{height}" if width and height else "Unknown",
            "width": width,
            "height": height,
            "fps": fps,
            "codec": codec,
            "duration": v.get("duration"),
        }

    audio_info = {}
    if has_audio:
        a = audio_streams[0]
        codec = a.get("codec_name", "Unknown").upper()
        channels = a.get("channels", "Unknown")
        sample_rate = a.get("sample_rate")
        sample_rate_khz = f"{float(sample_rate)/1000:.1f} kHz" if sample_rate else "Unknown"

        audio_info = {
            "codec": codec,
            "channels": channels,
            "sample_rate": sample_rate_khz,
        }

    return {
        "file_name": os.path.basename(file_path),
        "file_size": size_str,
        "size_bytes": size_bytes,
        "duration": duration_str,
        "duration_seconds": duration_val,
        "bitrate": bitrate_kbps,
        "has_video": has_video,
        "video": video_info,
        "has_audio": has_audio,
        "audio": audio_info,
    }


def print_terminal_report(info):
    """Prints an aligned terminal report."""
    print(f"\nVideo Inspection Report: {info['file_name']}")
    print("=" * 50)
    print(f"File Size   : {info['file_size']}")
    print(f"Duration    : {info['duration']}")
    print(f"Bitrate     : {info['bitrate']}")

    print("\nVideo Stream Details:")
    if info["has_video"]:
        v = info["video"]
        print(f"  * Resolution : {v['resolution']}")
        print(f"  * Frame Rate : {v['fps']}")
        print(f"  * Codec      : {v['codec']}")
    else:
        print("  * No Video Stream Found.")

    print("\nAudio Stream Details:")
    if info["has_audio"]:
        a = info["audio"]
        print("  * Status     : Audio Present")
        print(f"  * Codec      : {a['codec']}")
        print(f"  * Channels   : {a['channels']}")
        print(f"  * Sample Rate: {a['sample_rate']}")
    else:
        print("  * Status     : No Audio Stream Present")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Inspect video details (duration, frame rate, resolution, audio presence) using ffprobe or imageio_ffmpeg."
    )
    parser.add_argument("file", help="Path to the video file to inspect")
    parser.add_argument("--json", action="store_true", help="Output parsed summary in JSON format")
    parser.add_argument("--raw", action="store_true", help="Output raw unmodified ffprobe/ffmpeg data")

    args = parser.parse_args()

    try:
        if args.raw:
            info = inspect_video(args.file, raw=True)
            print(json.dumps(info, indent=2))
        else:
            info = inspect_video(args.file, raw=False)
            if args.json:
                print(json.dumps(info, indent=2))
            else:
                print_terminal_report(info)
    except Exception as e:
        print(f"Error inspecting video: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
