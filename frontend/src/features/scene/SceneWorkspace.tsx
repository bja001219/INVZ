import { useQuery } from "@tanstack/react-query";

import { api, errorMessage } from "../../api/client";
import type { GenerationMode, Scene } from "../../api/types";
import { CutCard } from "../generations/CutCard";
import { SequencePlayer } from "../player/SequencePlayer";
import { BatchControls } from "./BatchControls";
import { CharacterPanel } from "./CharacterPanel";

const activeStatuses = new Set(["QUEUED", "SUBMITTING", "PROCESSING", "RETRY_WAIT"]);

export function hasActiveJobs(scene: Scene | undefined): boolean {
  return Boolean(
    scene?.cuts.some((cut) =>
      [...cut.imageJobs, ...cut.videoJobs].some((job) => activeStatuses.has(job.status)),
    ),
  );
}

interface SceneWorkspaceProps {
  sceneId: string;
  generationMode: GenerationMode;
}

export function SceneWorkspace({ sceneId, generationMode }: SceneWorkspaceProps) {
  const sceneQuery = useQuery({
    queryKey: ["scene", sceneId],
    queryFn: () => api.getScene(sceneId),
    refetchInterval: (query) => hasActiveJobs(query.state.data) ? 1000 : false,
  });

  if (sceneQuery.isPending) {
    return <p className="workspace-status" role="status">Restoring scene…</p>;
  }
  if (sceneQuery.isError) {
    return <p className="error-message workspace-status" role="alert">{errorMessage(sceneQuery.error)}</p>;
  }

  const scene = sceneQuery.data;
  const orderedCuts = [...scene.cuts].sort((left, right) => left.order - right.order);

  return (
    <section className="workspace" aria-labelledby="scene-title">
      <header className="scene-summary">
        <div>
          <p className="eyebrow">CURRENT SCENE</p>
          <h2 id="scene-title">{scene.title}</h2>
          <p>{scene.scenario}</p>
        </div>
        <dl className="scene-metrics">
          <div><dt>Cuts</dt><dd>6</dd></div>
          <div><dt>Nominal duration</dt><dd>30 sec</dd></div>
        </dl>
      </header>

      <CharacterPanel profiles={scene.characterProfiles} />

      <BatchControls cuts={orderedCuts} generationMode={generationMode} sceneId={sceneId} />

      <SequencePlayer cuts={orderedCuts} />

      <div className="cut-list">
        {orderedCuts.map((cut) => (
          <CutCard
            cut={cut}
            generationMode={generationMode}
            key={cut.id}
            sceneId={sceneId}
          />
        ))}
      </div>
    </section>
  );
}
