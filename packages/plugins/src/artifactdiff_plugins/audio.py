"""Audio comparison: format, duration, sample rate, channels, level, silence, spectrum, waveform.

Files are decoded with libsndfile (via soundfile) block by block; nothing is held in memory
beyond small envelopes. Spectra are averaged Hann-windowed FFT power spectra of the mono mix
(windows are sub-sampled for very long files). Levels are RMS/peak in dBFS — *not* LUFS, which
requires K-weighting. ArtifactDiff makes no claim about what the audio *means*.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import Field

from artifactdiff.errors import PluginInputError, ResourceLimitError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff_plugins._common import png_bytes

if TYPE_CHECKING:
    import numpy as np

FFT = 2048
ENV_CHUNK = 256
SPEC_COLS = 400
SPEC_BINS = 96
BANDS = (20, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


class AudioOptions(PluginOptions):
    segment_seconds: float = Field(0.1, gt=0.005, le=10, description="Analysis segment length.")
    silence_threshold_db: float = Field(-60, le=0, description="Segments below this RMS (dBFS) are silent.")
    min_silence_seconds: float = Field(0.3, ge=0, description="Shortest silent stretch listed as an interval.")
    level_tolerance_db: float = Field(0.5, ge=0, description="RMS/peak changes below this are ignored.")
    spectral_tolerance_db: float = Field(1.5, ge=0, description="Per-band energy changes below this are ignored.")
    spectral_floor_db: float = Field(-80, le=0, description="Bands below this level in both files are not compared.")
    difference_threshold_db: float = Field(-60, le=0, description="Difference RMS (dBFS) marking a segment changed.")
    max_segments: int = Field(20, ge=0, description="Differing time ranges listed individually.")
    max_duration_seconds: float = Field(4 * 3600, gt=0, description="Longer recordings are rejected.")
    max_fft_windows: int = Field(4000, ge=10, description="FFT windows analysed per file (sub-sampled beyond).")


def db(x: float) -> float | None:
    return round(20 * math.log10(x), 2) if x > 0 else None


@dataclass
class Analysis:
    samplerate: int
    channels: int
    frames: int
    format: str
    subtype: str
    rms: float = 0.0
    peak: float = 0.0
    segment_rms: list[float] = field(default_factory=list)
    env_min: Any = None
    env_max: Any = None
    spectrum: Any = None  # mean power per FFT bin
    spectrogram: Any = None  # (SPEC_BINS, SPEC_COLS) dB

    @property
    def duration(self) -> float:
        return self.frames / self.samplerate if self.samplerate else 0.0


def _info(file: ArtifactFile) -> Any:
    import soundfile as sf

    try:
        return sf.info(str(file.path))
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise PluginInputError(f"{file.display_name} is not decodable audio: {exc}") from exc


def _log_bins(sr: int) -> np.ndarray:
    """Map each FFT bin to one of SPEC_BINS log-spaced frequency bands (30 Hz to Nyquist)."""
    import numpy as np

    freqs = np.fft.rfftfreq(FFT, 1 / sr)
    edges = np.geomspace(30, sr / 2, SPEC_BINS + 1)
    return np.clip(np.searchsorted(edges, freqs) - 1, 0, SPEC_BINS - 1)


def analyse(file: ArtifactFile, opts: AudioOptions, ctx: ComparisonContext, label: str) -> Analysis:
    import numpy as np
    import soundfile as sf

    info = _info(file)
    an = Analysis(info.samplerate, info.channels, info.frames, info.format, info.subtype)
    if an.duration > opts.max_duration_seconds:
        raise ResourceLimitError(
            f"{file.display_name} is {an.duration:.0f}s long (limit {opts.max_duration_seconds:.0f}s)"
        )
    seg = max(1, round(opts.segment_seconds * an.samplerate))
    total_windows = max(1, an.frames // FFT)
    stride = max(1, math.ceil(total_windows / opts.max_fft_windows))
    bin_idx = _log_bins(an.samplerate)
    window = np.hanning(FFT).astype(np.float32)
    power_sum = np.zeros(FFT // 2 + 1)
    windows_used = 0
    spec_acc = np.zeros((SPEC_BINS, SPEC_COLS))
    spec_cnt = np.zeros(SPEC_COLS)
    env_min, env_max, seg_rms = [], [], []
    sumsq = 0.0
    peak = 0.0
    pos = 0
    block = seg * max(1, (1 << 18) // seg)  # multiple of the segment size, ~256k frames
    try:
        for data in sf.blocks(str(file.path), blocksize=block, dtype="float32", always_2d=True):
            ctx.check()
            if data.size == 0:
                continue
            sumsq += float(np.square(data, dtype=np.float64).sum())
            peak = max(peak, float(np.abs(data).max()))
            mono = data.mean(axis=1)
            n = len(mono)
            for s in range(0, n, seg):
                part = mono[s : s + seg]
                seg_rms.append(float(np.sqrt(np.mean(np.square(part, dtype=np.float64)))))
            pad = (-n) % ENV_CHUNK
            m = np.pad(mono, (0, pad)).reshape(-1, ENV_CHUNK)
            env_min.append(m.min(axis=1))
            env_max.append(m.max(axis=1))
            for w0 in range((-pos) % FFT, n - FFT + 1, FFT):
                widx = (pos + w0) // FFT
                if widx % stride:
                    continue
                spec = np.abs(np.fft.rfft(mono[w0 : w0 + FFT] * window)) ** 2
                power_sum += spec
                windows_used += 1
                col = min(SPEC_COLS - 1, int(widx * SPEC_COLS / total_windows))
                spec_acc[:, col] += np.bincount(bin_idx, weights=spec, minlength=SPEC_BINS)
                spec_cnt[col] += 1
            pos += n
            if an.frames:
                ctx.progress(min(1.0, pos / an.frames), f"analysing {label} audio")
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise PluginInputError(f"{file.display_name} could not be decoded: {exc}") from exc
    an.frames = pos  # decoded frame count (MP3 headers can be approximate)
    total = pos * an.channels
    an.rms = math.sqrt(sumsq / total) if total else 0.0
    an.peak = peak
    an.segment_rms = seg_rms
    an.env_min = np.concatenate(env_min) if env_min else np.zeros(0)
    an.env_max = np.concatenate(env_max) if env_max else np.zeros(0)
    norm = (window.sum() ** 2) / 4  # a full-scale sine maps to ~0 dB
    an.spectrum = power_sum / max(1, windows_used) / norm
    with np.errstate(divide="ignore"):
        filled = spec_cnt > 0
        sg = np.full((SPEC_BINS, SPEC_COLS), -120.0)
        sg[:, filled] = 10 * np.log10(np.maximum(spec_acc[:, filled] / spec_cnt[filled] / norm, 1e-12))
    an.spectrogram = sg
    return an


def silence(an: Analysis, opts: AudioOptions) -> dict[str, Any]:
    thr = 10 ** (opts.silence_threshold_db / 20)
    seg = opts.segment_seconds
    silent = [r < thr for r in an.segment_rms]
    lead = next((i for i, s in enumerate(silent) if not s), len(silent))
    trail = next((i for i, s in enumerate(reversed(silent)) if not s), len(silent))
    intervals = []
    i = 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            if (j - i) * seg >= opts.min_silence_seconds:
                intervals.append([round(i * seg, 3), round(min(j * seg, an.duration), 3)])
            i = j
        else:
            i += 1
    return {
        "leading": round(min(lead * seg, an.duration), 3),
        "trailing": round(min(trail * seg, an.duration), 3),
        "total": round(min(sum(silent) * seg, an.duration), 3),
        "intervals": intervals,
    }


def band_energy(an: Analysis) -> dict[str, float | None]:
    import numpy as np

    freqs = np.fft.rfftfreq(FFT, 1 / an.samplerate)
    out: dict[str, float | None] = {}
    edges = [*BANDS, an.samplerate / 2]
    for lo, hi in itertools.pairwise(edges):
        if lo >= an.samplerate / 2:
            break
        mask = (freqs >= lo) & (freqs < hi)
        p = float(an.spectrum[mask].sum()) if mask.any() else 0.0
        out[f"{lo:g}-{hi:g}Hz"] = round(10 * math.log10(p), 2) if p > 1e-12 else None
    return out


class AudioPlugin(ArtifactPlugin):
    name = "audio"
    version = "1.0.0"
    display_name = "Audio"
    description = "Duration, format, level, silence, spectral and waveform comparison of audio files."
    format_id = "audio"
    format_name = "Audio"
    supported_extensions = (".wav", ".flac", ".ogg", ".oga", ".mp3", ".aiff", ".aif", ".opus")
    mime_types = ("audio/wav", "audio/x-wav", "audio/flac", "audio/ogg", "audio/mpeg", "audio/aiff")
    capabilities = ("duration", "loudness", "silence", "spectrum", "waveform-diff", "spectrogram", "streaming")
    limitations = (
        "Levels are RMS/peak dBFS, not perceptual loudness (LUFS).",
        "Sample-level waveform comparison needs equal sample rates and channel counts.",
        "No semantic understanding of audio content (speech, music, instruments) is attempted.",
        "MP3/Opus support depends on the libsndfile build (see `artifactdiff doctor`).",
    )
    requires = ("soundfile", "numpy", "PIL")
    install_hint = 'pip install "artifactdiff[audio]"'
    Options = AudioOptions

    def sniff(self, head: bytes) -> bool:
        return (head[:4] == b"RIFF" and head[8:12] == b"WAVE") or head.startswith((b"fLaC", b"OggS", b"FORM"))

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        info = _info(file)
        return {
            "samplerate": info.samplerate,
            "channels": info.channels,
            "frames": info.frames,
            "duration_seconds": round(info.duration, 4),
            "format": info.format,
            "subtype": info.subtype,
        }

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, AudioOptions)
        a = analyse(old, options, ctx, "old")
        b = analyse(new, options, ctx, "new")
        ev = "libsndfile stream info"
        for attr, sev in (
            ("samplerate", ChangeSeverity.HIGH),
            ("channels", ChangeSeverity.HIGH),
            ("format", ChangeSeverity.LOW),
            ("subtype", ChangeSeverity.LOW),
        ):
            va, vb = getattr(a, attr), getattr(b, attr)
            if va != vb:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.METADATA if attr in ("format", "subtype") else ChangeCategory.AUDIO,
                    attr,
                    f"{attr.capitalize()} changed",
                    old=va,
                    new=vb,
                    severity=sev,
                    evidence=ev,
                )
        if abs(a.duration - b.duration) > 1 / max(a.samplerate, b.samplerate, 1):
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.AUDIO,
                "duration",
                f"Duration changed by {b.duration - a.duration:+.3f}s",
                old=round(a.duration, 4),
                new=round(b.duration, 4),
                severity=ChangeSeverity.MEDIUM,
                evidence="Decoded frame count / sample rate",
            )
        for label, va, vb in (("rms_dbfs", db(a.rms), db(b.rms)), ("peak_dbfs", db(a.peak), db(b.peak))):
            ctx.metric(f"old_{label}", va)
            ctx.metric(f"new_{label}", vb)
            if va != vb and (va is None or vb is None or abs(va - vb) > options.level_tolerance_db):
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.AUDIO,
                    f"level:{label}",
                    f"{'RMS level' if label.startswith('rms') else 'Peak level'} changed"
                    + (f" by {vb - va:+.2f} dB" if va is not None and vb is not None else ""),
                    old=va,
                    new=vb,
                    severity=ChangeSeverity.LOW,
                    evidence=f"{'Root-mean-square' if label.startswith('rms') else 'Maximum absolute'} "
                    f"sample value over all channels, in dBFS",
                )
        sa, sb = silence(a, options), silence(b, options)
        for key in ("leading", "trailing", "total"):
            if abs(sa[key] - sb[key]) >= options.segment_seconds:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.AUDIO,
                    f"silence:{key}",
                    f"{key.capitalize()} silence changed from {sa[key]:.2f}s to {sb[key]:.2f}s",
                    old=sa[key],
                    new=sb[key],
                    severity=ChangeSeverity.LOW,
                    evidence=f"{options.segment_seconds:g}s segments with RMS below "
                    f"{options.silence_threshold_db:g} dBFS",
                )
        ctx.metric("old_silence", sa)
        ctx.metric("new_silence", sb)
        if a.samplerate == b.samplerate:
            ea, eb = band_energy(a), band_energy(b)
            for band in ea:
                va, vb = ea[band], eb.get(band)
                floor = options.spectral_floor_db
                if (va is None or va < floor) and (vb is None or vb < floor):
                    continue  # noise floor in both files: differences there are not meaningful
                if va is None or vb is None or abs(va - vb) > options.spectral_tolerance_db:
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.AUDIO,
                        f"spectrum:{band}",
                        f"Energy in {band} changed"
                        + (f" by {vb - va:+.1f} dB" if va is not None and vb is not None else ""),
                        old=va,
                        new=vb,
                        severity=ChangeSeverity.INFO,
                        evidence=f"Mean Hann-windowed {FFT}-point FFT power of the mono mix",
                    )
            ctx.metric("old_band_energy_db", ea)
            ctx.metric("new_band_energy_db", eb)
        else:
            ctx.warn("spectral-skipped", "Sample rates differ; spectral bands were not compared")
        if a.samplerate == b.samplerate and a.channels == b.channels:
            self._waveform(ctx, old, new, a, b, options)
        else:
            ctx.warn("waveform-skipped", "Sample rate or channel count differs; samples were not compared directly")
        if ctx.generate_assets:
            self._assets(ctx, a, b)

    def _waveform(
        self, ctx: ComparisonContext, old: ArtifactFile, new: ArtifactFile, a: Analysis, b: Analysis, opts: AudioOptions
    ) -> None:
        import numpy as np
        import soundfile as sf

        sr = a.samplerate
        seg = max(1, round(opts.segment_seconds * sr))
        block = seg * max(1, (1 << 18) // seg)
        thr = 10 ** (opts.difference_threshold_db / 20)
        diff_sumsq = sig_sumsq = 0.0
        max_abs = 0.0
        first: int | None = None
        changed_segments: list[int] = []
        seg_index = 0
        ga = sf.blocks(str(old.path), blocksize=block, dtype="float32", always_2d=True)
        gb = sf.blocks(str(new.path), blocksize=block, dtype="float32", always_2d=True)
        pos = 0
        for xa, xb in zip(ga, gb, strict=False):
            ctx.check()
            n = min(len(xa), len(xb))
            d = xa[:n] - xb[:n]
            diff_sumsq += float(np.square(d, dtype=np.float64).sum())
            sig_sumsq += float(np.square(xa[:n], dtype=np.float64).sum())
            ad = np.abs(d)
            if ad.size:
                max_abs = max(max_abs, float(ad.max()))
                if first is None and ad.max() > 0:
                    first = pos + int(np.argmax(ad.max(axis=1) > 0))
            for s in range(0, n, seg):
                part = d[s : s + seg]
                if float(np.sqrt(np.mean(np.square(part, dtype=np.float64)))) > thr:
                    changed_segments.append(seg_index)
                seg_index += 1
            pos += n
            if n < len(xa) or n < len(xb):
                break
        ctx.metric("max_abs_sample_difference", round(max_abs, 6))
        ctx.metric("difference_rms_dbfs", db(math.sqrt(diff_sumsq / max(1, pos * a.channels))))
        snr = 10 * math.log10(sig_sumsq / diff_sumsq) if diff_sumsq > 0 and sig_sumsq > 0 else None
        ctx.metric("signal_to_difference_db", round(snr, 2) if snr is not None else None)
        if first is not None:
            ctx.metric("first_difference_seconds", round(first / sr, 6))
        # merge consecutive segments into ranges
        ranges: list[list[int]] = []
        for s in changed_segments:
            if ranges and s == ranges[-1][1] + 1:
                ranges[-1][1] = s
            else:
                ranges.append([s, s])
        segsec = seg / sr
        for s0, s1 in ranges[: opts.max_segments]:
            t0, t1 = round(s0 * segsec, 3), round(min((s1 + 1) * segsec, pos / sr), 3)
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.AUDIO,
                f"time[{t0:.3f}s-{t1:.3f}s]",
                f"Waveform differs between {t0:.2f}s and {t1:.2f}s",
                old=None,
                new={"start": t0, "end": t1},
                severity=ChangeSeverity.LOW,
                evidence=f"Sample-wise difference RMS above {opts.difference_threshold_db:g} dBFS "
                f"in {opts.segment_seconds:g}s segments",
            )
        if len(ranges) > opts.max_segments:
            ctx.warn("segments-truncated", f"{len(ranges)} differing ranges; first {opts.max_segments} listed")
        ctx.metric("differing_ranges", len(ranges))

    def _assets(self, ctx: ComparisonContext, a: Analysis, b: Analysis) -> None:
        import numpy as np
        from PIL import Image, ImageDraw

        width, lane = 1000, 90
        img = Image.new("RGB", (width, lane * 2 + 10), (13, 17, 23))
        draw = ImageDraw.Draw(img)
        longest = max(a.duration, b.duration) or 1.0
        for i, (an, color) in enumerate(((a, (122, 162, 247)), (b, (63, 185, 80)))):
            top = i * (lane + 10)
            mid = top + lane // 2
            n = len(an.env_min)
            if not n:
                continue
            px = max(1, int(width * an.duration / longest))
            edges = np.linspace(0, n, px + 1).astype(int)
            for x in range(px):
                lo, hi = edges[x], max(edges[x] + 1, edges[x + 1])
                mn, mx = float(an.env_min[lo:hi].min()), float(an.env_max[lo:hi].max())
                draw.line((x, mid - mx * lane / 2, x, mid - mn * lane / 2), fill=color)
            draw.text((4, top + 2), "old" if i == 0 else "new", fill=(200, 200, 200))
        data, w, h = png_bytes(img)
        ctx.add_asset(
            "waveform", "Waveforms (old above, new below, shared time axis)", "image/png", data, width=w, height=h
        )
        from artifactdiff_plugins.image import _heat_colormap

        rows = []
        for an in (a, b):
            sg = np.clip((an.spectrogram + 100) / 100 * 255, 0, 255).astype(np.uint8)[::-1]
            rows.append(_heat_colormap(sg))
            rows.append(np.full((4, SPEC_COLS, 3), 40, np.uint8))
        spec = Image.fromarray(np.concatenate(rows[:-1], axis=0), "RGB").resize(
            (SPEC_COLS * 2, (SPEC_BINS * 2 + 4) * 2), Image.Resampling.NEAREST
        )
        data, w, h = png_bytes(spec)
        ctx.add_asset(
            "spectrogram",
            "Spectrograms, log-frequency 30 Hz-Nyquist (old above, new below)",
            "image/png",
            data,
            width=w,
            height=h,
        )
