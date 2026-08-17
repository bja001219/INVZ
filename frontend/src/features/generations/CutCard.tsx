import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, errorMessage, resolveMediaUrl } from "../../api/client";
import type {
  Cut,
  CutImage,
  CutVideo,
  GenerationJob,
  GenerationKind,
  GenerationMode,
  MockScenario,
  Scene,
} from "../../api/types";

const activeStatuses = new Set(["QUEUED", "SUBMITTING", "PROCESSING", "RETRY_WAIT"]);

interface CutCardProps {
  cut: Cut;
  sceneId: string;
  generationMode: GenerationMode;
}

function versionForArtifact(
  generationJobId: string,
  jobs: GenerationJob[],
): number {
  return jobs.find((job) => job.id === generationJobId)?.version ?? 0;
}

function jobStatus(job: GenerationJob): string {
  if (job.status === "FAILED") {
    return `Failed after ${job.attemptCount}/${job.maxAttempts} attempts`;
  }
  if (job.status === "RETRY_WAIT") {
    return `Retrying after ${job.attemptCount}/${job.maxAttempts} attempts`;
  }
  const label = job.status.toLowerCase().replace("_", " ");
  return `${label[0].toUpperCase()}${label.slice(1)} · ${job.attemptCount}/${job.maxAttempts} attempts`;
}

function imageVersionLabel(imageId: string | null, cut: Cut): string | null {
  if (!imageId) return null;
  const image = cut.images.find((candidate) => candidate.id === imageId);
  if (!image) return imageId;
  return `Image v${versionForArtifact(image.generationJobId, cut.imageJobs)}`;
}

function GenerationHistory({ cut, jobs, kind }: {
  cut: Cut;
  jobs: GenerationJob[];
  kind: GenerationKind;
}) {
  const sortedJobs = [...jobs].sort((left, right) => right.version - left.version);
  const title = kind === "IMAGE" ? "Image" : "Video";

  if (!sortedJobs.length) return <p className="muted">No {title.toLowerCase()} generations yet.</p>;

  return (
    <div className="history-list">
      {sortedJobs.map((job) => {
        const sourceImage = imageVersionLabel(job.sourceImageId, cut);
        const referenceImage = imageVersionLabel(job.referenceImageId, cut);
        return (
          <article
            aria-label={`${title} generation v${job.version}`}
            className={`job job--${job.status.toLowerCase()}`}
            key={job.id}
          >
            <div className="job__heading">
              <strong>{title} v{job.version}</strong>
              <span className={`mode-tag mode-tag--${job.generationMode.toLowerCase()}`}>
                {job.generationMode}
              </span>
              <span className="job__status">{jobStatus(job)}</span>
            </div>
            <p><span>Prompt</span>{job.prompt}</p>
            {sourceImage && <p><span>Source image</span>{sourceImage}</p>}
            {referenceImage && <p><span>Reference</span>{referenceImage}</p>}
            {job.waitingForAnchor && (
              <p className="job__waiting">
                Waiting for the Cut 1 image so this cut keeps the same characters.
              </p>
            )}
            {job.nextRunAt && (
              <p><span>Next retry</span><time dateTime={job.nextRunAt}>{new Date(job.nextRunAt).toLocaleString()}</time></p>
            )}
            {job.lastErrorMessage && <p className="job__error">{job.lastErrorMessage}</p>}
          </article>
        );
      })}
    </div>
  );
}

function sortedImages(cut: Cut): CutImage[] {
  return [...cut.images].sort(
    (left, right) =>
      versionForArtifact(right.generationJobId, cut.imageJobs) -
      versionForArtifact(left.generationJobId, cut.imageJobs),
  );
}

function sortedVideos(cut: Cut): CutVideo[] {
  return [...cut.videos].sort(
    (left, right) =>
      versionForArtifact(right.generationJobId, cut.videoJobs) -
      versionForArtifact(left.generationJobId, cut.videoJobs),
  );
}

