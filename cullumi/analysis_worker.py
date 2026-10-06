from __future__ import annotations

import ctypes
import multiprocessing
import os
import queue
import threading
import time
from multiprocessing.context import BaseContext
from pathlib import Path
from typing import Any

# NIQE uses tiny linear algebra operations. Avoid multiplying BLAS worker
# threads inside each spawned photo-analysis process.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from .media import analyze_photo, failed_photo_analysis
from .niqe import initialize_niqe

DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 60.0

# A worker is restarted after this many tasks. Restarting bounds whatever a
# long-lived process accumulates, but each one costs ~0.38s (measured: 412ms
# for the first task vs 34ms for the rest, and importing cullumi.media alone is
# 180ms). At 50 tasks a 5000-photo scan paid that ~100 times per worker. Raised
# after measuring three full passes through one process: peak RSS went 122.0 →
# 122.6MB across 870 tasks, i.e. the footprint plateaus rather than creeping, so
# the low cap was buying no real protection.
DEFAULT_MAX_TASKS_PER_WORKER = 500

# Safety ceilings handed to the worker (enforced via a job object on Windows).
MIN_WORKER_MEMORY_BYTES = 512 * 1024 * 1024
MAX_WORKER_MEMORY_BYTES = 1536 * 1024 * 1024

# What one worker must be assumed to hold. This is the WORST CASE, not the
# average: measured per image in an isolated process, a 48MP JPEG peaks at
# 89.6MB while a 48MP PNG peaks at 622.3MB, because JPEG goes through
# draft() down-scaling and PNG/HEIC decode at full resolution. Sizing the pool
# by the ~130MB average over a mostly-JPEG library would let 6 workers hold
# 3.65GB and blow the budget below.
MEASURED_WORKER_PEAK_BYTES = 640 * 1024 * 1024

# Share of physical memory the analysis pool may occupy, and its absolute cap.
WORKER_MEMORY_BUDGET_FRACTION = 0.20
WORKER_MEMORY_BUDGET_CEILING = 3 * 1024**3

# Above the memory bound more workers stop paying: measured 4 workers 3.49s vs
# 6 workers 3.28s on a 290-photo library (a further 6%), for 50% more memory.
MAX_PARALLEL_WORKERS = 6


class AnalysisCancelled(Exception):
    """Raised when a scan is cancelled while a worker task is active."""


def _physical_memory_bytes() -> int:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return 4 * 1024 * 1024 * 1024


def default_worker_memory_limit() -> int:
    target = int(_physical_memory_bytes() * 0.20)
    return max(MIN_WORKER_MEMORY_BYTES, min(MAX_WORKER_MEMORY_BYTES, target))


_WORKER_JOB_HANDLE: int | None = None


