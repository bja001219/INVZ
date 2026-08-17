import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, errorMessage } from "../../api/client";
import type {
  Cut,
  GenerationBatch,
  GenerationJob,
  GenerationKind,
  GenerationMode,
  MockScenario,
} from "../../api/types";

const ACTIVE_STATUSES = new Set(["QUEUED", "SUBMITTING", "PROCESSING", "RETRY_WAIT"]);

interface BatchProgress {
  done: number;
  failed: number;
  active: number;
}

/**
 * Progress is derived from the jobs rather than read from a batch record, so the numbers can
 * never disagree with the state machine that owns them.
 */
export function batchProgress(cuts: Cut[], kind: GenerationKind): BatchProgress {
  const latest = cuts
    .map((cut) => newestJob(kind === "IMAGE" ? cut.imageJobs : cut.videoJobs))
    .filter((job): job is GenerationJob => job !== null);

  return {
    done: latest.filter((job) => job.status === "SUCCEEDED").length,
    failed: latest.filter((job) => job.status === "FAILED").length,
    active: latest.filter((job) => ACTIVE_STATUSES.has(job.status)).length,
  };
}

function newestJob(jobs: GenerationJob[]): GenerationJob | null {
  if (!jobs.length) return null;
  return [...jobs].sort((left, right) => right.version - left.version)[0];
}

function summary(label: string, cuts: Cut[], kind: GenerationKind): string {
  const { done, failed, active } = batchProgress(cuts, kind);
  const parts = [`${label} ${done}/${cuts.length} done`];
  if (active) parts.push(`${active} running`);
  if (failed) parts.push(`${failed} failed`);
  return parts.join(" · ");
}

/**
 * Every AppError code `_create_batch` can catch from job creation. An unmapped code is shown
 * verbatim, because a skip the UI cannot name is still a cut that did not start.
 */
const skipReasons = new Map<string, string>([
  ["GENERATION_ALREADY_ACTIVE", "a generation is already running"],
  ["SELECTED_IMAGE_REQUIRED", "no image is selected"],
  ["ARTIFACT_MODE_MISMATCH", "the selected image came from the other mode"],
  ["CUT_NOT_FOUND", "the cut no longer exists"],
  ["REQUEST_VALIDATION_FAILED", "the request was rejected"],
]);

function SkippedCuts({ batch, cuts, label }: {
  batch: GenerationBatch | undefined;
  cuts: Cut[];
  label: string;
}) {
  if (!batch?.skipped.length) return null;

  const orderByCutId = new Map(cuts.map((cut) => [cut.id, cut.order]));
  const cutName = (cutId: string) => {
    const order = orderByCutId.get(cutId);
    return order === undefined ? "An unlisted cut" : `Cut ${order}`;
  };

  return (
    <div aria-label={`Skipped ${label} cuts`} className="batch-controls__row" role="status">
      <p>{`${batch.skipped.length} of ${batch.requestedCount} cuts were skipped`}</p>
      <ul className="selection-note">
        {batch.skipped.map((skip) => (
          <li key={skip.cutId}>
            {`${cutName(skip.cutId)} · ${skipReasons.get(skip.reason) ?? skip.reason}`}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface BatchControlsProps {
  sceneId: string;
  cuts: Cut[];
  generationMode: GenerationMode;
}

export function BatchControls({ sceneId, cuts, generationMode }: BatchControlsProps) {
  const queryClient = useQueryClient();
  const [mockScenario, setMockScenario] = useState<MockScenario>("SUCCESS");
  const scenarioForRequest = generationMode === "MOCK" ? mockScenario : undefined;

  const refreshScene = () =>
    queryClient.invalidateQueries({ queryKey: ["scene", sceneId], exact: true });

  const imageBatch = useMutation({
    mutationFn: () => api.createBatch(sceneId, "IMAGE", scenarioForRequest),
    onSuccess: refreshScene,
  });
  const videoBatch = useMutation({
    mutationFn: () => api.createBatch(sceneId, "VIDEO", scenarioForRequest),
    onSuccess: refreshScene,
  });

  const anyReadyForVideo = cuts.some((cut) => cut.selectedImageId);
  const error = imageBatch.error ?? videoBatch.error;

  return (
    <section className="batch-controls" aria-labelledby="batch-controls-title">
      <div className="batch-controls__heading">
        <p className="eyebrow">BATCH</p>
        <h3 id="batch-controls-title">Generate all six cuts</h3>
      </div>

      {generationMode === "MOCK" && (
        <label className="scenario-control">
          Mock scenario for batch
          <select
            onChange={(event) => setMockScenario(event.target.value as MockScenario)}
            value={mockScenario}
          >
            <option value="SUCCESS">Success</option>
            <option value="FAIL_TWICE_THEN_SUCCEED">Fail twice, then succeed</option>
            <option value="ALWAYS_FAIL">Always fail</option>
            <option value="SUCCEED_VIA_WEBHOOK">Succeed via webhook</option>
          </select>
        </label>
      )}

      <div className="batch-controls__row">
        <button
          className="button button--secondary"
          disabled={imageBatch.isPending}
          onClick={() => {
            if (videoBatch.isError) videoBatch.reset();
            imageBatch.mutate();
          }}
          type="button"
        >
          Generate all images
        </button>
        <p role="status">{summary("Images", cuts, "IMAGE")}</p>
      </div>
      <SkippedCuts batch={imageBatch.data} cuts={cuts} label="image" />

      <div className="batch-controls__row">
        <button
          className="button button--secondary"
          disabled={videoBatch.isPending || !anyReadyForVideo}
          onClick={() => {
            if (imageBatch.isError) imageBatch.reset();
            videoBatch.mutate();
          }}
          type="button"
        >
          Generate all videos
        </button>
        <p role="status">{summary("Videos", cuts, "VIDEO")}</p>
      </div>
      <SkippedCuts batch={videoBatch.data} cuts={cuts} label="video" />

      {!anyReadyForVideo && (
        <p className="selection-note">Generate images before batching videos.</p>
      )}
      {error && <p className="error-message" role="alert">{errorMessage(error)}</p>}
    </section>
  );
}
