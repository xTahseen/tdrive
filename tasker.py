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

# Read from .env (UPLOAD_WORKERS / UPLOAD_QUEUE_PER_USER) via Config, with
# the same defaults as before if those env vars aren't set.
MAX_GLOBAL_WORKERS  = Config.UPLOAD_WORKERS
MAX_QUEUE_PER_USER  = Config.UPLOAD_QUEUE_PER_USER

_job_id_counter = itertools.count(1)


@dataclass
class UploadJob:
    user_id:    int
    file_name:  str
    file_size:  int
    # coroutine factory — called by the worker with no args
    coro_fn:    Callable[[], Awaitable[Any]]
    # Unique id for this job — used to build a collision-free temp file path
    # even when two jobs share the same user_id + file_name (e.g. the user
    # re-sends the same file while a previous copy is still queued/active).
    job_id:     int = field(default_factory=lambda: next(_job_id_counter))
    # The Pyrogram Message object for the queue/status message.
    # Passed in by on_file.py via the status_msg_holder list.
    # The worker edits it in-place as the job progresses.
    task:       asyncio.Task | None = field(default=None, init=False)


class UploadQueueManager:
    def __init__(self):
        # user_id -> list[UploadJob]  (ordered, pending jobs)
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
        # Refresh position messages immediately so existing queued jobs for
        # this user show their current (still-correct) position right away,
        # and so the dispatcher's view of pending count stays in sync with
        # what's shown to the user. The new job itself isn't in this list's
        # "already-sent" messages yet — its own message is sent by the
        # caller right after this call returns.
        await self._update_position_messages(user_id)
        return True, ""

    async def _update_position_messages(self, user_id: int):
        """
        After a job finishes, edit each remaining queued job's status message
        to show its updated position number.
        """
        pending = self._queues.get(user_id, [])
        for idx, job in enumerate(pending):
            # status_msg_holder is a list[Message|None] stored on the job via closure.
            # We can't easily reach it here without storing it explicitly on the job.
            # Instead we expose a callback mechanism — see _position_update_cb below.
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
                # Mark the user active HERE, in the same synchronous step as
                # popping the job — not inside the worker. asyncio.Queue.put()
                # on an unbounded queue never actually awaits/yields control,
                # but to be safe against future changes we still want the
                # invariant enforced at the single point that decides
                # "this user now has a job in flight" rather than relying on
                # a worker to get around to it later. Without this, the
                # dispatcher's next loop iteration (which runs before any
                # worker has had a chance to pick the job up) sees this user
                # as still "not active" and dispatches a SECOND job for the
                # same user — causing two concurrent downloads/uploads that
                # collide on the same temp file path and the same network
                # connection (this was the source of the "Broken pipe" /
                # "No such file" errors and the queue position jumping from
                # #5 straight to #1).
                self._active[user_id] = job
                await self._pipe.put((user_id, job))
                # Recompute remaining positions for this user immediately,
                # since one of their pending jobs just left the queue.
                await self._update_position_messages(user_id)
                dispatched = True
            if not dispatched:
                await asyncio.sleep(0.2)

    async def _worker(self, worker_id: int):
        while True:
            user_id, job = await self._pipe.get()
            # NOTE: self._active[user_id] is already set by the dispatcher
            # at the moment the job was popped from the queue (see
            # _dispatcher above) — that's what prevents a second job for
            # the same user being dispatched while this one is in flight.
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
                # Update position messages for all remaining jobs of this user
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
