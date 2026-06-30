"""
tasker.py — per-user upload queue with global concurrency cap.

Design
------
Each user gets their own asyncio.Queue. A pool of global worker coroutines
(MAX_GLOBAL_WORKERS) pull jobs from a shared round-robin dispatcher so:

  • A single heavy user can't starve others.
  • Never more than MAX_GLOBAL_WORKERS simultaneous uploads.
  • Each user can queue up to MAX_QUEUE_PER_USER pending jobs; excess is rejected.
  • When a job starts, its queue-position message is edited in-place to
    "Processing…" rather than sending a new message.
  • After any job finishes, all remaining queued jobs for that user get their
    position messages updated (e.g. #2 → #1).
"""

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any

from config import Config

logger = logging.getLogger(__name__)

MAX_GLOBAL_WORKERS  = Config.UPLOAD_WORKERS
MAX_QUEUE_PER_USER  = Config.UPLOAD_QUEUE_PER_USER

_job_id_counter = itertools.count(1)


@dataclass
class UploadJob:
    user_id:    int
    file_name:  str
    file_size:  int
    coro_fn:    Callable[[], Awaitable[Any]]
    job_id:     int = field(default_factory=lambda: next(_job_id_counter))
    task:       asyncio.Task | None = field(default=None, init=False)


class UploadQueueManager:
    def __init__(self):
        self._queues:  dict[int, list[UploadJob]] = {}
        self._active:  dict[int, UploadJob]       = {}
        self._worker_tasks: list[asyncio.Task]    = []
        self._pipe:    asyncio.Queue              = asyncio.Queue()
        self._started  = False

    async def start(self):
        if self._started:
            return
        self._started = True
        for i in range(MAX_GLOBAL_WORKERS):
            t = asyncio.ensure_future(self._worker(i))
            self._worker_tasks.append(t)
        asyncio.ensure_future(self._dispatcher())
        logger.info(
            f"UploadQueueManager started — "
            f"{MAX_GLOBAL_WORKERS} workers, {MAX_QUEUE_PER_USER} slots/user"
        )

    async def stop(self):
        for t in self._worker_tasks:
            t.cancel()

    def queue_size(self, user_id: int) -> int:
        return len(self._queues.get(user_id, []))

    def is_active(self, user_id: int) -> bool:
        return user_id in self._active

    async def cancel_active(self, user_id: int) -> bool:
        job = self._active.get(user_id)
        if job and job.task and not job.task.done():
            job.task.cancel()
            return True
        return False

    async def enqueue(self, job: UploadJob) -> tuple[bool, str]:
        user_id = job.user_id
        q = self._queues.setdefault(user_id, [])
        if len(q) >= MAX_QUEUE_PER_USER:
            return False, (
                f"⚠️ You already have **{MAX_QUEUE_PER_USER}** files queued.\n"
                "Please wait for them to finish before sending more."
            )
        q.append(job)
        logger.debug(
            f"[QUEUE] user={user_id} pending={len(q)} "
            f"active={self.is_active(user_id)} file={job.file_name!r}"
        )
        await self._update_position_messages(user_id)
        return True, ""

    async def _update_position_messages(self, user_id: int):
        """
        After a job finishes, edit each remaining queued job's status message
        to show its updated position number.
        """
        pending = self._queues.get(user_id, [])
        for idx, job in enumerate(pending):
            if hasattr(job, "_position_cb") and job._position_cb:
                try:
                    await job._position_cb(idx + 1)
                except Exception:
                    pass

    async def _dispatcher(self):
        while True:
            dispatched = False
            for user_id, q in list(self._queues.items()):
                if not q:
                    continue
                if user_id in self._active:
                    continue
                job = q.pop(0)
                self._active[user_id] = job
                await self._pipe.put((user_id, job))
                await self._update_position_messages(user_id)
                dispatched = True
            if not dispatched:
                await asyncio.sleep(0.2)

    async def _worker(self, worker_id: int):
        while True:
            user_id, job = await self._pipe.get()
            logger.debug(
                f"[WORKER-{worker_id}] starting user={user_id} file={job.file_name!r}"
            )
            try:
                task = asyncio.ensure_future(job.coro_fn())
                job.task = task
                await task
            except asyncio.CancelledError:
                logger.info(f"[WORKER-{worker_id}] cancelled user={user_id}")
            except Exception as e:
                logger.error(
                    f"[WORKER-{worker_id}] error user={user_id}: {e}", exc_info=True
                )
            finally:
                self._active.pop(user_id, None)
                self._pipe.task_done()
                await self._update_position_messages(user_id)
                logger.debug(
                    f"[WORKER-{worker_id}] finished user={user_id}"
                )


_manager: UploadQueueManager | None = None


def get_queue_manager() -> UploadQueueManager:
    global _manager
    if _manager is None:
        _manager = UploadQueueManager()
    return _manager
