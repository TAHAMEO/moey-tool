"""Source analysis: scenes, visual features, audio energy and audio events.

All local and free. Results are stored as reusable ``Analysis`` rows keyed by
source, so a resubmitted link never re-analyses the same media.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from . import ffmpeg_tools, models

log = logging.getLogger(__name__)

# YAMNet classes that matter for highlight scoring.
EVENT_CLASSES = {
    "Laughter": "laughter", "Giggle": "laughter", "Chuckle, chortle": "laughter", "Belly laugh": "laughter",
    "Applause": "applause", "Clapping": "applause", "Cheering": "cheering", "Crowd": "crowd",
    "Music": "music", "Singing": "music", "Shout": "shout", "Yell": "shout", "Screaming": "shout",
    "Gasp": "gasp", "Silence": "silence", "Speech": "speech",
}


def detect_scenes(video: Path, *, threshold: float = 27.0, min_scene_len_s: float = 1.0) -> list[dict]:
    """Scene cuts with PySceneDetect's ContentDetector (run on the low-res proxy)."""
    from scenedetect import ContentDetector, SceneManager, open_video

    stream = open_video(str(video), backend="opencv")
    fps = stream.frame_rate or 10.0
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=max(2, int(min_scene_len_s * fps))))
    manager.detect_scenes(stream, show_progress=False)
    scenes = manager.get_scene_list()
    if not scenes:
        duration = ffmpeg_tools.probe(video).duration_s
        return [{"start": 0.0, "end": round(duration, 3)}]
    def secs(tc):
        return tc.seconds if hasattr(tc, "seconds") else tc.get_seconds()

    return [{"start": round(secs(s), 3), "end": round(secs(e), 3)} for s, e in scenes]


def visual_features(proxy: Path, *, fps: float = 2.0) -> list[dict]:
    """Per-sample sharpness, brightness, motion and face presence."""
    import cv2

    detector = None
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions

        detector = FaceDetector.create_from_options(
            FaceDetectorOptions(base_options=BaseOptions(model_asset_path=str(models.ensure("face_detector"))),
                                min_detection_confidence=0.35))
    except Exception as exc:  # noqa: BLE001 - face features are optional
        log.warning("Face detector unavailable: %s", exc)
    samples: list[dict] = []
    previous = None
    try:
        for t, frame in ffmpeg_tools.sample_frames(proxy, fps=fps, width=640):
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            bright = float(gray.mean())
            contrast = float(gray.std())
            motion = float(np.abs(gray.astype(np.int16) - previous).mean()) if previous is not None else 0.0
            previous = gray.astype(np.int16)
            faces: list[dict] = []
            if detector is not None:
                faces = _detect_faces_tiled(detector, mp, frame)
            samples.append({"t": round(t, 2), "sharpness": round(sharp, 1), "brightness": round(bright, 1),
                            "contrast": round(contrast, 1), "motion": round(motion, 2), "faces": faces})
    finally:
        if detector is not None:
            detector.close()
    return samples


def _detect_faces_tiled(detector, mp, frame: np.ndarray) -> list[dict]:
    """BlazeFace on the full frame plus square tiles (see vendored reframe/faces.py)."""
    from ..vendor.autoclip.reframe.faces import _tiles

    height, width = frame.shape[:2]
    found: list[dict] = []
    for key, x0, y0, tw, th in _tiles(width, height):
        crop = frame if key == "full" else frame[y0:y0 + th, x0:x0 + tw]
        result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)))
        for detection in result.detections or []:
            box = detection.bounding_box
            cx, cy = x0 + box.origin_x + box.width / 2, y0 + box.origin_y + box.height / 2
            if any(abs(cx - f["_x"]) < box.width / 2 and abs(cy - f["_y"]) < box.height / 2 for f in found):
                continue
            found.append({"_x": cx, "_y": cy, "cx": round(cx / width, 3), "cy": round(cy / height, 3),
                          "area": round(box.width * box.height / (width * height), 4)})
    return [{k: v for k, v in f.items() if not k.startswith("_")} for f in found]


def audio_events(wav: Path, *, block_s: float = 60.0) -> list[dict]:
    """YAMNet events per ~1 s window (only classes relevant to highlights)."""
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.audio import audio_classifier as ac
    from mediapipe.tasks.python.components.containers import audio_data as ad

    options = ac.AudioClassifierOptions(base_options=BaseOptions(model_asset_path=str(models.ensure("yamnet"))),
                                        max_results=5, score_threshold=0.15)
    duration = ffmpeg_tools.wav_duration(wav)
    events: list[dict] = []
    with ac.AudioClassifier.create_from_options(options) as classifier:
        start = 0.0
        while start < duration:
            end = min(duration, start + block_s)
            samples = ffmpeg_tools.read_wav(wav, start, end).astype(np.float32) / 32768.0
            if samples.size >= ffmpeg_tools.ASR_RATE:
                for result in classifier.classify(ad.AudioData.create_from_array(samples, ffmpeg_tools.ASR_RATE)):
                    labels = {}
                    for category in result.classifications[0].categories:
                        key = EVENT_CLASSES.get(category.category_name)
                        if key:
                            labels[key] = max(labels.get(key, 0.0), round(float(category.score), 3))
                    if labels:
                        events.append({"t": round(start + result.timestamp_ms / 1000.0, 2), **labels})
            start = end
    return events


def audio_profile(wav: Path) -> dict:
    levels = ffmpeg_tools.loudness_curve(wav, 0.5)
    silences = ffmpeg_tools.silences_from_curve(levels, 0.5, min_duration_s=0.5)
    arr = np.asarray(levels) if levels else np.zeros(1)
    voiced = arr[arr > (np.percentile(arr, 10) + 6)] if arr.size > 10 else arr
    return {
        "window_s": 0.5,
        "levels_db": levels,
        "silences": silences,
        "speech_level_db": round(float(np.median(voiced)), 2) if voiced.size else None,
        "clipping_ratio": round(float(np.mean(arr > -1.0)), 4) if arr.size else 0.0,
    }
