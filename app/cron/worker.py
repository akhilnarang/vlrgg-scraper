import asyncio
import logging
from asyncio import Task
from typing import Any

from arq import cron
from arq.worker import create_worker

from app.core.config import settings
from app.services.fcm import close_app

logger = logging.getLogger(__name__)
_ARQ_RESTART_DELAY = 5


class ArqWorker:
    """Run the scheduled arq jobs and restart a stopped worker."""

    def __init__(self) -> None:
        """Initialize the worker task reference.

        :return: None.
        """
        self.task: Task | None = None

    async def start(self, **kwargs: Any) -> None:
        """Schedule enabled jobs and start the worker task.

        :param kwargs: Arguments forwarded to the arq worker.
        :return: None.
        """
        cron_jobs = []
        if settings.ENABLE_CACHE:
            cron_jobs.extend(
                [
                    cron("app.cron.jobs.rankings_cron", hour=None, minute={0, 30}),
                    cron(
                        "app.cron.jobs.matches_cron",
                        hour=None,
                        minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
                    ),
                    cron("app.cron.jobs.events_cron", hour=None, minute={0, 30}),
                    cron("app.cron.jobs.news_cron", hour=None, minute={0, 30}),
                    cron("app.cron.jobs.standings_cron", hour=0, minute=0),
                ]
            )

        # Only try to run the FCM cron if we have a service account JSON
        if settings.ENABLE_CACHE and settings.GOOGLE_APPLICATION_CREDENTIALS is not None:
            cron_jobs.append(cron("app.cron.jobs.fcm_notification_cron", hour=None, minute={0, 15, 30, 45}))
        if settings.ENABLE_LIVE_PUSH:
            # Every minute; the fixed job_id makes arq skip a run while the previous one is still going.
            cron_jobs.append(cron("app.cron.live_push.live_push_cron", second=0, job_id="live_push_cron"))

        self.task = asyncio.create_task(self._run(cron_jobs, kwargs))

    async def _run(self, cron_jobs: list, kwargs: dict[str, Any]) -> None:
        """Restart the arq worker if it exits unexpectedly.

        :param cron_jobs: Scheduled arq jobs.
        :param kwargs: Arguments forwarded to the arq worker.
        :return: None.
        """
        while True:
            worker = create_worker({"cron_jobs": cron_jobs}, **kwargs)
            try:
                await worker.async_run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("arq worker crashed; restarting")
            else:
                logger.error("arq worker stopped unexpectedly; restarting")
            finally:
                try:
                    await worker.close()
                except Exception:
                    logger.warning("failed to close arq worker", exc_info=True)
            await asyncio.sleep(_ARQ_RESTART_DELAY)

    async def stop(self) -> None:
        """Cancel the worker and close its Firebase app.

        :return: None.
        """
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await close_app()


arq_worker = ArqWorker()
