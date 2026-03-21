#!/usr/bin/env python3
"""Benchmark helper for the existing QwenTTS FastAPI server."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import mimetypes
import secrets
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_TEXT_SEED = (
    "This benchmark sentence is repeated to create stable synthesis input for latency and memory tests. "
)
DEFAULT_HTTP_TIMEOUT_SECONDS = 4000


@dataclass
class AudioMetadata:
    path: str
    size_bytes: int
    duration_seconds: float
    samplerate: int
    frames: int


@dataclass
class RequestMetrics:
    latency_seconds: float
    response_bytes: int
    vram_baseline_mb: dict[int, int]
    vram_peak_mb: dict[int, int]
    vram_delta_mb: dict[int, int]


@dataclass
class TextInputSpec:
    label: str
    text: str
    text_length: int
    source_path: str | None
    source_type: str


class HttpError(RuntimeError):
    """Raised when the benchmark request receives a non-2xx response."""


class VramSampler:
    """Poll `nvidia-smi` while a benchmark section is running."""

    def __init__(self, gpu_indexes: list[int] | None, interval_seconds: float) -> None:
        self.gpu_indexes = gpu_indexes
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._baseline: dict[int, int] = {}
        self._peak: dict[int, int] = {}
        self._thread: threading.Thread | None = None
        self.available = self._command_available()
        self.error: str | None = None

    @staticmethod
    def _command_available() -> bool:
        try:
            subprocess.run(
                ["nvidia-smi", "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return False
        return True

    def _read_once(self) -> dict[int, int]:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        )
        usage: dict[int, int] = {}
        for raw_line in completed.stdout.splitlines():
            if not raw_line.strip():
                continue
            index_text, used_text = [part.strip() for part in raw_line.split(",", maxsplit=1)]
            index = int(index_text)
            if self.gpu_indexes is not None and index not in self.gpu_indexes:
                continue
            usage[index] = int(float(used_text))
        return usage

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                sample = self._read_once()
                with self._lock:
                    if not self._baseline:
                        self._baseline = sample.copy()
                        self._peak = sample.copy()
                    else:
                        for gpu_index, used_mb in sample.items():
                            self._peak[gpu_index] = max(self._peak.get(gpu_index, used_mb), used_mb)
                self._stop_event.wait(self.interval_seconds)
        except Exception as exc:  # pragma: no cover - best effort monitor
            self.error = str(exc)

    def start(self) -> None:
        if not self.available:
            self.error = "nvidia-smi not available"
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
        if not self.available:
            return {}, {}, {}
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            baseline = self._baseline.copy()
            peak = self._peak.copy()
        delta = {
            gpu_index: peak_value - baseline.get(gpu_index, peak_value)
            for gpu_index, peak_value in peak.items()
        }
        return baseline, peak, delta


def build_text(target_length: int, seed: str) -> str:
    """Repeat a seed string until the text reaches the requested character count."""
    if target_length <= 0:
        raise ValueError("target_length must be > 0")
    if not seed:
        raise ValueError("text seed must not be empty")
    repeats = math.ceil(target_length / len(seed))
    return (seed * repeats)[:target_length]


def load_audio_metadata(audio_path: Path) -> AudioMetadata:
    try:
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "soundfile is required to inspect audio metadata. Run this script with the project environment, "
            "for example: uv run python benchmark_qwentts.py ..."
        ) from exc
    info = sf.info(str(audio_path))
    return AudioMetadata(
        path=str(audio_path),
        size_bytes=audio_path.stat().st_size,
        duration_seconds=float(info.duration),
        samplerate=int(info.samplerate),
        frames=int(info.frames),
    )


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    weight = position - lower
    return lower_value + (upper_value - lower_value) * weight


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def read_text_seed(args: argparse.Namespace) -> str:
    if args.text_seed_file:
        return Path(args.text_seed_file).read_text(encoding="utf-8").strip()
    if args.text_seed:
        return args.text_seed
    return DEFAULT_TEXT_SEED


def load_text_file_spec(path_like: str, label: str | None = None) -> TextInputSpec:
    text_path = Path(path_like).expanduser().resolve()
    if not text_path.exists():
        raise SystemExit(f"text file not found: {text_path}")
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"text file is empty: {text_path}")
    return TextInputSpec(
        label=label or text_path.name,
        text=text,
        text_length=len(text),
        source_path=str(text_path),
        source_type="file",
    )


def build_generated_text_spec(target_length: int, seed: str, label: str | None = None) -> TextInputSpec:
    text = build_text(target_length, seed)
    return TextInputSpec(
        label=label or f"generated-{target_length}",
        text=text,
        text_length=len(text),
        source_path=None,
        source_type="generated",
    )


def resolve_single_text_specs(args: argparse.Namespace) -> list[TextInputSpec]:
    if args.text_files:
        return [load_text_file_spec(path_like) for path_like in args.text_files]
    text_lengths = args.text_lengths or [1000, 5000, 10000]
    text_seed = read_text_seed(args)
    return [build_generated_text_spec(text_length, text_seed) for text_length in text_lengths]


def resolve_multi_text_spec(args: argparse.Namespace, single_text_specs: list[TextInputSpec]) -> TextInputSpec:
    if args.multi_text_file:
        return load_text_file_spec(args.multi_text_file, label="multi-text")
    if args.text_files:
        return single_text_specs[0]
    text_seed = read_text_seed(args)
    return build_generated_text_spec(args.multi_text_length, text_seed, label=f"generated-multi-{args.multi_text_length}")


def encode_multipart(fields: dict[str, str], files: dict[str, tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = f"----CodexBoundary{secrets.token_hex(16)}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    for field_name, (filename, content_type, payload) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                payload,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request_json(method: str, url: str, payload: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, method=method, data=payload, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise HttpError(f"{method} {url} failed with {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"{method} {url} failed: {exc.reason}") from exc
    if not body:
        return None
    return json.loads(body)


def request_binary(method: str, url: str, payload: bytes, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, method=method, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise HttpError(f"{method} {url} failed with {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"{method} {url} failed: {exc.reason}") from exc


def get_health(base_url: str) -> Any:
    return request_json("GET", f"{normalize_base_url(base_url)}/health")


def register_voice(base_url: str, audio_path: Path, name: str, ref_text: str | None) -> dict[str, Any]:
    audio_bytes = audio_path.read_bytes()
    content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    fields = {"name": name}
    if ref_text:
        fields["ref_text"] = ref_text
    body, header = encode_multipart(
        fields=fields,
        files={"ref_audio": (audio_path.name, content_type, audio_bytes)},
    )
    return request_json("POST", f"{normalize_base_url(base_url)}/tts/voices", body, {"Content-Type": header})


def delete_voice(base_url: str, voice_id: str) -> None:
    request_json("DELETE", f"{normalize_base_url(base_url)}/tts/voices/{voice_id}")


def synthesize_voice(base_url: str, voice_id: str, text: str, language: str) -> bytes:
    body, header = encode_multipart(fields={"text": text, "language": language}, files={})
    return request_binary(
        "POST",
        f"{normalize_base_url(base_url)}/tts/voices/{voice_id}/synthesize",
        body,
        {"Content-Type": header},
    )


def measure_request(func: Any, *args: Any, gpu_indexes: list[int] | None, interval_seconds: float, **kwargs: Any) -> tuple[Any, RequestMetrics, str | None]:
    sampler = VramSampler(gpu_indexes=gpu_indexes, interval_seconds=interval_seconds)
    sampler.start()
    started_at = time.perf_counter()
    result: Any = None
    latency_seconds = 0.0
    try:
        result = func(*args, **kwargs)
        latency_seconds = time.perf_counter() - started_at
    finally:
        baseline, peak, delta = sampler.stop()
    response_bytes = len(result) if isinstance(result, bytes) else len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    metrics = RequestMetrics(
        latency_seconds=latency_seconds,
        response_bytes=response_bytes,
        vram_baseline_mb=baseline,
        vram_peak_mb=peak,
        vram_delta_mb=delta,
    )
    return result, metrics, sampler.error


def summarize_runs(latencies: list[float]) -> dict[str, float]:
    return {
        "count": len(latencies),
        "avg_seconds": statistics.fmean(latencies) if latencies else 0.0,
        "min_seconds": min(latencies) if latencies else 0.0,
        "max_seconds": max(latencies) if latencies else 0.0,
        "p50_seconds": percentile(latencies, 0.50),
        "p95_seconds": percentile(latencies, 0.95),
    }


def benchmark_clone(
    base_url: str,
    audio_paths: list[Path],
    ref_text: str | None,
    runs: int,
    gpu_indexes: list[int] | None,
    interval_seconds: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    cleanup_voice_ids: list[str] = []

    for audio_path in audio_paths:
        metadata = load_audio_metadata(audio_path)
        run_items: list[dict[str, Any]] = []
        for run_index in range(1, runs + 1):
            voice_name = f"bench-clone-{audio_path.stem}-{uuid.uuid4().hex[:8]}"
            response, metrics, monitor_error = measure_request(
                register_voice,
                base_url,
                audio_path,
                voice_name,
                ref_text,
                gpu_indexes=gpu_indexes,
                interval_seconds=interval_seconds,
            )
            voice_id = response["voice_id"]
            cleanup_voice_ids.append(voice_id)
            run_items.append(
                {
                    "run_index": run_index,
                    "voice_id": voice_id,
                    "latency_seconds": metrics.latency_seconds,
                    "response_bytes": metrics.response_bytes,
                    "vram_baseline_mb": metrics.vram_baseline_mb,
                    "vram_peak_mb": metrics.vram_peak_mb,
                    "vram_delta_mb": metrics.vram_delta_mb,
                    "vram_monitor_error": monitor_error,
                }
            )

        latencies = [item["latency_seconds"] for item in run_items]
        max_delta_by_gpu: dict[int, int] = {}
        for item in run_items:
            for gpu_index, used_mb in item["vram_delta_mb"].items():
                max_delta_by_gpu[gpu_index] = max(max_delta_by_gpu.get(gpu_index, 0), used_mb)

        results.append(
            {
                "audio": asdict(metadata),
                "summary": summarize_runs(latencies),
                "max_vram_delta_mb": max_delta_by_gpu,
                "runs": run_items,
            }
        )

    return results, cleanup_voice_ids


def _register_task(base_url: str, audio_path: Path, ref_text: str | None, round_index: int, request_index: int) -> dict[str, Any]:
    started_at = time.perf_counter()
    response = register_voice(
        base_url=base_url,
        audio_path=audio_path,
        name=f"bench-clone-parallel-{audio_path.stem}-{uuid.uuid4().hex[:8]}",
        ref_text=ref_text,
    )
    latency_seconds = time.perf_counter() - started_at
    return {
        "round_index": round_index,
        "request_index": request_index,
        "base_url": base_url,
        "voice_id": response["voice_id"],
        "latency_seconds": latency_seconds,
        "response_bytes": len(json.dumps(response, ensure_ascii=False).encode("utf-8")),
    }


def benchmark_parallel_clone(
    base_urls: list[str],
    audio_paths: list[Path],
    ref_text: str | None,
    runs: int,
    gpu_indexes: list[int] | None,
    interval_seconds: float,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    results: list[dict[str, Any]] = []
    cleanup_pairs: list[tuple[str, str]] = []

    for audio_path in audio_paths:
        metadata = load_audio_metadata(audio_path)
        round_results: list[dict[str, Any]] = []
        all_latencies: list[float] = []

        for round_index in range(1, runs + 1):
            sampler = VramSampler(gpu_indexes=gpu_indexes, interval_seconds=interval_seconds)
            sampler.start()
            wall_started_at = time.perf_counter()
            request_results: list[dict[str, Any]] = []
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(base_urls)) as executor:
                    futures = []
                    request_index = 0
                    for base_url in base_urls:
                        request_index += 1
                        futures.append(
                            executor.submit(
                                _register_task,
                                base_url,
                                audio_path,
                                ref_text,
                                round_index,
                                request_index,
                            )
                        )
                    for future in concurrent.futures.as_completed(futures):
                        request_results.append(future.result())
                wall_seconds = time.perf_counter() - wall_started_at
            finally:
                baseline, peak, delta = sampler.stop()

            request_results.sort(key=lambda item: item["request_index"])
            for item in request_results:
                all_latencies.append(item["latency_seconds"])
                cleanup_pairs.append((item["base_url"], item["voice_id"]))
            round_results.append(
                {
                    "round_index": round_index,
                    "wall_seconds": wall_seconds,
                    "throughput_rps": len(request_results) / wall_seconds if wall_seconds else 0.0,
                    "requests": request_results,
                    "vram_baseline_mb": baseline,
                    "vram_peak_mb": peak,
                    "vram_delta_mb": delta,
                    "vram_monitor_error": sampler.error,
                }
            )

        max_delta_by_gpu: dict[int, int] = {}
        for round_item in round_results:
            for gpu_index, used_mb in round_item["vram_delta_mb"].items():
                max_delta_by_gpu[gpu_index] = max(max_delta_by_gpu.get(gpu_index, 0), used_mb)

        results.append(
            {
                "audio": asdict(metadata),
                "servers": base_urls,
                "requests_per_round": len(base_urls),
                "rounds": runs,
                "summary": summarize_runs(all_latencies),
                "max_vram_delta_mb": max_delta_by_gpu,
                "round_results": round_results,
            }
        )

    return results, cleanup_pairs


def create_temporary_voice(
    base_url: str,
    audio_path: Path,
    ref_text: str | None,
    name_prefix: str,
) -> str:
    response = register_voice(
        base_url=base_url,
        audio_path=audio_path,
        name=f"{name_prefix}-{uuid.uuid4().hex[:8]}",
        ref_text=ref_text,
    )
    return response["voice_id"]


def create_temporary_multi_voices(
    base_urls: list[str],
    audio_path: Path,
    ref_text: str | None,
    name_prefix: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    registered: list[tuple[str, str]] = []
    cleanup_voice_ids: list[str] = []
    for base_url in base_urls:
        voice_id = create_temporary_voice(
            base_url=base_url,
            audio_path=audio_path,
            ref_text=ref_text,
            name_prefix=name_prefix,
        )
        registered.append((base_url, voice_id))
        cleanup_voice_ids.append(voice_id)
    return registered, cleanup_voice_ids


def resolve_multi_voice_bindings(base_urls: list[str], voice_ids: list[str]) -> list[tuple[str, str]]:
    if len(voice_ids) != len(base_urls):
        raise SystemExit("--multi-voice-id must be provided once per --server")
    return list(zip(base_urls, voice_ids, strict=True))


def benchmark_single_synthesis(
    base_url: str,
    text_specs: list[TextInputSpec],
    language: str,
    runs: int,
    gpu_indexes: list[int] | None,
    interval_seconds: float,
    voice_id: str,
    voice_source_type: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    cleanup_voice_ids: list[str] = []
    results: list[dict[str, Any]] = []

    for text_spec in text_specs:
        run_items: list[dict[str, Any]] = []
        for run_index in range(1, runs + 1):
            response_bytes, metrics, monitor_error = measure_request(
                synthesize_voice,
                base_url,
                voice_id,
                text_spec.text,
                language,
                gpu_indexes=gpu_indexes,
                interval_seconds=interval_seconds,
            )
            run_items.append(
                {
                    "run_index": run_index,
                    "latency_seconds": metrics.latency_seconds,
                    "response_bytes": len(response_bytes),
                    "voice_id": voice_id,
                    "voice_source_type": voice_source_type,
                    "text_label": text_spec.label,
                    "text_length": text_spec.text_length,
                    "text_source_path": text_spec.source_path,
                    "text_source_type": text_spec.source_type,
                    "vram_baseline_mb": metrics.vram_baseline_mb,
                    "vram_peak_mb": metrics.vram_peak_mb,
                    "vram_delta_mb": metrics.vram_delta_mb,
                    "vram_monitor_error": monitor_error,
                }
            )

        latencies = [item["latency_seconds"] for item in run_items]
        max_delta_by_gpu: dict[int, int] = {}
        for item in run_items:
            for gpu_index, used_mb in item["vram_delta_mb"].items():
                max_delta_by_gpu[gpu_index] = max(max_delta_by_gpu.get(gpu_index, 0), used_mb)

        results.append(
            {
                "voice_id": voice_id,
                "voice_source_type": voice_source_type,
                "text_label": text_spec.label,
                "text_length": text_spec.text_length,
                "text_source_path": text_spec.source_path,
                "text_source_type": text_spec.source_type,
                "summary": summarize_runs(latencies),
                "max_vram_delta_mb": max_delta_by_gpu,
                "runs": run_items,
            }
        )

    return results, cleanup_voice_ids


def _synthesize_task(base_url: str, voice_id: str, text: str, language: str, round_index: int, request_index: int) -> dict[str, Any]:
    started_at = time.perf_counter()
    audio_bytes = synthesize_voice(base_url, voice_id, text, language)
    latency_seconds = time.perf_counter() - started_at
    return {
        "round_index": round_index,
        "request_index": request_index,
        "base_url": base_url,
        "voice_id": voice_id,
        "latency_seconds": latency_seconds,
        "response_bytes": len(audio_bytes),
    }


def benchmark_multi_server(
    base_urls: list[str],
    text_spec: TextInputSpec,
    language: str,
    rounds: int,
    gpu_indexes: list[int] | None,
    interval_seconds: float,
    registered: list[tuple[str, str]],
    voice_source_type: str,
) -> tuple[dict[str, Any], list[str]]:
    cleanup_voice_ids: list[str] = []

    round_results: list[dict[str, Any]] = []
    all_latencies: list[float] = []

    for round_index in range(1, rounds + 1):
        sampler = VramSampler(gpu_indexes=gpu_indexes, interval_seconds=interval_seconds)
        sampler.start()
        wall_started_at = time.perf_counter()
        request_results: list[dict[str, Any]] = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(registered)) as executor:
                futures = []
                request_index = 0
                for base_url, voice_id in registered:
                    request_index += 1
                    futures.append(
                        executor.submit(
                            _synthesize_task,
                            base_url,
                            voice_id,
                            text_spec.text,
                            language,
                            round_index,
                            request_index,
                        )
                    )
                for future in concurrent.futures.as_completed(futures):
                    request_results.append(future.result())
            wall_seconds = time.perf_counter() - wall_started_at
        finally:
            baseline, peak, delta = sampler.stop()
        request_results.sort(key=lambda item: item["request_index"])
        all_latencies.extend(item["latency_seconds"] for item in request_results)
        round_results.append(
            {
                "round_index": round_index,
                "wall_seconds": wall_seconds,
                "throughput_rps": len(request_results) / wall_seconds if wall_seconds else 0.0,
                "requests": request_results,
                "vram_baseline_mb": baseline,
                "vram_peak_mb": peak,
                "vram_delta_mb": delta,
                "vram_monitor_error": sampler.error,
            }
        )

    return {
        "servers": base_urls,
        "voice_ids": [voice_id for _, voice_id in registered],
        "voice_source_type": voice_source_type,
        "text_label": text_spec.label,
        "text_length": text_spec.text_length,
        "text_source_path": text_spec.source_path,
        "text_source_type": text_spec.source_type,
        "requests_per_round": len(registered),
        "rounds": rounds,
        "summary": summarize_runs(all_latencies),
        "round_results": round_results,
    }, cleanup_voice_ids


def cleanup_voices(base_urls: list[str], voice_ids: list[str]) -> list[str]:
    errors: list[str] = []
    for base_url, voice_id in zip(base_urls, voice_ids, strict=False):
        try:
            delete_voice(base_url, voice_id)
        except Exception as exc:  # pragma: no cover - cleanup only
            errors.append(f"failed to delete {voice_id} on {base_url}: {exc}")
    return errors


def print_section(title: str) -> None:
    print()
    print(f"== {title} ==")


def print_clone_summary(results: list[dict[str, Any]]) -> None:
    print_section("Clone Benchmark")
    for item in results:
        summary = item["summary"]
        audio = item["audio"]
        print(
            f"{Path(audio['path']).name}: duration={audio['duration_seconds']:.2f}s "
            f"size={audio['size_bytes']}B avg={summary['avg_seconds']:.3f}s "
            f"p95={summary['p95_seconds']:.3f}s max_vram_delta={item['max_vram_delta_mb']}"
        )


def print_parallel_clone_summary(results: list[dict[str, Any]]) -> None:
    print_section("Parallel Clone Benchmark")
    for item in results:
        summary = item["summary"]
        audio = item["audio"]
        print(
            f"{Path(audio['path']).name}: servers={len(item['servers'])} requests_per_round={item['requests_per_round']} "
            f"rounds={item['rounds']} avg={summary['avg_seconds']:.3f}s "
            f"p95={summary['p95_seconds']:.3f}s max_vram_delta={item['max_vram_delta_mb']}"
        )


def print_single_summary(results: list[dict[str, Any]]) -> None:
    print_section("Single TTS Benchmark")
    for item in results:
        summary = item["summary"]
        print(
            f"voice={item['voice_source_type']} text={item['text_label']} length={item['text_length']}: avg={summary['avg_seconds']:.3f}s "
            f"p95={summary['p95_seconds']:.3f}s max_vram_delta={item['max_vram_delta_mb']}"
        )


def print_multi_summary(result: dict[str, Any]) -> None:
    print_section("Multi Server Benchmark")
    summary = result["summary"]
    print(
        f"servers={len(result['servers'])} voice={result['voice_source_type']} requests_per_round={result['requests_per_round']} "
        f"text={result['text_label']} length={result['text_length']} avg={summary['avg_seconds']:.3f}s "
        f"p95={summary['p95_seconds']:.3f}s"
    )
    for round_item in result["round_results"]:
        latencies = [request["latency_seconds"] for request in round_item["requests"]]
        print(
            f"round={round_item['round_index']} wall={round_item['wall_seconds']:.3f}s "
            f"throughput={round_item['throughput_rps']:.2f}rps "
            f"request_latencies={[round(value, 3) for value in latencies]} "
            f"vram_delta={round_item['vram_delta_mb']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        dest="servers",
        action="append",
        required=True,
        help="Base server URL. Repeat to benchmark multiple servers.",
    )
    parser.add_argument(
        "--audio-file",
        dest="audio_files",
        action="append",
        default=[],
        help="Reference audio file. Repeat to compare different audio durations.",
    )
    parser.add_argument("--ref-text-file", help="Optional transcript file for ICL mode.")
    parser.add_argument("--language", default="Korean", help="Language for synthesis requests.")
    parser.add_argument(
        "--text-file",
        dest="text_files",
        action="append",
        default=[],
        help="Saved text file for single TTS tests. Repeatable. If provided, these files are used instead of generated text lengths.",
    )
    parser.add_argument(
        "--multi-text-file",
        help="Saved text file for multi-server tests. Defaults to the first --text-file when present.",
    )
    parser.add_argument(
        "--text-length",
        dest="text_lengths",
        type=int,
        action="append",
        default=[],
        help="Character length for generated single TTS tests. Ignored when --text-file is provided. Default: 1000, 5000, 10000.",
    )
    parser.add_argument(
        "--multi-text-length",
        type=int,
        default=1000,
        help="Character length for generated multi-server tests. Ignored when --multi-text-file or --text-file is provided.",
    )
    parser.add_argument("--clone-runs", type=int, default=3, help="Runs per audio file for clone benchmark.")
    parser.add_argument(
        "--clone-parallel",
        action="store_true",
        help="Run clone benchmark in parallel across all --server entries, sending one register request to each server per round.",
    )
    parser.add_argument("--single-runs", type=int, default=3, help="Runs per text input for single TTS benchmark.")
    parser.add_argument("--multi-rounds", type=int, default=3, help="Concurrent rounds for multi-server benchmark.")
    parser.add_argument(
        "--single-voice-id",
        help="Existing voice_id to use for the single TTS benchmark. If omitted, the script registers a temporary voice first.",
    )
    parser.add_argument(
        "--multi-voice-id",
        dest="multi_voice_ids",
        action="append",
        default=[],
        help="Existing voice_id for each server in the multi benchmark. Repeat once per --server. If omitted, temporary voices are registered.",
    )
    parser.add_argument(
        "--requests-per-server",
        type=int,
        default=1,
        help="Deprecated. Multi benchmark now sends exactly one request to each server per round, so this must stay 1.",
    )
    parser.add_argument("--gpu-index", dest="gpu_indexes", type=int, action="append", help="GPU index to monitor.")
    parser.add_argument("--vram-interval", type=float, default=0.1, help="VRAM polling interval in seconds.")
    parser.add_argument("--text-seed", help="Seed text used to generate long synthesis inputs.")
    parser.add_argument("--text-seed-file", help="Path to a seed text file.")
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--skip-clone", action="store_true", help="Skip the voice registration benchmark.")
    parser.add_argument("--skip-single", action="store_true", help="Skip the single synthesis benchmark.")
    parser.add_argument("--skip-multi", action="store_true", help="Skip the multi-server benchmark.")
    parser.add_argument("--keep-voices", action="store_true", help="Do not delete benchmark-created voices.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_urls = [normalize_base_url(url) for url in args.servers]
    audio_paths = [Path(item).expanduser().resolve() for item in args.audio_files]
    single_text_specs = resolve_single_text_specs(args)
    multi_text_spec = resolve_multi_text_spec(args, single_text_specs)
    ref_text = Path(args.ref_text_file).read_text(encoding="utf-8").strip() if args.ref_text_file else None

    needs_clone_audio = not args.skip_clone
    needs_single_audio = not args.skip_single and not args.single_voice_id
    needs_multi_audio = not args.skip_multi and not args.multi_voice_ids

    if needs_clone_audio or needs_single_audio or needs_multi_audio:
        if not audio_paths:
            raise SystemExit("--audio-file is required for clone benchmarks and for any benchmark that registers a temporary voice")

    for audio_path in audio_paths:
        if not audio_path.exists():
            raise SystemExit(f"audio file not found: {audio_path}")

    if args.multi_voice_ids and len(args.multi_voice_ids) != len(base_urls):
        raise SystemExit("--multi-voice-id must be provided once per --server")
    if not args.skip_multi and args.requests_per_server != 1:
        raise SystemExit(
            "This API server handles one request at a time. Multi benchmark now sends exactly one request per server, so --requests-per-server must be 1"
        )

    health = {}
    for base_url in base_urls:
        health[base_url] = get_health(base_url)

    results: dict[str, Any] = {
        "started_at_epoch": time.time(),
        "servers": base_urls,
        "health": health,
        "audio_files": [str(path) for path in audio_paths],
        "clone_parallel": args.clone_parallel,
        "single_text_inputs": [asdict(item) for item in single_text_specs],
        "multi_text_input": asdict(multi_text_spec),
        "language": args.language,
        "single_voice_id": args.single_voice_id,
        "multi_voice_ids": args.multi_voice_ids,
        "gpu_indexes": args.gpu_indexes,
        "vram_interval": args.vram_interval,
    }

    cleanup_pairs: list[tuple[str, str]] = []

    if not args.skip_clone:
        if args.clone_parallel:
            clone_results, clone_cleanup_pairs = benchmark_parallel_clone(
                base_urls=base_urls,
                audio_paths=audio_paths,
                ref_text=ref_text,
                runs=args.clone_runs,
                gpu_indexes=args.gpu_indexes,
                interval_seconds=args.vram_interval,
            )
            results["clone_parallel_benchmark"] = clone_results
            cleanup_pairs.extend(clone_cleanup_pairs)
            print_parallel_clone_summary(clone_results)
        else:
            clone_results, clone_voice_ids = benchmark_clone(
                base_url=base_urls[0],
                audio_paths=audio_paths,
                ref_text=ref_text,
                runs=args.clone_runs,
                gpu_indexes=args.gpu_indexes,
                interval_seconds=args.vram_interval,
            )
            results["clone_benchmark"] = clone_results
            cleanup_pairs.extend((base_urls[0], voice_id) for voice_id in clone_voice_ids)
            print_clone_summary(clone_results)

    if not args.skip_single:
        single_voice_source_type = "existing"
        single_cleanup_voice_ids: list[str] = []
        if args.single_voice_id:
            single_voice_id = args.single_voice_id
        else:
            single_voice_id = create_temporary_voice(
                base_url=base_urls[0],
                audio_path=audio_paths[0],
                ref_text=ref_text,
                name_prefix="bench-single",
            )
            single_cleanup_voice_ids = [single_voice_id]
            single_voice_source_type = "temporary_registered"
        single_results, single_voice_ids = benchmark_single_synthesis(
            base_url=base_urls[0],
            text_specs=single_text_specs,
            language=args.language,
            runs=args.single_runs,
            gpu_indexes=args.gpu_indexes,
            interval_seconds=args.vram_interval,
            voice_id=single_voice_id,
            voice_source_type=single_voice_source_type,
        )
        results["single_tts_benchmark"] = single_results
        cleanup_pairs.extend((base_urls[0], voice_id) for voice_id in single_cleanup_voice_ids + single_voice_ids)
        print_single_summary(single_results)

    if not args.skip_multi:
        multi_voice_source_type = "existing"
        multi_cleanup_voice_ids: list[str] = []
        if args.multi_voice_ids:
            registered = resolve_multi_voice_bindings(base_urls, args.multi_voice_ids)
        else:
            registered, multi_cleanup_voice_ids = create_temporary_multi_voices(
                base_urls=base_urls,
                audio_path=audio_paths[0],
                ref_text=ref_text,
                name_prefix="bench-multi",
            )
            multi_voice_source_type = "temporary_registered"
        multi_result, multi_voice_ids = benchmark_multi_server(
            base_urls=base_urls,
            text_spec=multi_text_spec,
            language=args.language,
            rounds=args.multi_rounds,
            gpu_indexes=args.gpu_indexes,
            interval_seconds=args.vram_interval,
            registered=registered,
            voice_source_type=multi_voice_source_type,
        )
        results["multi_server_benchmark"] = multi_result
        cleanup_pairs.extend((base_url, voice_id) for base_url, voice_id in zip(base_urls, multi_cleanup_voice_ids + multi_voice_ids, strict=False))
        print_multi_summary(multi_result)

    cleanup_errors: list[str] = []
    if not args.keep_voices:
        for base_url, voice_id in cleanup_pairs:
            try:
                delete_voice(base_url, voice_id)
            except Exception as exc:  # pragma: no cover - cleanup only
                cleanup_errors.append(f"failed to delete {voice_id} on {base_url}: {exc}")
    results["cleanup_errors"] = cleanup_errors

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print(f"saved benchmark report to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
