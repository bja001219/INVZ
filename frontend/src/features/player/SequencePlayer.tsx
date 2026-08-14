import { useEffect, useRef, useState } from "react";

import { resolveMediaUrl } from "../../api/client";
import type { Cut, CutVideo } from "../../api/types";

interface SequencePlayerProps {
  cuts: Cut[];
}

export function SequencePlayer({ cuts }: SequencePlayerProps) {
  const orderedCuts = [...cuts].sort((left, right) => left.order - right.order);
  const readyVideos = orderedCuts.map(selectedReadyVideo);
  const readyCount = readyVideos.filter((video) => video !== null).length;
  const hasSixCuts = orderedCuts.length === 6;
  const ready = hasSixCuts && readyCount === 6;
  const readinessMessage = hasSixCuts
    ? `${readyCount} of 6 videos ready`
    : `Exactly 6 Cuts required; ${orderedCuts.length} found`;
  const lineageSignature = selectionLineageSignature(orderedCuts);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const activeMediaRef = useRef<HTMLVideoElement | null>(null);
  const previousLineageRef = useRef(lineageSignature);
  const playAttemptRef = useRef(0);
  const mountedRef = useRef(true);
  const currentVideo = ready ? readyVideos[currentIndex] : null;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      pauseActiveMedia();
      mountedRef.current = false;
      playAttemptRef.current += 1;
    };
  }, []);

  useEffect(() => {
    if (previousLineageRef.current === lineageSignature) return;
    previousLineageRef.current = lineageSignature;
    playAttemptRef.current += 1;
    pauseActiveMedia();
    setPlaying(false);
    setCompleted(false);
    setCurrentIndex(0);
    setPlaybackError(null);
  }, [lineageSignature]);

  useEffect(() => {
    const media = videoRef.current;
    if (playing && media) startPlayback(media);
  }, [currentIndex]);

  function startPlayback(media: HTMLVideoElement) {
    const attempt = playAttemptRef.current + 1;
    playAttemptRef.current = attempt;
    activeMediaRef.current = media;
    setPlaybackError(null);
    setPlaying(true);

    let playPromise: Promise<void>;
    try {
      playPromise = media.play();
    } catch {
      failPlayback(attempt);
      return;
    }
    void playPromise.catch(() => failPlayback(attempt));
  }

  function failPlayback(attempt: number) {
    if (!mountedRef.current || playAttemptRef.current !== attempt) return;
    activeMediaRef.current = null;
    setPlaying(false);
    setPlaybackError("Playback could not start");
  }

  function pauseActiveMedia() {
    const media = activeMediaRef.current;
    activeMediaRef.current = null;
    media?.pause();
  }

  function playSequence() {
    if (playing) {
      playAttemptRef.current += 1;
      pauseActiveMedia();
      setPlaying(false);
      return;
    }

    if (completed) {
      playAttemptRef.current += 1;
      setCompleted(false);
      setPlaying(true);
      setCurrentIndex(0);
      return;
    }

    const media = videoRef.current;
    if (!media) return;
    startPlayback(media);
  }

  function advanceSequence() {
    if (!playing) return;
    playAttemptRef.current += 1;
    activeMediaRef.current = null;
    if (currentIndex === 5) {
      setPlaying(false);
      setCompleted(true);
      return;
    }
    setCurrentIndex((index) => index + 1);
  }

  function handleMediaError() {
    playAttemptRef.current += 1;
    pauseActiveMedia();
    setPlaying(false);
    setCompleted(false);
    setPlaybackError("Cut video could not be loaded");
  }

  return (
    <section className="sequence-player" aria-labelledby="sequence-player-title">
      <header className="sequence-player__header">
        <p className="eyebrow">SEQUENCE PLAYER</p>
        <h3 id="sequence-player-title">Nominal 30-second sequence</h3>
        <p aria-label={readinessMessage} className="muted" role="status">
          {readinessMessage}
        </p>
      </header>

      <div className="sequence-player__stage">
        {currentVideo && (
          <video
            aria-label={`Sequence preview, Cut ${currentIndex + 1}`}
            data-testid="sequence-video"
            key={currentVideo.id}
            onEnded={advanceSequence}
            onError={handleMediaError}
            ref={videoRef}
            src={resolveMediaUrl(currentVideo.url)}
          >
            Your browser does not support video playback.
          </video>
        )}
      </div>

      <div className="sequence-player__controls">
        <div className="sequence-player__progress">
          <strong>Cut {currentIndex + 1} of 6</strong>
          <progress
            aria-label="Sequence progress"
            max={6}
            value={completed ? 6 : currentIndex}
          />
        </div>
        <button
          className="button button--primary"
          disabled={!ready}
          onClick={playSequence}
          type="button"
        >
          {completed ? "Restart sequence" : playing ? "Pause sequence" : "Play sequence"}
        </button>
      </div>
      {playbackError && <p className="error-message" role="alert">{playbackError}</p>}
    </section>
  );
}

function selectedReadyVideo(cut: Cut): CutVideo | null {
  if (!cut.selectedImageId || !cut.selectedVideoId) return null;
  if (!cut.images.some((image) => image.id === cut.selectedImageId)) return null;

  const video = cut.videos.find((candidate) => candidate.id === cut.selectedVideoId);
  if (!video || video.cutImageId !== cut.selectedImageId) return null;

  const job = cut.videoJobs.find((candidate) => candidate.id === video.generationJobId);
  if (
    !job ||
    job.kind !== "VIDEO" ||
    job.status !== "SUCCEEDED" ||
    job.sourceImageId !== cut.selectedImageId
  ) return null;

  return video;
}

function selectionLineageSignature(cuts: Cut[]): string {
  return JSON.stringify(cuts.map((cut) => {
    const image = cut.images.find((candidate) => candidate.id === cut.selectedImageId);
    const video = cut.videos.find((candidate) => candidate.id === cut.selectedVideoId);
    const job = cut.videoJobs.find((candidate) => candidate.id === video?.generationJobId);
    return {
      cutId: cut.id,
      order: cut.order,
      selectedImageId: cut.selectedImageId,
      imageId: image?.id,
      selectedVideoId: cut.selectedVideoId,
      videoId: video?.id,
      videoImageId: video?.cutImageId,
      videoJobId: video?.generationJobId,
      jobId: job?.id,
      jobKind: job?.kind,
      jobStatus: job?.status,
      jobSourceImageId: job?.sourceImageId,
    };
  }));
}
