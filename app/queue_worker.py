"""Async queue worker for GPU-bound TTS inference with configurable concurrency."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class TTSJob:
    """A single TTS inference job."""
    func: Callable          # Can be sync or async callable
    kwargs: dict = field(default_factory=dict)
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class TTSQueue:
    """
    Asyncio-based queue with configurable concurrency.

    Args:
        max_workers: Number of jobs to process concurrently.
                     1 = sequential (one at a time), N = up to N simultaneous jobs.
        maxsize:     Maximum queue size (0 = unlimited).

    Usage:
        queue = TTSQueue(max_workers=1)
        tasks = queue.start_workers()

        # Submit an async callable (recommended for model-manager pattern):
        async def my_job():
            model = await model_manager.get_model(ModelType.BASE)
            return model.generate_voice_clone(text=..., language=...)
        result = await queue.submit(my_job)

        # Or submit a sync function with kwargs (legacy):
        result = await queue.submit(model.generate_voice_clone, text=..., language=...)

        # On shutdown:
        queue.stop(tasks)
    """

    def __init__(self, max_workers: int = 1, maxsize: int = 0):
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._max_workers = max_workers
        self._queue: asyncio.Queue[TTSJob] = asyncio.Queue(maxsize=maxsize)

    # ── public API ──────────────────────────────────────────────────

    async def submit(self, func: Callable, **kwargs: Any) -> Any:
        """Enqueue a job and wait for the result."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        job = TTSJob(func=func, kwargs=kwargs, future=future)
        await self._queue.put(job)
        logger.info("Job enqueued (pending=%d, workers=%d)", self._queue.qsize(), self._max_workers)
        return await future

    @property
    def pending_count(self) -> int:
        """Number of jobs waiting in the queue."""
        return self._queue.qsize()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    # ── lifecycle ───────────────────────────────────────────────────

    def start_workers(self) -> list[asyncio.Task]:
        
        """Spawn worker tasks. Returns list of tasks (keep reference for shutdown)."""
        tasks = []
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker_loop(worker_id=i))
            tasks.append(task)
        logger.info("Started %d TTS worker(s)", self._max_workers)
        return tasks

    @staticmethod
    async def stop(tasks: list[asyncio.Task]) -> None:
        """Cancel all worker tasks and wait for them to finish."""
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All TTS workers stopped")

    # ── worker loop ─────────────────────────────────────────────────

    async def _worker_loop(self, worker_id: int) -> None:
        """Single worker: continuously dequeue and execute jobs."""
        logger.info("TTS worker-%d started", worker_id)
        try:
            while True:
                job = await self._queue.get()
                try:
                    logger.info("Worker-%d processing job (remaining=%d)", worker_id, self._queue.qsize())

                    if asyncio.iscoroutinefunction(job.func) or asyncio.iscoroutine(job.func):
                        # Async callable (e.g. closure with model_manager.get_model)
                        result = await job.func(**job.kwargs)
                    else:
                        # Sync callable — run in thread
                        result = await asyncio.to_thread(job.func, **job.kwargs)

                    job.future.set_result(result)
                except Exception as exc:
                    job.future.set_exception(exc)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            logger.info("TTS worker-%d stopped", worker_id)
