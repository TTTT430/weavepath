from __future__ import annotations

import queue
import threading

from agent_runtime.service import AgentRuntimeService


class AgentRunDispatcher:
    """Single-process durable run dispatcher.

    The database is the source of truth; the in-memory queue contains only run
    ids. A browser reload therefore has no effect on execution. On process
    startup, still-queued ids can be submitted again without replaying a model
    call that had already started.
    """

    def __init__(self, service: AgentRuntimeService) -> None:
        self.service = service
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._guard = threading.Lock()
        self._started = False
        self._stopping = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._guard:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._work, name="weavepath-agent-runs", daemon=True,
            )
            self._thread.start()

    def submit(self, run_id: str) -> None:
        with self._guard:
            if self._stopping:
                raise RuntimeError("Agent run dispatcher is stopping")
        self._queue.put(run_id)

    def stop(self) -> None:
        with self._guard:
            if not self._started or self._stopping:
                return
            self._stopping = True
        self._queue.put(None)
        thread = self._thread
        if thread is not None:
            # Provider reads intentionally have no deadline. Do not let one
            # upstream call block application shutdown indefinitely.
            thread.join(timeout=1.0)

    def _work(self) -> None:
        while True:
            run_id = self._queue.get()
            try:
                if run_id is None:
                    return
                with self._guard:
                    if self._stopping:
                        return
                try:
                    self.service.resume_queued(run_id)
                except Exception:
                    # AgentRuntimeService has already journaled a stable,
                    # redacted durable failure for every execution error.
                    continue
            finally:
                self._queue.task_done()
