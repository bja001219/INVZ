import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
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

_ANCHOR_CUT_ORDER = 1
_CANDIDATE_FLOOR = 24
_ACTIVE_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.SUBMITTING,
    JobStatus.PROCESSING,
    JobStatus.RETRY_WAIT,
)


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
        concurrency: int = 1,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._clock = clock
        self._retry_base_delay_sec = retry_base_delay_sec
        self._provider_poll_interval_sec = provider_poll_interval_sec
        self._generation_attempt_timeout_sec = generation_attempt_timeout_sec
        self._concurrency = max(1, concurrency)
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
        """Advance the queue by one tick.

        Claiming is serial inside a single transaction so two jobs can never be claimed twice;
        only the slow provider calls fan out, bounded by the configured concurrency.
        """
        if await self._recover_one_submitting():
            return True
        touched, requests = await self._claim_due_submissions(self._concurrency)
        if touched:
            if requests:
                await asyncio.gather(*(self._submit(request) for request in requests))
            return True
        if await self._expire_one_attempt():
            return True
        polls = await self._claim_due_polls(self._concurrency)
        if not polls:
            return False
        await asyncio.gather(
            *(self._poll(job_id, external_task_id) for job_id, external_task_id in polls)
        )
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

    async def _claim_due_submissions(
        self, limit: int
    ) -> tuple[bool, list[GenerationRequest]]:
        """Claim up to `limit` due jobs in one transaction.

        Returns whether any row was written, which is separate from how many requests came back:
        a job can be terminally failed here (missing source image) without producing a request,
        and a job can be skipped without being written (waiting on the scene anchor).
        """
        now = self._clock.now()
        touched = False
        requests: list[GenerationRequest] = []
        async with self._session_factory() as session, session.begin():
            candidates = list(
                await session.scalars(
                    select(GenerationJob)
                    .where(
                        GenerationJob.status.in_([JobStatus.QUEUED, JobStatus.RETRY_WAIT]),
                        or_(
                            GenerationJob.next_run_at.is_(None),
                            GenerationJob.next_run_at <= now,
                        ),
                    )
                    .order_by(GenerationJob.created_at, GenerationJob.id)
                    # Over-fetch: anchor-gated jobs are filtered out below, and a plain
                    # LIMIT would let one gated job hide the runnable jobs behind it.
                    .limit(max(limit * 4, _CANDIDATE_FLOOR))
                )
            )
            for job in candidates:
                if len(requests) >= limit:
                    break
                source_image_url: str | None = None
                reference_image_url: str | None = None
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
                        touched = True
                        continue
                    source_image_url = source.url
                else:
                    ready, reference = await self._resolve_anchor_reference(session, job)
                    if not ready:
                        continue
                    if reference is not None:
                        job.reference_image_id, reference_image_url = reference
                job.attempt_count += 1
                job.status = JobStatus.SUBMITTING
                job.external_task_id = None
                job.next_run_at = None
                job.attempt_deadline_at = None
                job.last_error_code = None
                job.last_error_message = None
                touched = True
                scenario = (
                    MockScenario(job.mock_scenario) if job.mock_scenario is not None else None
                )
                requests.append(
                    GenerationRequest(
                        job_id=job.id,
                        kind=job.kind,
                        prompt=job.prompt,
                        source_image_url=source_image_url,
                        duration_sec=5,
                        mock_scenario=scenario,
                        attempt_count=job.attempt_count,
                        reference_image_url=reference_image_url,
                    )
                )
        return touched, requests

    async def _resolve_anchor_reference(
        self, session: AsyncSession, job: GenerationJob
    ) -> tuple[bool, tuple[UUID, str] | None]:
        """Decide whether an IMAGE job may run now, and which anchor image it should reference.

        Cuts after the first stay consistent by referencing the scene's anchor: the selected
        image of Cut 1. They wait only while an anchor image job is still active, so a
        permanently failed Cut 1 opens the gate instead of stalling the scene forever.
        """
        cut = await session.get(Cut, job.cut_id)
        if cut is None or cut.order == _ANCHOR_CUT_ORDER:
            return True, None
        anchor_cut = await session.scalar(
            select(Cut).where(Cut.scene_id == cut.scene_id, Cut.order == _ANCHOR_CUT_ORDER)
        )
        if anchor_cut is None:
            return True, None
        if anchor_cut.selected_image_id is not None:
            anchor_image = await session.get(CutImage, anchor_cut.selected_image_id)
            if anchor_image is not None:
                return True, (anchor_image.id, anchor_image.url)
        pending_anchor = await session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.cut_id == anchor_cut.id,
                GenerationJob.kind == GenerationKind.IMAGE,
                GenerationJob.status.in_(_ACTIVE_STATUSES),
            )
        )
        if pending_anchor:
            return False, None
        return True, None

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

    async def _claim_due_polls(self, limit: int) -> list[tuple[UUID, str]]:
        now = self._clock.now()
        async with self._session_factory() as session:
            jobs = list(
                await session.scalars(
                    select(GenerationJob)
                    .where(
                        GenerationJob.status == JobStatus.PROCESSING,
                        GenerationJob.external_task_id.is_not(None),
                        or_(
                            GenerationJob.next_run_at.is_(None),
                            GenerationJob.next_run_at <= now,
                        ),
                    )
                    .order_by(GenerationJob.next_run_at, GenerationJob.created_at)
                    .limit(limit)
                )
            )
        return [(job.id, job.external_task_id) for job in jobs if job.external_task_id]

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