export function CutCard({ cut, sceneId, generationMode }: CutCardProps) {
  const queryClient = useQueryClient();
  const [mockScenario, setMockScenario] = useState<MockScenario>("SUCCESS");
  const sceneKey = ["scene", sceneId] as const;

  function refreshScene() {
    return queryClient.invalidateQueries({ queryKey: sceneKey, exact: true });
  }

  function seedAndRefresh(scene: Scene) {
    queryClient.setQueryData(sceneKey, scene);
    return refreshScene();
  }

  const imageGeneration = useMutation({
    mutationFn: () => api.createGeneration(
      cut.id,
      "IMAGE",
      generationMode === "MOCK" ? mockScenario : undefined,
    ),
    onSuccess: refreshScene,
  });
  const videoGeneration = useMutation({
    mutationFn: () => api.createGeneration(
      cut.id,
      "VIDEO",
      generationMode === "MOCK" ? mockScenario : undefined,
    ),
    onSuccess: refreshScene,
  });
  const selectImage = useMutation({
    mutationFn: (imageId: string) => api.selectImage(cut.id, imageId),
    onSuccess: seedAndRefresh,
  });
  const selectVideo = useMutation({
    mutationFn: (videoId: string) => api.selectVideo(cut.id, videoId),
    onSuccess: seedAndRefresh,
  });

  function resetMutationErrors() {
    if (imageGeneration.isError) imageGeneration.reset();
    if (videoGeneration.isError) videoGeneration.reset();
    if (selectImage.isError) selectImage.reset();
    if (selectVideo.isError) selectVideo.reset();
  }

  const imageActive = cut.imageJobs.some((job) => activeStatuses.has(job.status));
  const videoActive = cut.videoJobs.some((job) => activeStatuses.has(job.status));
  const imageLabel = cut.imageJobs.length ? "Regenerate image" : "Generate image";
  const videoLabel = cut.videoJobs.length ? "Regenerate video" : "Generate video";
  const mutationError = imageGeneration.error ?? videoGeneration.error ?? selectImage.error ?? selectVideo.error;

  return (
    <section aria-labelledby={`cut-${cut.id}-heading`} className="cut-card">
      <header className="cut-header">
        <div>
          <p className="eyebrow">SHOT {String(cut.order).padStart(2, "0")}</p>
          <h3 id={`cut-${cut.id}-heading`}>Cut {cut.order}</h3>
        </div>
        <span className="duration">{cut.durationSec} sec</span>
      </header>

      <p className="prompt-copy"><span>Shot</span>{cut.shotDescription}</p>

      {generationMode === "MOCK" && (
        <label className="scenario-control">
          Mock scenario for Cut {cut.order}
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

      <div className="generation-grid">
        <section aria-labelledby={`cut-${cut.id}-images`} className="generation-column">
          <div className="column-heading">
            <div>
              <p className="eyebrow">STILL</p>
              <h4 id={`cut-${cut.id}-images`}>Images</h4>
            </div>
            <button
              className="button button--secondary"
              disabled={imageGeneration.isPending || imageActive}
              onClick={() => {
                resetMutationErrors();
                imageGeneration.mutate();
              }}
              type="button"
            >
              {imageLabel}
            </button>
          </div>

          <p className="prompt-copy"><span>Image prompt</span>{cut.imagePrompt}</p>
          <div className="artifact-list">
            {sortedImages(cut).map((image) => {
              const version = versionForArtifact(image.generationJobId, cut.imageJobs);
              const selected = cut.selectedImageId === image.id;
              return (
                <figure className={`artifact ${selected ? "artifact--selected" : ""}`} key={image.id}>
                  <img alt={`Cut ${cut.order} image version ${version}`} src={resolveMediaUrl(image.url)} />
                  <figcaption>
                    <strong>Image v{version}</strong>
                    {selected ? (
                      <span className="selected-label">Selected</span>
                    ) : (
                      <button
                        className="text-button"
                        disabled={selectImage.isPending || videoGeneration.isPending}
                        onClick={() => {
                          resetMutationErrors();
                          selectImage.mutate(image.id);
                        }}
                        type="button"
                      >
                        Select Image v{version}
                      </button>
                    )}
                  </figcaption>
                </figure>
              );
            })}
          </div>
          <GenerationHistory cut={cut} jobs={cut.imageJobs} kind="IMAGE" />
        </section>

        <section aria-labelledby={`cut-${cut.id}-videos`} className="generation-column">
          <div className="column-heading">
            <div>
              <p className="eyebrow">MOTION</p>
              <h4 id={`cut-${cut.id}-videos`}>Videos</h4>
            </div>
            <button
              className="button button--secondary"
              disabled={
                videoGeneration.isPending ||
                selectImage.isPending ||
                videoActive ||
                !cut.selectedImageId
              }
              onClick={() => {
                resetMutationErrors();
                videoGeneration.mutate();
              }}
              type="button"
            >
              {videoLabel}
            </button>
          </div>

          <p className="prompt-copy"><span>Video prompt</span>{cut.videoPrompt}</p>
          {!cut.selectedImageId && <p className="selection-note">Select an image before generating video.</p>}
          {cut.selectedImageId && !cut.selectedVideoId && (
            <p className="selection-note">Select a compatible video</p>
          )}
          <div className="artifact-list">
            {sortedVideos(cut).map((video) => {
              const version = versionForArtifact(video.generationJobId, cut.videoJobs);
              const selected = cut.selectedVideoId === video.id;
              const compatible = video.cutImageId === cut.selectedImageId;
              return (
                <figure className={`artifact ${selected ? "artifact--selected" : ""}`} key={video.id}>
                  <video controls preload="metadata" src={resolveMediaUrl(video.url)}>
                    Your browser does not support video playback.
                  </video>
                  <figcaption>
                    <strong>Video v{version}</strong>
                    {selected ? (
                      <span className="selected-label">Selected</span>
                    ) : compatible ? (
                      <button
                        className="text-button"
                        disabled={selectVideo.isPending}
                        onClick={() => {
                          resetMutationErrors();
                          selectVideo.mutate(video.id);
                        }}
                        type="button"
                      >
                        Select Video v{version}
                      </button>
                    ) : (
                      <span className="muted">Different source image</span>
                    )}
                  </figcaption>
                </figure>
              );
            })}
          </div>
          <GenerationHistory cut={cut} jobs={cut.videoJobs} kind="VIDEO" />
        </section>
      </div>

      {mutationError && <p className="error-message" role="alert">{errorMessage(mutationError)}</p>}
    </section>
  );
}
