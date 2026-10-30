"""Transcription worker: handles ads where you pressed "Transcribe" in the web app.

Uses faster-whisper (free, open source) on CPU. Urdu/Hindi/any language is detected
automatically; we store the original-language text AND an English version.

  python transcribe.py --count     -> prints how many requests are waiting (no heavy imports)
  python transcribe.py             -> process up to 10 waiting requests
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import requests

import db

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")   # tiny / base / small / medium
BATCH = int(os.environ.get("TRANSCRIBE_BATCH", "10"))
MAX_BYTES = 120 * 1024 * 1024


def waiting() -> list[dict]:
    return db.select("ads", {"transcript_status": "eq.requested",
                             "select": "ad_archive_id,video_url,library_url", "limit": str(BATCH)})


def download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise RuntimeError("video too large")
                f.write(chunk)


def to_wav(src: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_wav(path: Path):
    """Read the 16 kHz mono wav that ffmpeg made into a float array, so faster-whisper does not
    need to decode audio itself (its decoder breaks on some PyAV versions)."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def main() -> int:
    if "--count" in sys.argv:
        n = len(waiting())
        print(n)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"pending={n}\n")
        return 0

    todo = waiting()
    if not todo:
        print("Nothing to transcribe.")
        return 0

    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

    for ad in todo:
        aid = ad["ad_archive_id"]
        print("transcribing", aid, flush=True)
        db.patch("ads", {"ad_archive_id": aid}, {"transcript_status": "processing", "transcript_error": None})
        try:
            if not ad.get("video_url"):
                raise RuntimeError("This ad has no video (image/text ad), so there is no spoken script to transcribe.")
            with tempfile.TemporaryDirectory() as tmp:
                vid, wav = Path(tmp, "v.mp4"), Path(tmp, "a.wav")
                try:
                    download(ad["video_url"], vid)
                except Exception as e:
                    raise RuntimeError(f"Video link expired or blocked ({e}). It refreshes on the next scrape - "
                                       "press Transcribe again after that.")
                to_wav(vid, wav)
                audio = load_wav(wav)
                segs, info = model.transcribe(audio, task="transcribe", vad_filter=True)
                original = " ".join(s.text.strip() for s in segs).strip()
                lang = info.language
                if lang == "en":
                    english = original
                else:
                    segs2, _ = model.transcribe(audio, task="translate", vad_filter=True)
                    english = " ".join(s.text.strip() for s in segs2).strip()
            if not original:
                raise RuntimeError("No speech detected in this video (music only?).")
            db.patch("ads", {"ad_archive_id": aid}, {
                "transcript_status": "done", "transcript_lang": lang,
                "transcript_original": original, "transcript_en": english, "transcript_error": None})
        except Exception as e:
            db.patch("ads", {"ad_archive_id": aid}, {"transcript_status": "failed",
                                                     "transcript_error": str(e)[:400]})
            print("  failed:", e, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
