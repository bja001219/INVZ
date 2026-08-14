import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.models import Cut, CutImage, CutVideo, GenerationJob, GenerationKind, JobStatus
from app.providers.contracts import (
    GenerationProvider,
    GenerationRequest,
    PermanentProviderError,
    RetryableProviderError,
    SubmissionUncertainError,
    TaskResult,
)
from app.schemas import MockScenario

logger = logging.getLogger(__name__)


class GenerationWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider: GenerationProvider,
        clock: Clock,
        retry_base_delay_sec: float,
        provider_poll_interval_sec: float,
        generation_attempt_timeout_sec: float,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._clock = clock
        self._retry_base_delay_sec = retry_base_delay_sec
        self._provider_poll_interval_sec = provider_poll_interval_sec
        self._generation_attempt_timeout_sec = generation_attempt_timeout_sec
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="generation-worker")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Generation worker iteration failed")
            await self._clock.sleep(self._provider_poll_interval_sec)

    async def run_once(self) -> bool:
        if await self._recover_one_submitting():
            return True
        handled_submission, request = await self._prepare_one_submission()
        if handled_submission:
            if request is not None:
                await self._submit(request)
            return True
        if await self._expire_one_attempt():
            return True
        polling = await self._one_due_poll()
        if polling is None:
            return False
        job_id, external_task_id = polling
        await self._poll(job_id, external_task_id)
        return True

    async def _recover_one_submitting(self) -> bool:
        async with self._session_factory() as session, session.begin():
            job = await session.scalar(
                select(GenerationJob)
                .where(GenerationJob.status == JobStatus.SUBMITTING)
                .order_by(GenerationJob.created_at, GenerationJob.id)
                .limit(1)
            )
            if job is None:
                return False
            _terminal_failure(
                job,
                now=self._clock.now(),
                code="SUBMISSION_UNCERTAIN",
                message="Generation submission outcome is uncertain",
            )
            return True

    async def _prepare_one_submission(self) -> tuple[bool, GenerationRequest | None]:
        now = self._clock.now()
        async with self._session_factory() as session, session.begin():
            job = await session.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.status.in_([JobStatus.QUEUED, JobStatus.RETRY_WAIT]),
                    or_(GenerationJob.next_run_at.is_(None), GenerationJob.next_run_at <= now),
                )
                .order_by(GenerationJob.created_at, GenerationJob.id)
                .limit(1)
            )
            if job is None:
                return False, None
            source_image_url: str | None = None
            if job.kind is GenerationKind.VIDEO:
                source = (
                    await session.get(CutImage, job.source_image_id)
                    if job.source_image_id is not None
                    else None
                )
                if source is None:
                    _terminal_failure(
                        job,
                        now=now,
                        code="SOURCE_IMAGE_MISSING",
                        message="Generation source image is unavailable",
                    )
                    return True, None
                source_image_url = source.url
            job.attempt_count += 1
            job.status = JobStatus.SUBMITTING
            job.external_task_id = None
            job.next_run_at = None
            job.attempt_deadline_at = None
            job.last_error_code = None
            job.last_error_message = None
            scenario = MockScenario(job.mock_scenario) if job.mock_scenario is not None else None
            return (
                True,
                GenerationRequest(
                    job_id=job.id,
                    kind=job.kind,
                    prompt=job.prompt,
                    source_image_url=source_image_url,
                    duration_sec=5,
                    mock_scenario=scenario,
                    attempt_count=job.attempt_count,
                ),
            )

    async def _submit(self, request: GenerationRequest) -> None:
        try:
            submission = await self._provider.submit(request)
        except SubmissionUncertainError:
            await self._fail(
                request.job_id,
                code="SUBMISSION_UNCERTAIN",
                message="Generation submission outcome is uncertain",
            )
        except RetryableProviderError as error:
            await self._retry_or_fail(
                request.job_id,
                code=error.code,
                message="Generation provider temporarily unavailable",
                retry_after_seconds=error.retry_after_seconds,
            )
        except PermanentProviderError as error:
            await self._fail(
                request.job_id,
                code=error.code,
                message="Generation provider failed",
            )
        else:
            async with self._session_factory() as session, session.begin():
                job = await session.get(GenerationJob, request.job_id)
                if job is None or job.status is not JobStatus.SUBMITTING:
                    return
                now = self._clock.now()
                job.status = JobStatus.PROCESSING
                job.external_task_id = submission.external_task_id
                job.next_run_at = now + timedelta(seconds=self._provider_poll_interval_sec)
                job.attempt_deadline_at = now + timedelta(
                    seconds=self._generation_attempt_timeout_sec
                )

    async def _expire_one_attempt(self) -> bool:
        now = self._clock.now()
        async with self._session_factory() as session, session.begin():
            job = await session.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.status == JobStatus.PROCESSING,
                    GenerationJob.attempt_deadline_at.is_not(None),
                    GenerationJob.attempt_deadline_at <= now,
                )
                .order_by(GenerationJob.attempt_deadline_at, GenerationJob.created_at)
                .limit(1)
            )
            if job is None:
                return False
            self._set_retry_or_failure(
                job,
                code="ATTEMPT_TIMEOUT",
                message="Generation attempt timed out",
                retry_after_seconds=None,
            )
            return True

    async def _one_due_poll(self) -> tuple[UUID, str] | None:
        now = self._clock.now()
        async with self._session_factory() as session:
            job = await session.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.status == JobStatus.PROCESSING,
                    GenerationJob.external_task_id.is_not(None),
                    or_(GenerationJob.next_run_at.is_(None), GenerationJob.next_run_at <= now),
                )
                .order_by(GenerationJob.next_run_at, GenerationJob.created_at)
                .limit(1)
            )
            if job is None or job.external_task_id is None:
                return None
            return job.id, job.external_task_id

    async def _poll(self, job_id: UUID, external_task_id: str) -> None:
        try:
            result = await self._provider.poll(external_task_id)
        except RetryableProviderError as error:
            async with self._session_factory() as session, session.begin():
                job = await session.get(GenerationJob, job_id)
                if job is None or job.status is not JobStatus.PROCESSING:
                    return
                delay = max(
                    self._provider_poll_interval_sec,
                    error.retry_after_seconds or 0,
                )
                job.next_run_at = self._clock.now() + timedelta(seconds=delay)
                job.last_error_code = error.code
                job.last_error_message = "Generation provider temporarily unavailable"
        except PermanentProviderError as error:
            await self._fail(
                job_id,
                code=error.code,
                message="Generation provider failed",
            )
        else:
            await self._apply_poll_result(job_id, result)

    async def _apply_poll_result(self, job_id: UUID, result: TaskResult) -> None:
        if result.state == "PENDING":
            async with self._session_factory() as session, session.begin():
                job = await session.get(GenerationJob, job_id)
                if job is None or job.status is not JobStatus.PROCESSING:
                    return
                job.next_run_at = self._clock.now() + timedelta(
                    seconds=self._provider_poll_interval_sec
                )
                job.last_error_code = None
                job.last_error_message = None
            return
        if result.state == "FAILED":
            code = result.error_code or "GENERATION_PROVIDER_FAILED"
            message = result.error_message or "Generation provider failed"
            if result.retryable:
                await self._retry_or_fail(
                    job_id,
                    code=code,
                    message=message,
                    retry_after_seconds=None,
                )
            else:
                await self._fail(job_id, code=code, message=message)
            return
        if result.result_url is None or not result.result_url.strip():
            await self._fail(
                job_id,
                code="PROVIDER_RESPONSE_INVALID",
                message="Generation provider failed",
            )
            return
        await self._complete(job_id, result.result_url)

    async def _complete(self, job_id: UUID, result_url: str) -> None:
        async with self._session_factory() as session, session.begin():
            job = await session.get(GenerationJob, job_id)
            if job is None or job.status is not JobStatus.PROCESSING:
                return
            cut = await session.get(Cut, job.cut_id)
            if cut is None:
                _terminal_failure(
                    job,
                    now=self._clock.now(),
                    code="CUT_MISSING",
                    message="Generation target is unavailable",
                )
                return
            if job.kind is GenerationKind.IMAGE:
                artifact = CutImage(
                    cut_id=cut.id,
                    generation_job_id=job.id,
                    url=result_url,
                    input_prompt=job.prompt,
                )
                session.add(artifact)
                await session.flush()
                if cut.selected_image_id is None:
                    cut.selected_image_id = artifact.id
            else:
                if job.source_image_id is None:
                    _terminal_failure(
                        job,
                        now=self._clock.now(),
                        code="SOURCE_IMAGE_MISSING",
                        message="Generation source image is unavailable",
                    )
                    return
                video = CutVideo(
                    cut_id=cut.id,
                    cut_image_id=job.source_image_id,
                    generation_job_id=job.id,
                    url=result_url,
                    input_prompt=job.prompt,
                )
                session.add(video)
                await session.flush()
                if cut.selected_video_id is None and cut.selected_image_id == job.source_image_id:
                    cut.selected_video_id = video.id
            job.status = JobStatus.SUCCEEDED
            job.next_run_at = None
            job.attempt_deadline_at = None
            job.last_error_code = None
            job.last_error_message = None
            job.completed_at = self._clock.now()

    async def _retry_or_fail(
        self,
        job_id: UUID,
        *,
        code: str,
        message: str,
        retry_after_seconds: float | None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return
            self._set_retry_or_failure(
                job,
                code=code,
                message=message,
                retry_after_seconds=retry_after_seconds,
            )

    def _set_retry_or_failure(
        self,
        job: GenerationJob,
        *,
        code: str,
        message: str,
        retry_after_seconds: float | None,
    ) -> None:
        if job.attempt_count >= job.max_attempts:
            _terminal_failure(job, now=self._clock.now(), code=code, message=message)
            return
        delay = max(
            self._retry_base_delay_sec * 2 ** (job.attempt_count - 1),
            retry_after_seconds or 0,
        )
        job.status = JobStatus.RETRY_WAIT
        job.external_task_id = None
        job.attempt_deadline_at = None
        job.next_run_at = self._clock.now() + timedelta(seconds=delay)
        job.last_error_code = code
        job.last_error_message = message

    async def _fail(self, job_id: UUID, *, code: str, message: str) -> None:
        async with self._session_factory() as session, session.begin():
            job = await session.get(GenerationJob, job_id)
            if job is not None:
                _terminal_failure(job, now=self._clock.now(), code=code, message=message)


def _terminal_failure(job: GenerationJob, *, now: datetime, code: str, message: str) -> None:
    job.status = JobStatus.FAILED
    job.next_run_at = None
    job.attempt_deadline_at = None
    job.last_error_code = code
    job.last_error_message = message
    job.completed_at = now
