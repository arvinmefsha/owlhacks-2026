"""Presentation timestamps, never an average-FPS reconstruction of a VFR clip."""
from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
import sys

import numpy as np


@dataclass
class VideoTimeline:
    times: np.ndarray
    origin_seconds: float
    source: str = "container-pts-v1"

    def metadata(self) -> dict:
        return {"source": self.source, "origin_seconds": self.origin_seconds,
                "frames": len(self.times), "variable_rate": bool(
                    len(self.times) > 2 and np.ptp(np.diff(self.times)) > 0.001)}


def read_timeline(path: str | Path) -> VideoTimeline:
    """Decode in presentation order. Preserve video/audio start offsets.

    Missing/duplicate PTS cannot safely be reconstructed from average FPS. Fail
    explicitly rather than silently storing shifted poses. OpenCV still decodes
    the images so its established orientation handling remains unchanged.
    """
    # PyAV and OpenCV wheel builds ship different FFmpeg libraries. Isolate the
    # decoder to avoid duplicate Objective-C AVFoundation classes on macOS.
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(path)],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(f"Cannot read video presentation timestamps: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    return VideoTimeline(np.asarray(data["times"], dtype=float), data["origin_seconds"])


def _decode(path):
    import av
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("The clip has no video stream.")
        origin = float(container.start_time / av.time_base) if container.start_time is not None else 0.0
        times = []
        for frame in container.decode(video=0):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Video timestamps are missing. Export this clip as MP4 and try again.")
            times.append(float(frame.pts * frame.time_base) - origin)
    values = np.asarray(times, dtype=float)
    if not len(values) or not np.isfinite(values).all() or np.any(np.diff(values) <= 0) or values[0] < -1e-5:
        raise ValueError("Video timestamps are ambiguous. Export this clip as MP4 and try again.")
    return {"times": np.maximum(values, 0.0).tolist(), "origin_seconds": origin}


if __name__ == "__main__":
    try:
        print(json.dumps(_decode(sys.argv[1])))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
