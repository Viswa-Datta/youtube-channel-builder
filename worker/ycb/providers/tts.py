"""Voice providers. Narration is synthesised sentence by sentence so captions get real sentence timings.

Free:  kokoro (Kokoro-82M ONNX, self-hosted, phonemes from espeak-ng)
Paid:  openai (tts-1 / tts-1-hd), google (Chirp 3 HD), elevenlabs
"""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

from ..config import require, settings

SR = 24000
_PUNCT = re.compile(r"([,.;:!?—])")


@dataclass
class Sentence:
    text: str
    start: float
    end: float


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    # keep abbreviations like "Dr." "U.S." together
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and (len(p) < 4 or re.search(r"\b(Dr|Mr|Mrs|Ms|St|No|U\.S)\.$", out[-1])):
            out[-1] += " " + p
        else:
            out.append(p)
    return out


# ---------------------------------------------------------------- Kokoro (free, local)
class Kokoro:
    _inst: "Kokoro | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        import onnxruntime as rt
        for p in (settings.kokoro_model, settings.kokoro_voices, settings.kokoro_vocab):
            if not Path(p).exists():
                raise FileNotFoundError(f"Kokoro model file missing: {p} (see docs/SETUP-KEYS.md, 'Free voice')")
        o = rt.SessionOptions()
        self.sess = rt.InferenceSession(str(settings.kokoro_model), o, providers=["CPUExecutionProvider"])
        self.voices = np.load(settings.kokoro_voices)
        self.vocab = json.load(open(settings.kokoro_vocab, encoding="utf-8"))["vocab"]
        self.tok_name = "input_ids" if any(i.name == "input_ids" for i in self.sess.get_inputs()) else "tokens"

    @classmethod
    def get(cls) -> "Kokoro":
        with cls._lock:
            if cls._inst is None:
                cls._inst = Kokoro()
            return cls._inst

    def phonemize(self, text: str, lang: str = "en-us") -> str:
        out = ""
        for part in _PUNCT.split(text):
            if not part.strip():
                continue
            if _PUNCT.fullmatch(part):
                out = out.rstrip() + ("," if part == "—" else part) + " "
                continue
            r = subprocess.run([settings.espeak_bin, "-q", "--ipa", "-v", lang, part],
                               capture_output=True, text=True, check=True)
            out += " ".join(r.stdout.split()) + " "
        return "".join(c for c in out.strip() if c in self.vocab)

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray:
        ph = self.phonemize(text)
        chunks = self._chunks(ph, 480)
        audio = []
        for c in chunks:
            tok = [self.vocab[ch] for ch in c]
            style = self.voices[voice][min(len(tok), 509)]
            a = self.sess.run(None, {self.tok_name: np.array([[0, *tok, 0]], dtype=np.int64), "style": style,
                                     "speed": np.array([speed], dtype=np.float32)})[0].ravel()
            audio.append(_trim(a))
        return np.concatenate(audio) if audio else np.zeros(1, dtype=np.float32)

    @staticmethod
    def _chunks(ph: str, limit: int) -> list[str]:
        if len(ph) <= limit:
            return [ph]
        out, cur = [], ""
        for piece in re.split(r"(?<=[,;:])\s", ph):
            if len(cur) + len(piece) + 1 > limit and cur:
                out.append(cur)
                cur = piece
            else:
                cur = (cur + " " + piece).strip()
        if cur:
            out.append(cur)
        final = []
        for c in out:  # hard split anything still too long at spaces
            while len(c) > limit:
                cut = c.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                final.append(c[:cut])
                c = c[cut:].strip()
            final.append(c)
        return final


def _trim(a: np.ndarray, thresh: float = 0.004) -> np.ndarray:
    idx = np.where(np.abs(a) > thresh)[0]
    if len(idx) == 0:
        return a
    s = max(0, idx[0] - int(0.02 * SR))
    e = min(len(a), idx[-1] + int(0.06 * SR))
    return a[s:e]


