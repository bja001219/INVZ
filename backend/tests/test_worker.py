import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import build_engine
from app.main import create_app
from app.models import (
    CLAIMABLE_JOB_STATUSES,
    Base,
    Cut,
    CutImage,
    CutVideo,
    GenerationJob,
    GenerationKind,
    JobStatus,
    Scene,
)
from app.providers.contracts import (
    GenerationRequest,
    RetryableProviderError,
    Submission,
    SubmissionUncertainError,
    TaskResult,
)
from app.providers.kie import KieGenerationProvider
from app.providers.mock import MockGenerationProvider
from app.schemas import MockScenario
from app.worker import GenerationWorker


class FakeClock(Clock):
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        self.sleep_calls: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        await asyncio.Future()

    def advance(self, *, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


SubmitObserver = Callable[[GenerationRequest], Awaitable[None]]


class StubProvider:
    def __init__(
        self,
        *,
        submit_effects: list[Submission | Exception] | None = None,
        poll_effects: list[TaskResult | Exception] | None = None,
        submit_observer: SubmitObserver | None = None,
    ) -> None:
        self.submit_effects = submit_effects or [Submission("task-1")]
        self.poll_effects = poll_effects or [TaskResult(state="PENDING")]
        self.submit_observer = submit_observer
        self.submit_requests: list[GenerationRequest] = []
        self.poll_requests: list[str] = []

    async def submit(self, request: GenerationRequest) -> Submission:
        self.submit_requests.append(request)
        if self.submit_observer is not None:
            await self.submit_observer(request)
        effect = self.submit_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    async def poll(self, external_task_id: str) -> TaskResult:
        self.poll_requests.append(external_task_id)
        effect = self.poll_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


@pytest_asyncio.fixture
async def worker_db(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def add_cut(factory: async_sessionmaker[AsyncSession], *, order: int = 1) -> Cut:
    async with factory.begin() as session:
        scene = Scene(user_prompt="prompt", title="title", scenario="scenario")
        session.add(scene)
        await session.flush()
        cut = Cut(
            scene_id=scene.id,
            order=order,
            image_prompt=f"image prompt {order}",
            video_prompt=f"video prompt {order}",
            duration_sec=5,
        )
        session.add(cut)
        await session.flush()
        return cut


async def add_image_job(
    factory: async_sessionmaker[AsyncSession],
    cut: Cut,
    *,
    status: JobStatus = JobStatus.QUEUED,
    attempt_count: int = 0,
    max_attempts: int = 3,
    next_run_at: datetime | None = None,
    external_task_id: str | None = None,
    attempt_deadline_at: datetime | None = None,
    scenario: MockScenario | None = None,
    created_at: datetime | None = None,
) -> GenerationJob:
    async with factory.begin() as session:
        version = cast(
            int,
            await session.scalar(
                select(func.coalesce(func.max(GenerationJob.version), 0) + 1).where(
                    GenerationJob.cut_id == cut.id,
                    GenerationJob.kind == GenerationKind.IMAGE,
                )
            ),
        )
        job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.IMAGE,
            version=version,
            status=status,
            prompt=cut.image_prompt,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            next_run_at=next_run_at,
            external_task_id=external_task_id,
            attempt_deadline_at=attempt_deadline_at,
            mock_scenario=scenario.value if scenario is not None else None,
        )
        # Claim order is (created_at, id); tests that care about which job is reached first
        # set it explicitly because SQLite's default timestamp only has second resolution.
        if created_at is not None:
            job.created_at = created_at
        session.add(job)
        await session.flush()
        return job


async def add_source_image(
    factory: async_sessionmaker[AsyncSession], cut: Cut, *, selected: bool = True
) -> CutImage:
    async with factory.begin() as session:
        version = cast(
            int,
            await session.scalar(
                select(func.coalesce(func.max(GenerationJob.version), 0) + 1).where(
                    GenerationJob.cut_id == cut.id,
                    GenerationJob.kind == GenerationKind.IMAGE,
                )
            ),
        )
        job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.IMAGE,
            version=version,
            status=JobStatus.SUCCEEDED,
            prompt=cut.image_prompt,
            attempt_count=1,
            max_attempts=3,
        )
        session.add(job)
        await session.flush()
        image = CutImage(
            cut_id=cut.id,
            generation_job_id=job.id,
            url="https://cdn.example/source.png",
            input_prompt=cut.image_prompt,
        )
        session.add(image)
        await session.flush()
        if selected:
            stored_cut = await session.get(Cut, cut.id)
            assert stored_cut is not None
            stored_cut.selected_image_id = image.id
        return image


