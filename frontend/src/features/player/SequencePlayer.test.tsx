import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Cut } from "../../api/types";
import {
  cutDetail,
  cutWithImageAndVideoVersions,
  generationJob,
  sceneDetail,
} from "../../test/fixtures";
import { renderWithClient } from "../../test/render";
import { server } from "../../test/server";
import { SceneWorkspace } from "../scene/SceneWorkspace";
import { SequencePlayer } from "./SequencePlayer";

function readyCut(order: number, suffix = ""): Cut {
  const imageId = `image-${order}${suffix}`;
  const videoJobId = `video-job-${order}${suffix}`;
  const videoId = `video-${order}${suffix}`;

  return cutDetail({
    id: `cut-${order}`,
    order,
    selectedImageId: imageId,
    selectedVideoId: videoId,
    images: [{
      id: imageId,
      generationJobId: `image-job-${order}`,
      url: `/media/cut-${order}.png`,
      inputPrompt: `Image ${order}`,
      createdAt: "2026-08-14T12:00:00Z",
    }],
    videos: [{
      id: videoId,
      cutImageId: imageId,
      generationJobId: videoJobId,
      url: `/media/cut-${order}.mp4`,
      inputPrompt: `Video ${order}`,
      createdAt: "2026-08-14T12:00:00Z",
    }],
    videoJobs: [generationJob({
      id: videoJobId,
      kind: "VIDEO",
      prompt: `Video ${order}`,
      sourceImageId: imageId,
      status: "SUCCEEDED",
    })],
  });
}

function sixReadyCuts(): Cut[] {
  return Array.from({ length: 6 }, (_, index) => readyCut(index + 1));
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, reject, resolve };
}

