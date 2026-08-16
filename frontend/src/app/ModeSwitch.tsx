import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, errorMessage } from "../api/client";
import type { Config, GenerationMode } from "../api/types";

const MODES: GenerationMode[] = ["MOCK", "LIVE"];

interface ModeSwitchProps {
  config: Config;
}

/**
 * Live is offered only when the backend says it is configured. The frontend never learns
 * anything about the keys themselves beyond that single boolean.
 */
export function ModeSwitch({ config }: ModeSwitchProps) {
  const queryClient = useQueryClient();
  const switchMode = useMutation({
    mutationFn: (mode: GenerationMode) => api.setGenerationMode(mode),
    onSuccess: (next) => queryClient.setQueryData(["config"], next),
  });

  return (
    <div className="mode-switch">
      <span className="mode-switch__label" id="mode-switch-label">
        Generation mode
      </span>
      <div className="mode-switch__options" role="group" aria-labelledby="mode-switch-label">
        {MODES.map((mode) => {
          const selected = config.generationMode === mode;
          const unavailable = mode === "LIVE" && !config.liveAvailable;
          return (
            <button
              aria-pressed={selected}
              className={`mode-switch__option ${selected ? "is-selected" : ""}`}
              disabled={selected || unavailable || switchMode.isPending}
              key={mode}
              onClick={() => switchMode.mutate(mode)}
              type="button"
            >
              {mode === "MOCK" ? "Mock" : "Live"}
            </button>
          );
        })}
      </div>
      {!config.liveAvailable && (
        <span className="mode-switch__hint">Live needs backend API keys</span>
      )}
      {switchMode.isError && (
        <span className="error-message" role="alert">{errorMessage(switchMode.error)}</span>
      )}
    </div>
  );
}