def _apply_windows_memory_limit(memory_limit: int) -> None:
    global _WORKER_JOB_HANDLE
    if os.name != "nt" or memory_limit <= 0:
        return

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_ulong),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_ulong),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_ulong),
            ("SchedulingClass", ctypes.c_ulong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x100 | 0x2000
    information.ProcessMemoryLimit = memory_limit
    configured = kernel32.SetInformationJobObject(
        ctypes.c_void_p(job),
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        ctypes.c_void_p(job), kernel32.GetCurrentProcess()
    )
    if assigned:
        _WORKER_JOB_HANDLE = int(job)
    else:
        kernel32.CloseHandle(ctypes.c_void_p(job))


def _worker_main(
    requests: Any,
    responses: Any,
    memory_limit: int,
) -> None:
    _apply_windows_memory_limit(memory_limit)
    niqe_initialized = False
    while True:
        request = requests.get()
        if request is None:
            return
        task_id, source, thumbnail, niqe_enabled, *options = request
        if niqe_enabled and not niqe_initialized:
            initialize_niqe()
            niqe_initialized = True
        try:
            kwargs = {"niqe_only": True} if options and options[0] else {}
            result = analyze_photo(Path(source), Path(thumbnail), niqe_enabled=niqe_enabled, **kwargs)
        except BaseException as error:
            result = failed_photo_analysis(
                Path(source), Path(thumbnail), str(error) or error.__class__.__name__
            )
            result["_worker_failure"] = True
        responses.put((task_id, result))


class PhotoAnalysisRunner:
    """Run scan-time photo decoding in one restartable worker process."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_ANALYSIS_TIMEOUT_SECONDS,
        max_tasks_per_worker: int = DEFAULT_MAX_TASKS_PER_WORKER,
        memory_limit: int | None = None,
        context: BaseContext | None = None,
        worker_main: Any = _worker_main,
    ) -> None:
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self.max_tasks_per_worker = max(1, int(max_tasks_per_worker))
        self.memory_limit = (
            default_worker_memory_limit() if memory_limit is None else memory_limit
        )
        self._context = context or multiprocessing.get_context("spawn")
        self._worker_main = worker_main
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._process: multiprocessing.Process | None = None
        self._requests: Any = None
        self._responses: Any = None
        self._task_id = 0
        self._tasks_completed = 0

    def _start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._stop()
        self._requests = self._context.Queue(maxsize=1)
        self._responses = self._context.Queue(maxsize=1)
        self._process = self._context.Process(
            target=self._worker_main,
            args=(self._requests, self._responses, self.memory_limit),
            daemon=True,
        )
        self._process.start()
        self._tasks_completed = 0

    def _stop(self) -> None:
        process = self._process
        requests = self._requests
        responses = self._responses
        self._process = None
        self._requests = None
        self._responses = None
        if process is not None:
            if process.is_alive():
                try:
                    if requests is not None:
                        requests.put_nowait(None)
                except (OSError, ValueError, queue.Full):
                    pass
                process.join(0.5)
            if process.is_alive():
                process.terminate()
                process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            process.close()
        for channel in (requests, responses):
            if channel is not None:
                try:
                    channel.close()
                    channel.join_thread()
                except (OSError, ValueError):
                    pass

    def _acquire(self, cancel: threading.Event | None) -> None:
        while not self._lock.acquire(timeout=0.1):
            if self._closed.is_set() or (cancel is not None and cancel.is_set()):
                raise AnalysisCancelled

    def analyze(
        self,
        source: Path,
        thumbnail: Path,
        cancel: threading.Event | None = None,
        niqe_enabled: bool = True,
        niqe_only: bool = False,
    ) -> dict[str, Any]:
        self._acquire(cancel)
        try:
            if self._closed.is_set() or (cancel is not None and cancel.is_set()):
                raise AnalysisCancelled
            for attempt in range(2):
                self._start()
                assert self._process is not None
                assert self._requests is not None
                assert self._responses is not None
                self._task_id += 1
                task_id = self._task_id
                self._requests.put(
                    (task_id, str(source), str(thumbnail), niqe_enabled, niqe_only)
                )
                deadline = time.monotonic() + self.timeout_seconds
                retry = False
                while True:
                    if self._closed.is_set() or (cancel is not None and cancel.is_set()):
                        self._stop()
                        raise AnalysisCancelled
                    if not self._process.is_alive():
                        exit_code = self._process.exitcode
                        self._stop()
                        if attempt == 0:
                            retry = True
                            break
                        return failed_photo_analysis(
                            source,
                            thumbnail,
                            f"图片分析进程异常退出（退出码 {exit_code}）",
                        )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._stop()
                        return failed_photo_analysis(
                            source,
                            thumbnail,
                            f"图片分析超过 {self.timeout_seconds:g} 秒，已安全跳过",
                        )
                    try:
                        response_id, result = self._responses.get(
                            timeout=min(0.1, remaining)
                        )
                    except queue.Empty:
                        continue
                    if response_id != task_id:
                        continue
                    worker_failure = bool(result.pop("_worker_failure", False))
                    if worker_failure and attempt == 0:
                        retry = True
                        break
                    self._tasks_completed += 1
                    if self._tasks_completed >= self.max_tasks_per_worker:
                        self._stop()
                    return result
                if retry:
                    continue
            raise AssertionError("unreachable worker retry state")
        finally:
            self._lock.release()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            self._stop()


def parallel_worker_count() -> int:
    """How many analysis processes to run at once.

    Bounded by the memory budget, one fewer than the logical core count, and a
    hard cap. The memory term divides by the worst-case per-worker footprint
    rather than the 1.5GB safety ceiling: using the ceiling made this evaluate
    to 2 on every machine, which masked the CPU term and left a 10-core machine
    running 2 workers regardless of how much memory it had.

    On a 16GB/10-core machine this returns 4. Measured scan of a 290-photo
    library: 2 workers 5.23s -> 4 workers 3.49s (1.50x). Six would reach 3.28s
    but needs 3.65GB at worst case, above the 3GB budget, so it is not used.
    """
    budget = min(
        int(_physical_memory_bytes() * WORKER_MEMORY_BUDGET_FRACTION),
        WORKER_MEMORY_BUDGET_CEILING,
    )
    by_memory = max(1, budget // MEASURED_WORKER_PEAK_BYTES)
    by_cpu = max(1, (os.cpu_count() or 1) - 1)
    return max(1, min(MAX_PARALLEL_WORKERS, by_cpu, by_memory))


class PhotoAnalysisPool:
    """One shared, bounded pool across projects; processes start lazily."""

    def __init__(self) -> None:
        self.parallel_capacity = parallel_worker_count()
        self._runners = [PhotoAnalysisRunner() for _ in range(self.parallel_capacity)]
        self._available: queue.Queue = queue.Queue()
        self._closed = threading.Event()
        for runner in self._runners:
            self._available.put(runner)

    def analyze(self, source, thumbnail, cancel=None, niqe_enabled=True, niqe_only=False, parallel=False):
        if self._closed.is_set():
            raise AnalysisCancelled
        if not parallel:
            return self._runners[0].analyze(source, thumbnail, cancel, niqe_enabled, niqe_only)
        while True:
            if self._closed.is_set() or (cancel is not None and cancel.is_set()):
                raise AnalysisCancelled
            try:
                runner = self._available.get(timeout=0.1)
                break
            except queue.Empty:
                continue
        try:
            return runner.analyze(source, thumbnail, cancel, niqe_enabled, niqe_only)
        finally:
            self._available.put(runner)

    def close(self) -> None:
        self._closed.set()
        # Set every stop flag before waiting for any individual process.
        for runner in self._runners:
            runner._closed.set()
        for runner in self._runners:
            runner.close()


__all__ = [
    "AnalysisCancelled",
    "PhotoAnalysisPool",
    "PhotoAnalysisRunner",
    "default_worker_memory_limit",
    "parallel_worker_count",
]
