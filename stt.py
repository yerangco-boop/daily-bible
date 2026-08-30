"""
whisper.cpp(pywhispercpp)로 오디오를 텍스트로 변환한다.

Render 무료 티어(512MB RAM) 안에서 안정적으로 돌아가도록 base(양자화) 모델을
쓴다. 모델은 처음 호출될 때 자동으로 다운로드되어 로컬(플랫폼 캐시 폴더)에
저장되고, 이후 호출부터는 캐시를 재사용한다 — 단, Render 무료 티어는
persistent disk가 없으므로 서비스가 재시작될 때마다 다시 받게 된다
(모델 파일은 수십 MB 수준이라 재다운로드 자체는 몇 초~수십 초 내로 끝난다).

주의: 이 모듈은 이 개발 환경(사내 네트워크 정책)에서는 huggingface.co
접속이 막혀 있어 실제 다운로드·추론까지 끝까지 검증하지 못했다. pywhispercpp
설치 자체(사전 컴파일된 wheel)는 이 환경에서 성공적으로 확인했다.
Render는 일반 인터넷 접속이 열려 있어야 하므로 배포 후 첫 실행에서
정상 동작하는지 반드시 확인이 필요하다(09절 "확인 필요" 항목).
"""
from __future__ import annotations

import subprocess
import tempfile
import os
import logging

logger = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("WHISPER_MODEL", "base-q8_0")

_model = None


def _get_model():
    global _model
    if _model is None:
        from pywhispercpp.model import Model

        logger.info("whisper 모델 로딩 중: %s", MODEL_NAME)
        _model = Model(MODEL_NAME, print_realtime=False, print_progress=False)
    return _model


def _to_wav16k(src_path: str) -> str:
    """whisper.cpp는 16kHz mono wav를 기대한다. ffmpeg로 변환한다."""
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ar", "16000", "-ac", "1", "-f", "wav", wav_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return wav_path


def transcribe_audio(mp3_path: str, language: str = "ko") -> str:
    """mp3 파일 경로를 받아 전체 낭독 스크립트(텍스트)를 반환한다."""
    wav_path = _to_wav16k(mp3_path)
    try:
        model = _get_model()
        segments = model.transcribe(wav_path, language=language)
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        return text.strip()
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python stt.py <mp3-or-wav-path>")
        raise SystemExit(1)
    print(transcribe_audio(sys.argv[1]))
