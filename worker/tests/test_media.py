"""Media acceptance: blur in every frame (moving/turning face + moving plate), Kokoro voice is real audio,
and a short end-to-end mux has an audio stream at about -14 LUFS."""
from pathlib import Path

import blur_fixture as bf


def test_blur_every_frame(tmp_path):
    from ycb.media import blur
    src = tmp_path / "clip.mp4"
    fx = bf.make(src)
    an = blur.analyse(src)
    out = blur.render(src, tmp_path / "blurred.mp4", an)
    r = bf.verify(src, out, fx["gt"])
    assert r["face"]["checked"] >= 200 and r["plate"]["checked"] >= 150
    assert r["pass"], r


def test_kokoro_produces_speech(tmp_path):
    import numpy as np
    import wave
    from ycb.providers import tts
    sents, chars, dur = tts.synth_scene("kokoro", "am_michael", 1.0, "The file was reopened. Every page was read again.", tmp_path / "s.wav")
    assert len(sents) == 2 and 2.0 < dur < 8.0 and chars > 40
    with wave.open(str(tmp_path / "s.wav")) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert np.sqrt(np.mean((a / 32768.0) ** 2)) > 0.02  # not silence


def test_short_mux_has_audio_at_minus_14(tmp_path):
    from ycb.media import cards, compose, ff
    from ycb.providers import tts
    sents, _, dur = tts.synth_scene("kokoro", "am_michael", 1.0, "This is a short test of the final mix. It should be clearly audible. " * 3, tmp_path / "n.wav")
    img = cards.chapter_card(tmp_path / "c.png", 1, "Test chapter", "Kicker")
    vid = compose.scene_video([compose.Segment("image", img, dur)], tmp_path, tmp_path / "v.mp4")
    bed = compose.ambient_bed(dur, tmp_path / "bed.wav")
    audio, _ = compose.mix(tmp_path / "n.wav", bed, tmp_path / "mix.m4a")
    meta = tmp_path / "m.ff"
    meta.write_text(compose.ffmetadata([(0, dur, "Test chapter")], "t"))
    srt = tmp_path / "c.srt"
    from ycb.media.captions import build_srt
    srt.write_text(build_srt([s.__dict__ for s in sents]))
    out = compose.final_mux(vid, audio, srt, meta, tmp_path / "o.mp4", burn=True, sidecar=True)
    acc = ff.acceptance(out)
    assert acc["has_audio"] and acc["chapters"] == 1 and acc["subtitle_streams"] == 1
    assert abs(acc["integrated_lufs"] + 14) < 2.0, acc
