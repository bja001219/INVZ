import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import { SceneWorkspace } from "../features/scene/SceneWorkspace";

function sceneIdFromUrl(): string | null {
  return new URL(window.location.href).searchParams.get("scene");
}

export function App() {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState("");
  const [sceneId, setSceneId] = useState<string | null>(() => sceneIdFromUrl());
  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: api.getConfig,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const createScene = useMutation({
    mutationFn: api.createScene,
    onSuccess: (scene) => {
      queryClient.setQueryData(["scene", scene.id], scene);
      const url = new URL(window.location.href);
      url.searchParams.set("scene", scene.id);
      window.history.pushState({}, "", url);
      setSceneId(scene.id);
    },
  });

  useEffect(() => {
    const restoreFromUrl = () => setSceneId(sceneIdFromUrl());
    window.addEventListener("popstate", restoreFromUrl);
    return () => window.removeEventListener("popstate", restoreFromUrl);
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedPrompt = prompt.trim();
    if (normalizedPrompt) createScene.mutate(normalizedPrompt);
  }

  const modeLabel = configQuery.data?.generationMode === "MOCK" ? "Mock mode" : "Live mode";

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="/" aria-label="Frameforge home">
          <span aria-hidden="true" className="brand-mark">F</span>
          <span>Frameforge</span>
        </a>
        {configQuery.isPending ? (
          <span className="mode-badge">Reading startup mode…</span>
        ) : configQuery.isError ? (
          <span className="mode-badge mode-badge--error">Mode unavailable</span>
        ) : (
          <span className={`mode-badge mode-badge--${configQuery.data.generationMode.toLowerCase()}`}>
            <span className="status-dot" aria-hidden="true" />
            {modeLabel}
          </span>
        )}
      </header>

      <main>
        <section className="hero" aria-labelledby="create-heading">
          <p className="eyebrow">PROMPT TO MOTION</p>
          <h1 id="create-heading">Build a six-shot story.</h1>
          <p className="hero-copy">
            Describe an idea. Frameforge shapes it into a 30-second scene with six
            image-to-video cuts you can generate, compare, and select.
          </p>
          <form className="prompt-form" onSubmit={submit}>
            <label htmlFor="animation-prompt">Animation prompt</label>
            <div className="prompt-row">
              <textarea
                id="animation-prompt"
                maxLength={2000}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="A moonlit voyage through a field of stars…"
                rows={3}
                value={prompt}
              />
              <button
                className="button button--primary"
                disabled={!prompt.trim() || createScene.isPending}
                type="submit"
              >
                {createScene.isPending ? "Creating scene…" : "Create scene"}
              </button>
            </div>
          </form>
          {createScene.isError && (
            <p className="error-message" role="alert">{errorMessage(createScene.error)}</p>
          )}
          {configQuery.isError && (
            <p className="error-message" role="alert">{errorMessage(configQuery.error)}</p>
          )}
        </section>

        {sceneId ? (
          configQuery.data && (
            <SceneWorkspace
              key={sceneId}
              generationMode={configQuery.data.generationMode}
              sceneId={sceneId}
            />
          )
        ) : (
          <section className="empty-state" aria-label="Empty workspace">
            <span className="empty-state__number">06</span>
            <div>
              <h2>Your storyboard starts here</h2>
              <p>Enter a prompt to create six ordered, five-second cuts.</p>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