describe("SequencePlayer", () => {
  let play: ReturnType<typeof vi.spyOn>;
  let pause: ReturnType<typeof vi.spyOn>;
  let pausedMedia: HTMLMediaElement[];
  let playedSources: string[];

  beforeEach(() => {
    pausedMedia = [];
    playedSources = [];
    play = vi.spyOn(window.HTMLMediaElement.prototype, "play").mockImplementation(function (this: HTMLMediaElement) {
      playedSources.push(this.getAttribute("src") ?? "");
      return Promise.resolve();
    });
    pause = vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(function (this: HTMLMediaElement) {
      pausedMedia.push(this);
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // Mutation caught: accepting a sequence with fewer than six ready Cuts.
  it("requires exactly six compatible selected videos", () => {
    const cuts = sixReadyCuts();
    cuts[5] = cutDetail({ id: "cut-6", order: 6 });

    render(<SequencePlayer cuts={cuts} />);

    expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
    expect(screen.getByText("5 of 6 videos ready")).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-video")).not.toBeInTheDocument();
  });

  // Mutation caught: allowing five ready Cuts to be described only as partial readiness.
  it("reports invalid cardinality when five ready Cuts are supplied", () => {
    render(<SequencePlayer cuts={sixReadyCuts().slice(0, 5)} />);

    expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
    expect(screen.getByRole("status", {
      name: "Exactly 6 Cuts required; 5 found",
    })).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-video")).not.toBeInTheDocument();
  });

  // Mutation caught: masking seven-Cut cardinality as six-of-six readiness.
  it("reports invalid cardinality when seven Cuts include six ready videos", () => {
    render(<SequencePlayer cuts={[...sixReadyCuts(), cutDetail({ id: "cut-7", order: 7 })]} />);

    expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
    expect(screen.getByRole("status", {
      name: "Exactly 6 Cuts required; 7 found",
    })).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-video")).not.toBeInTheDocument();
  });

  // Mutation caught: removing live status semantics from valid-cardinality readiness feedback.
  it("announces compatible video readiness as an accessible status", () => {
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    expect(screen.getByRole("status", {
      name: "6 of 6 videos ready",
    })).toBeInTheDocument();
  });

  // Mutation caught: treating a dangling selectedImageId as a selected image.
  it("requires the selected image artifact to exist", () => {
    const cuts = sixReadyCuts();
    cuts[2] = { ...cuts[2], images: [] };

    render(<SequencePlayer cuts={cuts} />);

    expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
    expect(screen.getByText("5 of 6 videos ready")).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-video")).not.toBeInTheDocument();
  });

  // Mutation caught: accepting a selected video produced from another image.
  it("requires the selected video artifact to match the selected image", () => {
    const cuts = sixReadyCuts();
    cuts[2] = {
      ...cuts[2],
      videos: [{ ...cuts[2].videos[0], cutImageId: "image-older" }],
    };

    render(<SequencePlayer cuts={cuts} />);

    expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
    expect(screen.getByText("5 of 6 videos ready")).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-video")).not.toBeInTheDocument();
  });

  // Mutation caught: accepting an artifact without its corresponding successful VIDEO job.
  it("requires the selected video job to exist and be successful", () => {
    const cuts = sixReadyCuts();
    cuts[2] = {
      ...cuts[2],
      videoJobs: [{ ...cuts[2].videoJobs[0], status: "FAILED" }],
    };

    render(<SequencePlayer cuts={cuts} />);

    expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
    expect(screen.getByText("5 of 6 videos ready")).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-video")).not.toBeInTheDocument();
  });

  // Mutation caught: accepting a successful video job from a stale source image.
  it("requires the selected video job source to match the selected image", () => {
    const cuts = sixReadyCuts();
    cuts[2] = {
      ...cuts[2],
      videoJobs: [{ ...cuts[2].videoJobs[0], sourceImageId: "image-older" }],
    };

    render(<SequencePlayer cuts={cuts} />);

    expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
    expect(screen.getByText("5 of 6 videos ready")).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-video")).not.toBeInTheDocument();
  });

  // Mutation caught: deferring initial play or ignoring an ended transition.
  it("starts Cut 1 from the primary control and advances on ended", async () => {
    const user = userEvent.setup();
    render(<SequencePlayer cuts={[...sixReadyCuts()].reverse()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));

    expect(play).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
    expect(screen.getByTestId("sequence-video")).toHaveAttribute(
      "src",
      "http://localhost:8000/media/cut-1.mp4",
    );

    fireEvent.ended(screen.getByTestId("sequence-video"));

    expect(screen.getByText("Cut 2 of 6")).toBeInTheDocument();
    await waitFor(() => expect(play).toHaveBeenCalledTimes(2));
  });

  // Mutation caught: changing UI state without pausing media or allowing ended after pause.
  it("pauses and resumes the current Cut with the primary control", async () => {
    const user = userEvent.setup();
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    const currentMedia = screen.getByTestId("sequence-video");
    expect(screen.getByRole("button", { name: "Pause sequence" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Pause sequence" }));
    expect(pause).toHaveBeenCalledTimes(1);
    expect(pausedMedia).toHaveLength(1);
    expect(pausedMedia[0]).toBe(currentMedia);
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();

    fireEvent.ended(screen.getByTestId("sequence-video"));
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    expect(screen.getByRole("button", { name: "Pause sequence" })).toBeEnabled();
    expect(play).toHaveBeenCalledTimes(2);
    expect(playedSources.at(-1)).toBe("http://localhost:8000/media/cut-1.mp4");
  });

  // Mutation caught: retaining playback state when selected image/video lineage changes.
  it("resets to stopped Cut 1 when a selection lineage changes", async () => {
    const user = userEvent.setup();
    const cuts = sixReadyCuts();
    const view = render(<SequencePlayer cuts={cuts} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    fireEvent.ended(screen.getByTestId("sequence-video"));
    expect(screen.getByText("Cut 2 of 6")).toBeInTheDocument();

    const changedCuts = [...cuts];
    changedCuts[5] = readyCut(6, "-new");
    view.rerender(<SequencePlayer cuts={changedCuts} />);

    expect(pause).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
    expect(screen.getByRole("progressbar", { name: "Sequence progress" })).toHaveAttribute(
      "value",
      "0",
    );
  });

  // Mutation caught: pausing the replacement node instead of the detached playing node.
  it("pauses the exact old media when the current Cut lineage changes", async () => {
    const user = userEvent.setup();
    const cuts = sixReadyCuts();
    const view = render(<SequencePlayer cuts={cuts} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    const oldMedia = screen.getByTestId("sequence-video");

    const changedCuts = [...cuts];
    changedCuts[0] = readyCut(1, "-new");
    view.rerender(<SequencePlayer cuts={changedCuts} />);

    expect(screen.getByTestId("sequence-video")).not.toBe(oldMedia);
    expect(pausedMedia).toHaveLength(1);
    expect(pausedMedia[0]).toBe(oldMedia);
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
  });

  // Mutation caught: invalidating on unmount without pausing the owned active media.
  it("pauses the exact active media once when a Strict Mode player unmounts", async () => {
    const user = userEvent.setup();
    const view = render(
      <StrictMode>
        <SequencePlayer cuts={sixReadyCuts()} />
      </StrictMode>,
    );

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    const activeMedia = screen.getByTestId("sequence-video");
    view.unmount();

    expect(pausedMedia).toHaveLength(1);
    expect(pausedMedia[0]).toBe(activeMedia);
  });

  // Mutation caught: leaving playback active or skipping after the current play Promise rejects.
  it("stops and reports when playback cannot start", async () => {
    const user = userEvent.setup();
    play.mockReset().mockRejectedValueOnce(new Error("autoplay denied"));
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Playback could not start");
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
    fireEvent.ended(screen.getByTestId("sequence-video"));
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
  });

  // Mutation caught: leaving the mounted guard false after Strict Mode replays effects.
  it("reports current play rejection after Strict Mode effect replay", async () => {
    const user = userEvent.setup();
    play.mockReset().mockRejectedValueOnce(new Error("autoplay denied"));
    render(
      <StrictMode>
        <SequencePlayer cuts={sixReadyCuts()} />
      </StrictMode>,
    );

    await user.click(screen.getByRole("button", { name: "Play sequence" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Playback could not start");
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
  });

  // Mutation caught: ignoring autoplay rejection after an ended transition.
  it("stops on the new Cut when transition autoplay rejects", async () => {
    const user = userEvent.setup();
    play.mockReset()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("autoplay denied"));
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    fireEvent.ended(screen.getByTestId("sequence-video"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Playback could not start");
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
    expect(screen.getByText("Cut 2 of 6")).toBeInTheDocument();
    fireEvent.ended(screen.getByTestId("sequence-video"));
    expect(screen.getByText("Cut 2 of 6")).toBeInTheDocument();
  });

  // Mutation caught: allowing an older play rejection to stop a newer resumed attempt.
  it("ignores a stale play rejection after pause and newer play", async () => {
    const user = userEvent.setup();
    const firstPlay = deferred<void>();
    play.mockReset()
      .mockReturnValueOnce(firstPlay.promise)
      .mockResolvedValueOnce(undefined);
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    await user.click(screen.getByRole("button", { name: "Pause sequence" }));
    await user.click(screen.getByRole("button", { name: "Play sequence" }));

    await act(async () => {
      firstPlay.reject(new Error("late rejection"));
      await firstPlay.promise.catch(() => undefined);
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause sequence" })).toBeEnabled();
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
  });

  // Mutation caught: restoring an old play error after a lineage reset cleared it.
  it("ignores a stale play rejection after selection reset", async () => {
    const user = userEvent.setup();
    const pendingPlay = deferred<void>();
    play.mockReset().mockReturnValueOnce(pendingPlay.promise);
    const cuts = sixReadyCuts();
    const view = render(<SequencePlayer cuts={cuts} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    const changedCuts = [...cuts];
    changedCuts[5] = readyCut(6, "-new");
    view.rerender(<SequencePlayer cuts={changedCuts} />);

    await act(async () => {
      pendingPlay.reject(new Error("late rejection"));
      await pendingPlay.promise.catch(() => undefined);
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
  });

  // Mutation caught: skipping a Cut or remaining active after the media element errors.
  it("stops on the current Cut and reports a media load error", async () => {
    const user = userEvent.setup();
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    const activeMedia = screen.getByTestId("sequence-video");
    fireEvent.error(activeMedia);

    expect(screen.getByRole("alert")).toHaveTextContent("Cut video could not be loaded");
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
    expect(pausedMedia).toHaveLength(1);
    expect(pausedMedia[0]).toBe(activeMedia);
    fireEvent.ended(screen.getByTestId("sequence-video"));
    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
  });

  // Mutation caught: retaining a media failure after selected lineage changes.
  it("clears playback errors when selection lineage changes", async () => {
    const user = userEvent.setup();
    const cuts = sixReadyCuts();
    const view = render(<SequencePlayer cuts={cuts} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    fireEvent.error(screen.getByTestId("sequence-video"));
    expect(screen.getByRole("alert")).toHaveTextContent("Cut video could not be loaded");

    const changedCuts = [...cuts];
    changedCuts[5] = readyCut(6, "-new");
    view.rerender(<SequencePlayer cuts={changedCuts} />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
  });

  // Mutation caught: leaving the player active or progress incomplete after Cut 6 ends.
  it("completes after Cut 6 and ignores further ended events", async () => {
    const user = userEvent.setup();
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    for (let cut = 1; cut < 6; cut += 1) {
      fireEvent.ended(screen.getByTestId("sequence-video"));
      expect(screen.getByText(`Cut ${cut + 1} of 6`)).toBeInTheDocument();
    }
    fireEvent.ended(screen.getByTestId("sequence-video"));

    expect(screen.getByRole("button", { name: "Restart sequence" })).toBeEnabled();
    expect(screen.getByRole("progressbar", { name: "Sequence progress" })).toHaveAttribute(
      "value",
      "6",
    );

    fireEvent.ended(screen.getByTestId("sequence-video"));
    expect(screen.getByText("Cut 6 of 6")).toBeInTheDocument();
    expect(play).toHaveBeenCalledTimes(6);
  });

  // Mutation caught: restarting by calling play on the stale Cut 6 media node.
  it("restarts completion from the newly rendered Cut 1 video", async () => {
    const user = userEvent.setup();
    render(<SequencePlayer cuts={sixReadyCuts()} />);

    await user.click(screen.getByRole("button", { name: "Play sequence" }));
    for (let cut = 1; cut <= 6; cut += 1) {
      fireEvent.ended(screen.getByTestId("sequence-video"));
    }

    await user.click(screen.getByRole("button", { name: "Restart sequence" }));

    expect(screen.getByText("Cut 1 of 6")).toBeInTheDocument();
    await waitFor(() => {
      expect(playedSources.at(-1)).toBe("http://localhost:8000/media/cut-1.mp4");
    });
  });

  // Mutation caught: restoring the exact Duration label above the nominal player.
  it("labels restored Scene duration as nominal", async () => {
    const scene = sceneDetail({
      cuts: Array.from({ length: 6 }, () => cutWithImageAndVideoVersions()).map(
        (cut, index) => ({ ...cut, id: `cut-${index + 1}`, order: index + 1 }),
      ),
    });
    server.use(
      http.get("http://localhost:8000/api/scenes/scene-1", () =>
        HttpResponse.json(scene),
      ),
    );

    renderWithClient(<SceneWorkspace sceneId="scene-1" generationMode="LIVE" />);

    expect(await screen.findByRole("heading", { name: "Nominal 30-second sequence" })).toBeInTheDocument();
    expect(screen.getByText("Nominal duration")).toBeInTheDocument();
    expect(screen.getByText("30 sec")).toBeInTheDocument();
    expect(screen.queryByText(/^Duration$/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play sequence" })).toBeEnabled();
    expect(screen.getByTestId("sequence-video")).toBeInTheDocument();
  });
});
