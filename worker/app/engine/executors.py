import concurrent.futures
import queue
import threading


class RealTimeThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """
    A tiny thread pool that drops *oldest* pending tasks when the queue is full.
    Matches your existing behavior but extracted into its own file.
    """
    def __init__(self, max_workers: int = 1):
        super().__init__(max_workers=max_workers)
        self._work_queue = queue.Queue(maxsize=5)
        self._lock = threading.Lock()

    def submit(self, fn, *args, **kwargs):
        with self._lock:
            if self._work_queue.full():
                try:
                    _ = self._work_queue.get_nowait()
                except Exception:
                    pass
        try:
            return super().submit(fn, *args, **kwargs)
        except queue.Full:
            return None