# ---------------------------------------------------------------- paid providers (return float32 @ 24 kHz)
def _pcm16_to_f32(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768.0


def _wav_to_f32(b: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(b)) as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        a = _pcm16_to_f32(w.readframes(n))
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        a = np.interp(np.linspace(0, len(a), int(len(a) * SR / sr), endpoint=False), np.arange(len(a)), a).astype(np.float32)
    return a


def _openai(text: str, voice: str, speed: float, model: str) -> np.ndarray:
    require("openai")
    r = requests.post("https://api.openai.com/v1/audio/speech", timeout=180,
                      headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                      json={"model": model, "voice": voice, "input": text, "speed": speed, "response_format": "pcm"})
    r.raise_for_status()
    return _trim(_pcm16_to_f32(r.content))  # raw 24 kHz 16-bit mono


def _elevenlabs(text: str, voice: str, speed: float, model: str) -> np.ndarray:
    require("elevenlabs")
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}", timeout=180,
                      params={"output_format": "pcm_24000"},
                      headers={"xi-api-key": settings.elevenlabs_api_key},
                      json={"text": text, "model_id": model, "voice_settings": {"stability": 0.6, "similarity_boost": 0.75, "speed": speed}})
    r.raise_for_status()
    return _trim(_pcm16_to_f32(r.content))


def _google(text: str, voice: str, speed: float, model: str) -> np.ndarray:
    require("google")
    lang = "-".join(voice.split("-")[:2])
    r = requests.post("https://texttospeech.googleapis.com/v1/text:synthesize", timeout=180,
                      params={"key": settings.google_tts_api_key},
                      json={"input": {"text": text}, "voice": {"languageCode": lang, "name": voice},
                            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": SR, "speakingRate": speed}})
    r.raise_for_status()
    return _trim(_wav_to_f32(base64.b64decode(r.json()["audioContent"])))


def synth_sentence(provider: str, voice: str, speed: float, text: str, model: str | None = None) -> np.ndarray:
    if provider == "kokoro":
        return Kokoro.get().synth(text, voice, speed)
    if provider == "openai":
        return _openai(text, voice, speed, model or "tts-1")
    if provider == "elevenlabs":
        return _elevenlabs(text, voice, speed, model or "eleven_multilingual_v2")
    if provider == "google":
        return _google(text, voice, speed, model or "chirp3-hd")
    raise ValueError(f"unknown TTS provider {provider}")


def synth_scene(provider: str, voice: str, speed: float, narration: str, out_wav: Path,
                model: str | None = None, gap: float = 0.38, para_gap: float = 0.75,
                on_progress=None) -> tuple[list[Sentence], int, float]:
    """Synthesise a scene. Returns sentence timings (relative to scene start), characters billed, duration."""
    paragraphs = [p for p in re.split(r"\n\s*\n", narration.strip()) if p.strip()]
    items: list[tuple[str, bool]] = []
    for pi, p in enumerate(paragraphs):
        ss = split_sentences(p)
        for si, s in enumerate(ss):
            items.append((s, si == len(ss) - 1 and pi < len(paragraphs) - 1))
    workers = 1 if provider == "kokoro" else 4
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(synth_sentence, provider, voice, speed, s, model) for s, _ in items]
        clips = []
        for i, f in enumerate(futs):
            clips.append(f.result())
            if on_progress:
                on_progress((i + 1) / len(futs))
    sentences: list[Sentence] = []
    t = 0.25
    buf = [np.zeros(int(0.25 * SR), dtype=np.float32)]
    for (s, para_end), a in zip(items, clips):
        sentences.append(Sentence(s, round(t, 3), round(t + len(a) / SR, 3)))
        buf.append(a.astype(np.float32))
        t += len(a) / SR
        pause = para_gap if para_end else gap
        buf.append(np.zeros(int(pause * SR), dtype=np.float32))
        t += pause
    audio = np.concatenate(buf)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio / peak * 0.89  # -1 dBFS peak; loudness is normalised at the final mix
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_wav.with_suffix(".part.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    tmp.replace(out_wav)
    chars = sum(len(s) for s, _ in items)
    return sentences, chars, len(audio) / SR
