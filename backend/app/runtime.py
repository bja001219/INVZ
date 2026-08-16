"""Runtime generation mode and provider selection.

The mode lives in process memory because the app is a single process by design; a settings
table would add a write path and a cache-invalidation problem for no benefit here. Jobs carry
their own mode snapshot, so flipping the switch never reroutes work that is already in flight.
"""

from app.core.config import GenerationMode
from app.core.errors import AppError
from app.providers.contracts import GenerationProvider, SceneProvider


class RuntimeMode:
    def __init__(self, initial: GenerationMode, *, live_available: bool) -> None:
        self._current = initial
        self._live_available = live_available

    @property
    def current(self) -> GenerationMode:
        return self._current

    @property
    def name(self) -> str:
        """MOCK or LIVE, the spelling used by the API and by job snapshots."""
        return self._current.name

    @property
    def live_available(self) -> bool:
        return self._live_available

    def set(self, mode: GenerationMode) -> None:
        if mode is GenerationMode.LIVE and not self._live_available:
            raise AppError(
                status_code=409,
                code="LIVE_MODE_UNAVAILABLE",
                message="Live mode is not configured",
            )
        self._current = mode


class ProviderRegistry:
    """Resolves the provider pair for a mode. Callers above this never branch on mode."""

    def __init__(
        self,
        *,
        mock_scene: SceneProvider,
        mock_generation: GenerationProvider,
        live_scene: SceneProvider | None = None,
        live_generation: GenerationProvider | None = None,
    ) -> None:
        self._mock_scene = mock_scene
        self._mock_generation = mock_generation
        self._live_scene = live_scene
        self._live_generation = live_generation

    @property
    def live_available(self) -> bool:
        return self._live_scene is not None and self._live_generation is not None

    def scene_provider(self, mode: GenerationMode | str) -> SceneProvider:
        if _is_live(mode):
            if self._live_scene is None:
                raise _unavailable()
            return self._live_scene
        return self._mock_scene

    def generation_provider(self, mode: GenerationMode | str) -> GenerationProvider:
        if _is_live(mode):
            if self._live_generation is None:
                raise _unavailable()
            return self._live_generation
        return self._mock_generation


def _is_live(mode: GenerationMode | str) -> bool:
    if isinstance(mode, GenerationMode):
        return mode is GenerationMode.LIVE
    return mode.upper() == GenerationMode.LIVE.name


def _unavailable() -> AppError:
    return AppError(
        status_code=409,
        code="LIVE_MODE_UNAVAILABLE",
        message="Live mode is not configured",
    )
