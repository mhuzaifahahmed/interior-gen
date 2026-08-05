import logging
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class PipelineTimer:
    """Records wall-clock duration of named pipeline stages and can emit one
    structured summary log line covering the whole run.

    Stage names may repeat (e.g. one "generate_image" entry per tier, since
    they run concurrently) - each occurrence is recorded separately rather
    than averaged, so the summary shows real per-tier variance, not a
    smoothed-over number. Thread-safe: generate.py's image/materials stages
    run on worker threads, so concurrent stage() calls are expected.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._stages: list[dict] = []
        self._lock = threading.Lock()
        self._start = time.monotonic()

    @contextmanager
    def stage(self, name: str):
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - t0
            with self._lock:
                self._stages.append({"name": name, "duration_s": round(elapsed, 3)})
            logger.info(
                "pipeline stage timing: run=%s stage=%s duration_s=%.3f",
                self.run_id,
                name,
                elapsed,
            )

    def summary(self) -> dict:
        with self._lock:
            stages = list(self._stages)
        return {
            "run_id": self.run_id,
            "total_duration_s": round(time.monotonic() - self._start, 3),
            "stages": stages,
        }

    def log_summary(self) -> dict:
        summary = self.summary()
        logger.info(
            "pipeline timing summary: run=%s total_s=%.3f stages=%s",
            summary["run_id"],
            summary["total_duration_s"],
            summary["stages"],
        )
        return summary
