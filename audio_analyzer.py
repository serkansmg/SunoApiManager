"""
Audio silence detection using pydub.
"""

import json
from pydub import AudioSegment
from pydub.silence import detect_silence


def analyze_silence(file_path: str, silence_thresh: int = -40,
                    min_silence_len: int = 1000) -> dict:
    """
    Analyze an audio file for silent gaps.

    Args:
        file_path: Path to the mp3 file
        silence_thresh: Silence threshold in dBFS (below this = silence)
        min_silence_len: Minimum silence length in ms

    Returns:
        dict with has_silence, silence_count, total_silence_sec, details
    """
    try:
        if file_path.endswith('.wav'):
            audio = AudioSegment.from_wav(file_path)
        else:
            audio = AudioSegment.from_mp3(file_path)
        duration_sec = len(audio) / 1000

        silences = detect_silence(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh
        )

        if not silences:
            return {
                "has_silence": False,
                "silence_count": 0,
                "total_silence_sec": 0,
                "duration_sec": duration_sec,
                "avg_dbfs": round(audio.dBFS, 1),
                "details": []
            }

        details = []
        total_silence = 0
        for start, end in silences:
            dur = (end - start) / 1000
            total_silence += dur
            details.append({
                "start": round(start / 1000, 2),
                "end": round(end / 1000, 2),
                "duration": round(dur, 2),
            })

        return {
            "has_silence": True,
            "silence_count": len(silences),
            "total_silence_sec": round(total_silence, 2),
            "duration_sec": round(duration_sec, 2),
            "avg_dbfs": round(audio.dBFS, 1),
            "details": details,
        }
    except Exception as e:
        return {
            "has_silence": None,
            "error": str(e),
            "details": [],
        }