async def add_video_job(
    factory: async_sessionmaker[AsyncSession],
    cut: Cut,
    source: CutImage,
    *,
    status: JobStatus = JobStatus.QUEUED,
    attempt_count: int = 0,
    external_task_id: str | None = None,
    next_run_at: datetime | None = None,
    attempt_deadline_at: datetime | None = None,
) -> GenerationJob:
    async with factory.begin() as session:
        version = cast(
            int,
            await session.scalar(
                select(func.coalesce(func.max(GenerationJob.version), 0) + 1).where(
                    GenerationJob.cut_id == cut.id,
                    GenerationJob.kind == GenerationKind.VIDEO,
                )
            ),
        )
        job = GenerationJob(
            cut_id=cut.id,
            kind=GenerationKind.VIDEO,
            version=version,
            status=status,
            prompt=cut.video_prompt,
            source_image_id=source.id,
            attempt_count=attempt_count,
            max_attempts=3,
            external_task_id=external_task_id,
            next_run_at=next_run_at,
            attempt_deadline_at=attempt_deadline_at,
        )
        session.add(job)
        await session.flush()
        return job


def make_worker(
    factory: async_sessionmaker[AsyncSession],
    provider: StubProvider | MockGenerationProvider,
    clock: FakeClock,
    *,
    concurrency: int = 1,
    attempt_timeout_sec: float = 10,
) -> GenerationWorker:
    return GenerationWorker(
        session_factory=factory,
        provider=provider,
        clock=clock,
        retry_base_delay_sec=1,
        provider_poll_interval_sec=1,
        generation_attempt_timeout_sec=attempt_timeout_sec,
        concurrency=concurrency,
    )


async def add_scene_cuts(
    factory: async_sessionmaker[AsyncSession], *, orders: tuple[int, ...]
) -> list[Cut]:
    """Cuts that share one Scene, so the anchor gate applies between them."""
    async with factory.begin() as session:
        scene = Scene(user_prompt="prompt", title="title", scenario="scenario")
        session.add(scene)
        await session.flush()
        cuts = [
            Cut(
                scene_id=scene.id,
                order=order,
                image_prompt=f"image prompt {order}",
                video_prompt=f"video prompt {order}",
                duration_sec=5,
            )
            for order in orders
        ]
        session.add_all(cuts)
        await session.flush()
        return cuts


async def load_job(factory: async_sessionmaker[AsyncSession], job: GenerationJob) -> GenerationJob:
    async with factory() as session:
        stored = await session.get(GenerationJob, job.id)
        assert stored is not None
        return stored


async def load_cut(factory: async_sessionmaker[AsyncSession], cut: Cut) -> Cut:
    async with factory() as session:
        stored = await session.get(Cut, cut.id)
        assert stored is not None
        return stored


async def in_flight_count(factory: async_sessionmaker[AsyncSession]) -> int:
    """Jobs a provider is currently working on, which is what the concurrency cap bounds."""
    async with factory() as session:
        return cast(
            int,
            await session.scalar(
                select(func.count())
                .select_from(GenerationJob)
                .where(
                    GenerationJob.status.in_((JobStatus.SUBMITTING, JobStatus.PROCESSING)),
                )
            ),
        )


async def succeed_one_image_job(
    factory: async_sessionmaker[AsyncSession], clock: FakeClock
) -> tuple[Cut, GenerationJob, GenerationWorker]:
    """Drive one image job to SUCCEEDED so a late delivery has a real terminal job to land on."""
    cut = await add_cut(factory)
    job = await add_image_job(
        factory,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[TaskResult(state="SUCCEEDED", result_url="https://cdn.example/done.png")]
    )
    worker = make_worker(factory, provider, clock)
    await worker.run_once()
    return cut, job, worker


async def test_worker_handles_one_due_job_per_run(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    first_cut = await add_cut(worker_db, order=1)
    second_cut = await add_cut(worker_db, order=2)
    first = await add_image_job(worker_db, first_cut)
    second = await add_image_job(worker_db, second_cut)
    provider = StubProvider(submit_effects=[Submission("first"), Submission("second")])
    clock = FakeClock()

    worked = await make_worker(worker_db, provider, clock).run_once()

    statuses = {(await load_job(worker_db, job)).status for job in (first, second)}
    assert worked is True
    assert statuses == {JobStatus.PROCESSING, JobStatus.QUEUED}


async def test_worker_submits_up_to_the_concurrency_limit_per_run(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cuts = [await add_cut(worker_db, order=order) for order in range(1, 7)]
    jobs = [await add_image_job(worker_db, cut) for cut in cuts]
    provider = StubProvider(submit_effects=[Submission(f"task-{n}") for n in range(6)])

    await make_worker(worker_db, provider, FakeClock(), concurrency=3).run_once()

    statuses = [(await load_job(worker_db, job)).status for job in jobs]
    assert statuses.count(JobStatus.PROCESSING) == 3
    assert statuses.count(JobStatus.QUEUED) == 3
    assert len(provider.submit_requests) == 3


async def test_worker_drains_a_batch_as_in_flight_work_completes(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    """The rest of a batch is claimed only once the first slice frees its concurrency budget.

    This used to drain on the very next tick regardless, because in-flight jobs were never
    counted again after the tick that claimed them.
    """
    cuts = [await add_cut(worker_db, order=order) for order in range(1, 7)]
    jobs = [await add_image_job(worker_db, cut) for cut in cuts]
    provider = StubProvider(
        submit_effects=[Submission(f"task-{n}") for n in range(6)],
        poll_effects=[
            TaskResult(state="SUCCEEDED", result_url=f"https://cdn.example/{n}.png")
            for n in range(3)
        ],
    )
    clock = FakeClock()
    worker = make_worker(worker_db, provider, clock, concurrency=3)

    await worker.run_once()
    blocked = [(await load_job(worker_db, job)).status for job in jobs]
    await worker.run_once()
    still_blocked = [(await load_job(worker_db, job)).status for job in jobs]

    clock.advance(seconds=1)
    await worker.run_once()
    await worker.run_once()

    statuses = [(await load_job(worker_db, job)).status for job in jobs]
    assert blocked.count(JobStatus.PROCESSING) == 3
    assert still_blocked == blocked
    assert statuses.count(JobStatus.SUCCEEDED) == 3
    assert statuses.count(JobStatus.PROCESSING) == 3


async def test_in_flight_provider_work_never_exceeds_the_concurrency_limit(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    """GENERATION_CONCURRENCY bounds in-flight provider work, not claims per tick.

    A six-cut video batch against a provider that stays PENDING used to add one more
    simultaneous task on every tick until all six were running at once, because the claim
    counted only what it was about to claim and never what was already in flight.
    """
    cuts = await add_scene_cuts(worker_db, orders=(1, 2, 3, 4, 5, 6))
    sources = [await add_source_image(worker_db, cut) for cut in cuts]
    jobs = [
        await add_video_job(worker_db, cut, source)
        for cut, source in zip(cuts, sources, strict=True)
    ]
    provider = StubProvider(
        submit_effects=[Submission(f"task-{n}") for n in range(6)],
        poll_effects=[TaskResult(state="PENDING")] * 60,
    )
    clock = FakeClock()
    worker = make_worker(worker_db, provider, clock, concurrency=2, attempt_timeout_sec=1000)

    peak = 0
    for _ in range(12):
        await worker.run_once()
        peak = max(peak, await in_flight_count(worker_db))
        clock.advance(seconds=1)

    assert peak == 2
    assert len(provider.submit_requests) == 2
    assert [(await load_job(worker_db, job)).status for job in jobs].count(
        JobStatus.QUEUED
    ) == 4


async def test_non_anchor_image_waits_while_the_anchor_job_is_active(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    first, second = await add_scene_cuts(worker_db, orders=(1, 2))
    anchor_job = await add_image_job(worker_db, first)
    follower_job = await add_image_job(worker_db, second)
    provider = StubProvider(submit_effects=[Submission("anchor")])

    await make_worker(worker_db, provider, FakeClock(), concurrency=3).run_once()

    assert (await load_job(worker_db, anchor_job)).status == JobStatus.PROCESSING
    assert (await load_job(worker_db, follower_job)).status == JobStatus.QUEUED
    assert len(provider.submit_requests) == 1


async def test_non_anchor_image_waits_when_the_anchor_cut_was_never_started(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    """Generating a single cut outside a batch must not slip past the gate.

    The gate used to open whenever no anchor job was *active*, which included the case where
    Cut 1 had simply never been requested. Cut 3's button then produced an unreferenced image
    and character consistency broke with nothing to tell the user why.
    """
    _, second = await add_scene_cuts(worker_db, orders=(1, 2))
    follower_job = await add_image_job(worker_db, second)
    provider = StubProvider(submit_effects=[])

    worked = await make_worker(worker_db, provider, FakeClock(), concurrency=3).run_once()

    assert worked is False
    assert (await load_job(worker_db, follower_job)).status == JobStatus.QUEUED
    assert provider.submit_requests == []


async def test_claiming_looks_past_a_full_page_of_gated_jobs(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    """A runnable job must not be hidden behind more gated jobs than one page holds."""
    base = datetime(2026, 8, 14, 11, 0, tzinfo=UTC)
    for scene_index in range(5):
        cuts = await add_scene_cuts(worker_db, orders=(1, 2, 3, 4, 5, 6))
        for offset, gated_cut in enumerate(cuts[1:]):
            await add_image_job(
                worker_db,
                gated_cut,
                created_at=base + timedelta(seconds=scene_index * 10 + offset),
            )
    runnable_cut = await add_cut(worker_db, order=1)
    runnable = await add_image_job(worker_db, runnable_cut, created_at=base + timedelta(hours=1))
    provider = StubProvider(submit_effects=[Submission("runnable")])

    await make_worker(worker_db, provider, FakeClock(), concurrency=1).run_once()

    assert (await load_job(worker_db, runnable)).status == JobStatus.PROCESSING
    assert [request.job_id for request in provider.submit_requests] == [runnable.id]


async def test_submission_callback_runs_only_once_the_job_is_processing(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    """Providers that push a callback must not fire it before the job can accept one.

    Mock's webhook used to be scheduled inside `submit()`, so a zero delay could deliver the
    callback while the job was still SUBMITTING. The webhook found nothing to apply and the
    job then sat idle until its attempt deadline expired.
    """
    cut = await add_cut(worker_db)
    job = await add_image_job(worker_db, cut)
    observed: list[JobStatus] = []

    async def on_processing() -> None:
        observed.append((await load_job(worker_db, job)).status)

    provider = StubProvider(submit_effects=[Submission("task-1", on_processing=on_processing)])

    await make_worker(worker_db, provider, FakeClock()).run_once()

    assert observed == [JobStatus.PROCESSING]


async def test_non_anchor_image_proceeds_unreferenced_once_the_anchor_failed_permanently(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    first, second = await add_scene_cuts(worker_db, orders=(1, 2))
    await add_image_job(worker_db, first, status=JobStatus.FAILED)
    follower_job = await add_image_job(worker_db, second)
    provider = StubProvider(submit_effects=[Submission("follower")])

    await make_worker(worker_db, provider, FakeClock(), concurrency=3).run_once()

    stored = await load_job(worker_db, follower_job)
    assert stored.status == JobStatus.PROCESSING
    assert stored.reference_image_id is None
    assert provider.submit_requests[0].reference_image_url is None


async def test_non_anchor_image_submits_with_the_anchor_reference(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    first, second = await add_scene_cuts(worker_db, orders=(1, 2))
    anchor_image = await add_source_image(worker_db, first, selected=True)
    follower_job = await add_image_job(worker_db, second)
    provider = StubProvider(submit_effects=[Submission("follower")])

    await make_worker(worker_db, provider, FakeClock(), concurrency=3).run_once()

    stored = await load_job(worker_db, follower_job)
    assert stored.reference_image_id == anchor_image.id
    assert provider.submit_requests[0].reference_image_url == anchor_image.url


async def test_anchor_cut_itself_is_never_gated_or_referenced(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    first, _ = await add_scene_cuts(worker_db, orders=(1, 2))
    anchor_job = await add_image_job(worker_db, first)
    provider = StubProvider(submit_effects=[Submission("anchor")])

    await make_worker(worker_db, provider, FakeClock(), concurrency=3).run_once()

    assert (await load_job(worker_db, anchor_job)).status == JobStatus.PROCESSING
    assert provider.submit_requests[0].reference_image_url is None
    assert len(provider.submit_requests) == 1


async def test_worker_commits_submitting_and_attempt_before_external_submit(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    job = await add_image_job(worker_db, cut)
    observed: list[tuple[JobStatus, int]] = []

    async def observe(request: GenerationRequest) -> None:
        stored = await load_job(worker_db, job)
        observed.append((stored.status, stored.attempt_count))
        assert request.attempt_count == stored.attempt_count

    provider = StubProvider(submit_observer=observe)

    await make_worker(worker_db, provider, FakeClock()).run_once()

    assert observed == [(JobStatus.SUBMITTING, 1)]


async def test_retryable_submission_uses_persisted_attempt_and_retry_after(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    job = await add_image_job(worker_db, cut)
    provider = StubProvider(
        submit_effects=[
            RetryableProviderError("PROVIDER_BUSY", retry_after_seconds=3),
            RetryableProviderError("PROVIDER_BUSY"),
            Submission("accepted"),
        ]
    )
    clock = FakeClock()
    worker = make_worker(worker_db, provider, clock)

    await worker.run_once()
    first = await load_job(worker_db, job)
    assert (first.status, first.attempt_count) == (JobStatus.RETRY_WAIT, 1)
    assert _aware(first.next_run_at) == clock.now() + timedelta(seconds=3)

    clock.advance(seconds=3)
    await worker.run_once()
    second = await load_job(worker_db, job)
    assert (second.status, second.attempt_count) == (JobStatus.RETRY_WAIT, 2)
    assert _aware(second.next_run_at) == clock.now() + timedelta(seconds=2)

    clock.advance(seconds=2)
    await worker.run_once()
    third = await load_job(worker_db, job)
    assert (third.status, third.attempt_count) == (JobStatus.PROCESSING, 3)
    assert [request.attempt_count for request in provider.submit_requests] == [1, 2, 3]


async def test_retryable_submission_exhaustion_is_terminal(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    job = await add_image_job(worker_db, cut, attempt_count=1, max_attempts=2)
    provider = StubProvider(submit_effects=[RetryableProviderError("PROVIDER_BUSY")])

    await make_worker(worker_db, provider, FakeClock()).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.FAILED
    assert stored.attempt_count == 2
    assert stored.last_error_code == "PROVIDER_BUSY"
    assert stored.next_run_at is None


async def test_submission_uncertain_is_not_retried(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    job = await add_image_job(worker_db, cut)
    provider = StubProvider(submit_effects=[SubmissionUncertainError("SUBMISSION_UNCERTAIN")])
    clock = FakeClock()
    worker = make_worker(worker_db, provider, clock)

    await worker.run_once()
    clock.advance(seconds=999)
    await worker.run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.FAILED
    assert stored.last_error_code == "SUBMISSION_UNCERTAIN"
    assert stored.last_error_message == "Generation submission outcome is uncertain"
    assert len(provider.submit_requests) == 1


async def test_transient_poll_error_does_not_consume_attempt_or_resubmit(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[RetryableProviderError("KIE_POLL_RETRYABLE", retry_after_seconds=4)]
    )

    await make_worker(worker_db, provider, clock).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.PROCESSING
    assert stored.attempt_count == 1
    assert _aware(stored.next_run_at) == clock.now() + timedelta(seconds=4)
    assert provider.submit_requests == []
    assert provider.poll_requests == ["task-1"]


async def test_attempt_deadline_is_handled_before_poll_and_retries_with_budget(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now(),
    )
    provider = StubProvider()

    await make_worker(worker_db, provider, clock).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.RETRY_WAIT
    assert stored.external_task_id is None
    assert stored.last_error_code == "ATTEMPT_TIMEOUT"
    assert _aware(stored.next_run_at) == clock.now() + timedelta(seconds=1)
    assert provider.poll_requests == []


async def test_attempt_deadline_exhaustion_is_terminal(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=3,
        max_attempts=3,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now(),
    )
    provider = StubProvider()

    await make_worker(worker_db, provider, clock).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.FAILED
    assert stored.last_error_code == "ATTEMPT_TIMEOUT"
    assert stored.completed_at is not None
    assert provider.poll_requests == []


async def test_stale_submitting_recovery_has_precedence_over_submission(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    stale_cut = await add_cut(worker_db, order=1)
    queued_cut = await add_cut(worker_db, order=2)
    stale = await add_image_job(worker_db, stale_cut, status=JobStatus.SUBMITTING, attempt_count=1)
    queued = await add_image_job(worker_db, queued_cut)
    provider = StubProvider()

    await make_worker(worker_db, provider, FakeClock()).run_once()

    recovered = await load_job(worker_db, stale)
    untouched = await load_job(worker_db, queued)
    assert recovered.status == JobStatus.FAILED
    assert recovered.last_error_code == "SUBMISSION_UNCERTAIN"
    assert untouched.status == JobStatus.QUEUED
    assert provider.submit_requests == []


async def test_pending_poll_schedules_next_poll_without_attempt_change(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=2,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(poll_effects=[TaskResult(state="PENDING")])

    await make_worker(worker_db, provider, clock).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.PROCESSING
    assert stored.attempt_count == 2
    assert _aware(stored.next_run_at) == clock.now() + timedelta(seconds=1)


async def test_retryable_task_failure_returns_to_retry_wait_without_consuming_attempt(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[
            TaskResult(
                state="FAILED",
                error_code="KIE_TASK_FAILED",
                error_message="Generation provider failed",
                retryable=True,
            )
        ]
    )

    await make_worker(worker_db, provider, clock).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.RETRY_WAIT
    assert stored.attempt_count == 1
    assert stored.external_task_id is None
    assert _aware(stored.next_run_at) == clock.now() + timedelta(seconds=1)


async def test_permanent_task_failure_is_terminal_with_safe_fields(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[
            TaskResult(
                state="FAILED",
                error_code="KIE_TASK_FAILED",
                error_message="Generation provider failed",
            )
        ]
    )

    await make_worker(worker_db, provider, clock).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.FAILED
    assert stored.last_error_code == "KIE_TASK_FAILED"
    assert stored.last_error_message == "Generation provider failed"
    assert stored.completed_at is not None


async def test_late_permanent_failure_leaves_a_succeeded_job_untouched(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    """A delivery that arrives after the job already succeeded must change nothing.

    Both failure branches used to load the job and write without re-checking its status, so a
    webhook landing seconds behind the poll that completed the job rewrote SUCCEEDED to FAILED
    and the finished artifact was reported to the user as a failure.
    """
    clock = FakeClock()
    cut, job, worker = await succeed_one_image_job(worker_db, clock)
    before = await load_job(worker_db, job)
    selected_before = (await load_cut(worker_db, cut)).selected_image_id
    assert before.status == JobStatus.SUCCEEDED

    clock.advance(seconds=60)
    await worker.apply_external_result(
        job.id,
        TaskResult(
            state="FAILED",
            error_code="KIE_TASK_FAILED",
            error_message="Generation provider failed",
        ),
    )

    after = await load_job(worker_db, job)
    assert after.status == JobStatus.SUCCEEDED
    assert after.last_error_code is None
    assert _aware(after.completed_at) == _aware(before.completed_at)
    assert (await load_cut(worker_db, cut)).selected_image_id == selected_before


async def test_late_retryable_failure_does_not_resurrect_a_succeeded_job(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    """A retryable late delivery is the worse half: RETRY_WAIT is claimable again.

    The job would be picked up on the next tick, submitted a second time, and produce a
    duplicate artifact for a cut that was already finished.
    """
    clock = FakeClock()
    _, job, worker = await succeed_one_image_job(worker_db, clock)

    clock.advance(seconds=60)
    await worker.apply_external_result(
        job.id,
        TaskResult(
            state="FAILED",
            error_code="KIE_TASK_FAILED",
            error_message="Generation provider failed",
            retryable=True,
        ),
    )

    after = await load_job(worker_db, job)
    assert after.status == JobStatus.SUCCEEDED
    assert after.status not in CLAIMABLE_JOB_STATUSES
    assert after.next_run_at is None


async def test_late_result_leaves_a_failed_job_terminal(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    cut = await add_cut(worker_db)
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.FAILED,
        attempt_count=1,
        external_task_id="task-1",
    )
    worker = make_worker(worker_db, StubProvider(), clock)

    await worker.apply_external_result(
        job.id,
        TaskResult(
            state="FAILED",
            error_code="KIE_TASK_FAILED",
            error_message="Generation provider failed",
            retryable=True,
        ),
    )

    after = await load_job(worker_db, job)
    assert after.status == JobStatus.FAILED
    assert after.last_error_code is None
    assert after.next_run_at is None


async def test_kie_contract_error_terminally_fails_processing_job(
    worker_db: async_sessionmaker[AsyncSession], respx_mock
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-1",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    respx_mock.get("https://api.kie.ai/api/v1/jobs/recordInfo").mock(
        return_value=httpx.Response(200, json={"code": 200, "data": {"state": []}})
    )
    provider = KieGenerationProvider(api_key="test-kie-key")

    try:
        await make_worker(worker_db, provider, clock).run_once()
    finally:
        await provider.aclose()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.FAILED
    assert stored.attempt_count == 1
    assert stored.last_error_code == "KIE_RESPONSE_INVALID"
    assert stored.last_error_message == "Generation provider failed"


async def test_image_success_creates_artifact_and_selects_only_when_null(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    existing = await add_source_image(worker_db, cut, selected=True)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-new",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[TaskResult(state="SUCCEEDED", result_url="https://cdn.example/new.png")]
    )

    await make_worker(worker_db, provider, clock).run_once()

    async with worker_db() as session:
        stored_job = await session.get(GenerationJob, job.id)
        artifact = await session.scalar(
            select(CutImage).where(CutImage.generation_job_id == job.id)
        )
        stored_cut = await session.get(Cut, cut.id)
    assert stored_job is not None and stored_job.status == JobStatus.SUCCEEDED
    assert stored_job.completed_at is not None
    assert artifact is not None and artifact.url == "https://cdn.example/new.png"
    assert stored_cut is not None and stored_cut.selected_image_id == existing.id


async def test_first_image_success_auto_selects_new_artifact(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    job = await add_image_job(
        worker_db,
        cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="task-new",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[TaskResult(state="SUCCEEDED", result_url="https://cdn.example/new.png")]
    )

    await make_worker(worker_db, provider, clock).run_once()

    async with worker_db() as session:
        artifact = await session.scalar(
            select(CutImage).where(CutImage.generation_job_id == job.id)
        )
        stored_cut = await session.get(Cut, cut.id)
    assert artifact is not None
    assert stored_cut is not None and stored_cut.selected_image_id == artifact.id


async def test_video_success_auto_selects_only_for_current_source(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    source = await add_source_image(worker_db, cut, selected=True)
    clock = FakeClock()
    job = await add_video_job(
        worker_db,
        cut,
        source,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="video-task",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[TaskResult(state="SUCCEEDED", result_url="https://cdn.example/video.mp4")]
    )

    await make_worker(worker_db, provider, clock).run_once()

    async with worker_db() as session:
        artifact = await session.scalar(
            select(CutVideo).where(CutVideo.generation_job_id == job.id)
        )
        stored_cut = await session.get(Cut, cut.id)
    assert artifact is not None and artifact.cut_image_id == source.id
    assert stored_cut is not None and stored_cut.selected_video_id == artifact.id


async def test_video_success_does_not_select_after_source_selection_changes(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    source = await add_source_image(worker_db, cut, selected=True)
    replacement = await add_source_image(worker_db, cut, selected=True)
    clock = FakeClock()
    await add_video_job(
        worker_db,
        cut,
        source,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="video-task",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(
        poll_effects=[TaskResult(state="SUCCEEDED", result_url="https://cdn.example/video.mp4")]
    )

    await make_worker(worker_db, provider, clock).run_once()

    async with worker_db() as session:
        stored_cut = await session.get(Cut, cut.id)
    assert stored_cut is not None and stored_cut.selected_image_id == replacement.id
    assert stored_cut.selected_video_id is None


async def test_video_submission_uses_persisted_source_image_url(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    source = await add_source_image(worker_db, cut)
    job = await add_video_job(worker_db, cut, source)
    provider = StubProvider()

    await make_worker(worker_db, provider, FakeClock()).run_once()

    stored = await load_job(worker_db, job)
    assert stored.status == JobStatus.PROCESSING
    assert provider.submit_requests[0].source_image_url == "https://cdn.example/source.png"
    assert provider.submit_requests[0].kind is GenerationKind.VIDEO


async def test_missing_video_source_transition_is_the_only_run_once_operation(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    malformed_cut = await add_cut(worker_db, order=1)
    polling_cut = await add_cut(worker_db, order=2)
    async with worker_db.begin() as session:
        malformed = GenerationJob(
            cut_id=malformed_cut.id,
            kind=GenerationKind.VIDEO,
            version=1,
            status=JobStatus.QUEUED,
            prompt=malformed_cut.video_prompt,
            source_image_id=None,
            attempt_count=0,
            max_attempts=3,
        )
        session.add(malformed)
        await session.flush()
    clock = FakeClock()
    polling = await add_image_job(
        worker_db,
        polling_cut,
        status=JobStatus.PROCESSING,
        attempt_count=1,
        external_task_id="poll-task",
        next_run_at=clock.now(),
        attempt_deadline_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider(poll_effects=[TaskResult(state="PENDING")])

    # concurrency 2, not 1: the PROCESSING job holds one slot of the in-flight budget, and with
    # no slot left the claim never walks the queue and never reaches the malformed job.
    await make_worker(worker_db, provider, clock, concurrency=2).run_once()

    malformed_stored = await load_job(worker_db, malformed)
    polling_stored = await load_job(worker_db, polling)
    assert malformed_stored.status == JobStatus.FAILED
    assert malformed_stored.last_error_code == "SOURCE_IMAGE_MISSING"
    assert _aware(polling_stored.next_run_at) == _aware(polling.next_run_at)
    assert provider.poll_requests == []


async def test_mock_fail_twice_then_succeed_uses_request_attempt_and_static_media(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    job = await add_image_job(worker_db, cut, scenario=MockScenario.FAIL_TWICE_THEN_SUCCEED)
    clock = FakeClock()
    await make_worker(worker_db, MockGenerationProvider(), clock).run_once()
    clock.advance(seconds=1)
    await make_worker(worker_db, MockGenerationProvider(), clock).run_once()
    clock.advance(seconds=2)
    await make_worker(worker_db, MockGenerationProvider(), clock).run_once()
    submitted = await load_job(worker_db, job)
    assert (submitted.status, submitted.attempt_count) == (JobStatus.PROCESSING, 3)

    clock.advance(seconds=1)
    await make_worker(worker_db, MockGenerationProvider(), clock).run_once()

    async with worker_db() as session:
        completed = await session.get(GenerationJob, job.id)
        artifact = await session.scalar(
            select(CutImage).where(CutImage.generation_job_id == job.id)
        )
    assert completed is not None and completed.status == JobStatus.SUCCEEDED
    assert artifact is not None and artifact.url == "/media/mock/cut-image.png"


async def test_no_due_work_returns_false_without_provider_call(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    clock = FakeClock()
    await add_image_job(
        worker_db,
        cut,
        status=JobStatus.RETRY_WAIT,
        next_run_at=clock.now() + timedelta(seconds=10),
    )
    provider = StubProvider()

    worked = await make_worker(worker_db, provider, clock).run_once()

    assert worked is False
    assert provider.submit_requests == []
    assert provider.poll_requests == []


async def test_worker_start_is_idempotent_and_stop_is_clean(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    worker = make_worker(worker_db, StubProvider(), FakeClock())

    await worker.start()
    first_task = worker.task
    await worker.start()

    assert first_task is not None
    assert worker.task is first_task
    await worker.stop()
    assert worker.task is None
    assert first_task.done()


async def test_worker_background_loop_survives_unexpected_iteration_error(
    worker_db: async_sessionmaker[AsyncSession],
) -> None:
    cut = await add_cut(worker_db)
    await add_image_job(worker_db, cut)
    submit_started = asyncio.Event()

    async def observe(_request: GenerationRequest) -> None:
        submit_started.set()

    provider = StubProvider(
        submit_effects=[RuntimeError("provider body must not be logged")],
        submit_observer=observe,
    )
    worker = make_worker(worker_db, provider, FakeClock())

    await worker.start()
    await asyncio.wait_for(submit_started.wait(), timeout=1)
    await asyncio.sleep(0)

    task = worker.task
    assert provider.submit_requests
    assert task is not None and not task.done()
    await worker.stop()


class RecordingWorker:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1

    async def stop(self) -> None:
        self.stops += 1


@pytest.mark.parametrize("generation_mode", ["mock", "live"])
async def test_app_lifespan_starts_and_stops_exactly_one_injected_worker(
    settings_factory: Callable[..., Settings], generation_mode: str
) -> None:
    recording = RecordingWorker()
    settings = settings_factory(
        generation_mode=generation_mode,
        openai_api_key="test-openai-key" if generation_mode == "live" else "",
        kie_api_key="test-kie-key" if generation_mode == "live" else "",
    )
    application = create_app(settings, generation_worker=cast(GenerationWorker, recording))

    async with application.router.lifespan_context(application):
        assert application.state.generation_worker is recording
        assert recording.starts == 1
        assert recording.stops == 0

    assert recording.starts == 1
    assert recording.stops == 1


def test_attempt_timeout_default_outlasts_measured_video_latency(
    settings_factory: Callable[..., Settings],
) -> None:
    """kling-2.6 image-to-video measured ~3m30s-4m00s on this project's own live run.

    At the previous 120s default all six live videos hit the wall on attempt 1. The attempt was
    abandoned without cancelling the upstream task and attempt 2 succeeded, so every video was
    generated — and billed — twice. The abandon-and-resubmit mechanism is fine; the deadline was
    simply shorter than the provider's normal latency.
    """
    assert settings_factory().generation_attempt_timeout_sec == 300


def _aware(value: datetime | None) -> datetime:
    assert value is not None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
