import { useQuery } from "@tanstack/react-query";
import { http, HttpResponse, delay } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CutCard } from "./CutCard";
import type { GenerationMode, Scene } from "../../api/types";
import {
  cutDetail,
  cutWithFailedVideo,
  cutWithImageAndVideoVersions,
  cutWithoutImage,
  generationJob,
  sceneDetail,
} from "../../test/fixtures";
import { createTestQueryClient, renderWithClient } from "../../test/render";
import { server } from "../../test/server";

const apiBase = "http://localhost:8000";

function deferredSignal() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

function CutHarness({
  sceneId,
  generationMode = "MOCK",
}: {
  sceneId: string;
  generationMode?: GenerationMode;
}) {
  const { data: scene } = useQuery<Scene>({
    queryKey: ["scene", sceneId],
    queryFn: async () => {
      const response = await fetch(`${apiBase}/api/scenes/${sceneId}`);
      return response.json() as Promise<Scene>;
    },
  });

  if (!scene) return null;
  return <CutCard cut={scene.cuts[0]} sceneId={sceneId} generationMode={generationMode} />;
}

function renderCut(cut = cutWithoutImage(), generationMode: GenerationMode = "MOCK") {
  const scene = sceneDetail({ cuts: [cut] });
  server.use(
    http.get(`${apiBase}/api/scenes/${scene.id}`, () => HttpResponse.json(scene)),
  );
  const queryClient = createTestQueryClient();
  queryClient.setQueryData(["scene", scene.id], scene);
  return renderWithClient(
    <CutHarness sceneId={scene.id} generationMode={generationMode} />,
    queryClient,
  );
}

describe("CutCard", () => {
  it("shows the shot description, generation mode, and reference image of each job", () => {
    const firstJob = generationJob({ id: "image-job-1", version: 1 });
    const anchor = {
      id: "image-1",
      generationJobId: firstJob.id,
      url: "/media/mock/cut-image.png",
      inputPrompt: firstJob.prompt,
      createdAt: "2026-08-14T12:00:00Z",
    };
    const secondJob = generationJob({
      id: "image-job-2",
      version: 2,
      generationMode: "LIVE",
      referenceImageId: anchor.id,
    });

    renderCut(
      cutDetail({
        shotDescription: "The two leads meet at the school gate",
        imageJobs: [secondJob, firstJob],
        images: [anchor],
      }),
    );

    expect(screen.getByText("The two leads meet at the school gate")).toBeInTheDocument();
    const referencedJob = screen.getByRole("article", { name: "Image generation v2" });
    expect(within(referencedJob).getByText("LIVE")).toBeInTheDocument();
    expect(within(referencedJob).getByText("Image v1")).toBeInTheDocument();
    const anchorJob = screen.getByRole("article", { name: "Image generation v1" });
    expect(within(anchorJob).getByText("MOCK")).toBeInTheDocument();
    expect(within(anchorJob).queryByText("Reference")).not.toBeInTheDocument();
  });

  it("explains that a queued job is holding for the Cut 1 anchor image", () => {
    const gated = generationJob({ status: "QUEUED", waitingForAnchor: true });

    renderCut(cutDetail({ order: 3, imageJobs: [gated] }));

    const job = screen.getByRole("article", { name: "Image generation v1" });
    expect(within(job).getByText(/waiting for the cut 1 image/i)).toBeInTheDocument();
  });

  it("says nothing about the anchor once a job is no longer gated", () => {
    const running = generationJob({ status: "PROCESSING" });

    renderCut(cutDetail({ order: 3, imageJobs: [running] }));

    const job = screen.getByRole("article", { name: "Image generation v1" });
    expect(within(job).queryByText(/waiting for the cut 1 image/i)).not.toBeInTheDocument();
  });

  it("disables generation immediately while the mutation is pending", async () => {
    const user = userEvent.setup();
    const receivedImageRequests: unknown[] = [];
    server.use(
      http.post(`${apiBase}/api/cuts/cut-1/images`, async ({ request }) => {
        receivedImageRequests.push(await request.json());
        await delay("infinite");
        return HttpResponse.json(generationJob(), { status: 202 });
      }),
    );
    renderCut(cutWithoutImage());
    const button = screen.getByRole("button", { name: "Generate image" });

    await user.click(button);

    expect(button).toBeDisabled();
    await user.click(button);
    expect(receivedImageRequests).toHaveLength(1);
  });

  it("shows retry and final failure then regenerates a new version", async () => {
    const user = userEvent.setup();
    const receivedVideoRequests: unknown[] = [];
    server.use(
      http.post(`${apiBase}/api/cuts/cut-1/videos`, async ({ request }) => {
        receivedVideoRequests.push(await request.json());
        return HttpResponse.json(
          generationJob({ id: "video-job-2", kind: "VIDEO", version: 2, status: "QUEUED" }),
          { status: 202 },
        );
      }),
      http.get(`${apiBase}/api/scenes/scene-1`, () =>
        HttpResponse.json(sceneDetail({ cuts: [cutWithFailedVideo({ attemptCount: 3, maxAttempts: 3 })] })),
      ),
    );
    renderCut(cutWithFailedVideo({ attemptCount: 3, maxAttempts: 3 }));

    expect(screen.getByText("Failed after 3/3 attempts")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Regenerate video" }));

    expect(receivedVideoRequests).toHaveLength(1);
  });

  it("shows retry timing and stable error detail", () => {
    const cut = cutWithFailedVideo({ attemptCount: 2, maxAttempts: 3 });
    cut.videoJobs[0] = generationJob({
      ...cut.videoJobs[0],
      status: "RETRY_WAIT",
      nextRunAt: "2026-08-14T12:00:05Z",
    });
    renderCut(cut);

    const history = screen.getByRole("article", { name: "Video generation v1" });
    expect(within(history).getByText("Retrying after 2/3 attempts")).toBeInTheDocument();
    expect(within(history).getByText("Next retry")).toBeInTheDocument();
    expect(within(history).getByText("Generation provider failed")).toBeInTheDocument();
  });

  it("clears the displayed video selection after selecting another image", async () => {
    const user = userEvent.setup();
    const current = sceneDetail({ cuts: [cutWithImageAndVideoVersions()] });
    const cleared = sceneDetail({
      cuts: [{ ...cutWithImageAndVideoVersions(), selectedImageId: "image-2", selectedVideoId: null }],
    });
    server.use(
      http.put(`${apiBase}/api/cuts/cut-1/selected-image`, () => HttpResponse.json(cleared)),
      http.get(`${apiBase}/api/scenes/scene-1`, () => HttpResponse.json(cleared)),
    );
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(["scene", current.id], current);
    renderWithClient(<CutHarness sceneId={current.id} />, queryClient);

    await user.click(screen.getByRole("button", { name: "Select Image v2" }));

    expect(await screen.findByText("Select a compatible video")).toBeInTheDocument();
  });

  it("shows newest generation versions first and resolves relative media", () => {
    renderCut(cutWithImageAndVideoVersions());

    const imageJobs = screen.getAllByRole("article", { name: /Image generation v/ });
    expect(imageJobs.map((job) => job.getAttribute("aria-label"))).toEqual([
      "Image generation v2",
      "Image generation v1",
    ]);
    expect(screen.getAllByRole("img")[0]).toHaveAttribute(
      "src",
      "http://localhost:8000/media/mock/cut-image.png",
    );
  });

  it("clears an earlier mutation error when a different action succeeds", async () => {
    const user = userEvent.setup();
    const cut = cutWithFailedVideo({ attemptCount: 3, maxAttempts: 3 });
    server.use(
      http.post(`${apiBase}/api/cuts/cut-1/images`, () =>
        HttpResponse.json(
          { code: "GENERATION_PROVIDER_FAILED", message: "Generation provider failed" },
          { status: 502 },
        ),
      ),
      http.post(`${apiBase}/api/cuts/cut-1/videos`, () =>
        HttpResponse.json(
          generationJob({
            id: "video-job-2",
            kind: "VIDEO",
            version: 2,
            status: "QUEUED",
          }),
          { status: 202 },
        ),
      ),
    );
    renderCut(cut);

    await user.click(screen.getByRole("button", { name: "Regenerate image" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Generation provider failed");

    await user.click(screen.getByRole("button", { name: "Regenerate video" }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("locks an already-active image generation without sending another request", async () => {
    const user = userEvent.setup();
    let receivedRequests = 0;
    const cut = cutWithoutImage();
    cut.imageJobs = [generationJob({ status: "PROCESSING" })];
    server.use(
      http.post(`${apiBase}/api/cuts/cut-1/images`, () => {
        receivedRequests += 1;
        return HttpResponse.json(generationJob(), { status: 202 });
      }),
    );
    renderCut(cut);
    const button = screen.getByRole("button", { name: "Regenerate image" });

    expect(button).toBeDisabled();
    await user.click(button);
    expect(receivedRequests).toBe(0);
  });

  it("sends the selected scenario only in a Mock generation request", async () => {
    const user = userEvent.setup();
    const receivedBodies: unknown[] = [];
    server.use(
      http.post(`${apiBase}/api/cuts/cut-1/images`, async ({ request }) => {
        receivedBodies.push(await request.json());
        return HttpResponse.json(generationJob({ status: "QUEUED" }), { status: 202 });
      }),
    );
    renderCut(cutWithoutImage(), "MOCK");

    await user.selectOptions(screen.getByLabelText("Mock scenario for Cut 1"), "ALWAYS_FAIL");
    await user.click(screen.getByRole("button", { name: "Generate image" }));

    await waitFor(() => expect(receivedBodies).toEqual([{ mockScenario: "ALWAYS_FAIL" }]));
  });

  it("omits the scenario field and control in a Live generation request", async () => {
    const user = userEvent.setup();
    const receivedBodies: unknown[] = [];
    server.use(
      http.post(`${apiBase}/api/cuts/cut-1/images`, async ({ request }) => {
        receivedBodies.push(await request.json());
        return HttpResponse.json(generationJob({ status: "QUEUED" }), { status: 202 });
      }),
    );
    renderCut(cutWithoutImage(), "LIVE");

    expect(screen.queryByLabelText("Mock scenario for Cut 1")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Generate image" }));

    await waitFor(() => expect(receivedBodies).toEqual([{}]));
  });

  it("disables video generation while image selection is pending", async () => {
    const user = userEvent.setup();
    const selectionGate = deferredSignal();
    const currentCut = cutWithImageAndVideoVersions();
    const selectedScene = sceneDetail({
      cuts: [{ ...currentCut, selectedImageId: "image-2", selectedVideoId: null }],
    });
    server.use(
      http.put(`${apiBase}/api/cuts/cut-1/selected-image`, async () => {
        await selectionGate.promise;
        return HttpResponse.json(selectedScene);
      }),
    );
    renderCut(currentCut);

    await user.click(screen.getByRole("button", { name: "Select Image v2" }));

    const videoButton = screen.getByRole("button", { name: "Regenerate video" });
    expect(videoButton).toBeDisabled();
    expect(screen.getByRole("button", { name: "Regenerate image" })).toBeEnabled();

    selectionGate.release();
    await waitFor(() => expect(videoButton).toBeEnabled());
  });

  it("disables image selection while video generation is pending", async () => {
    const user = userEvent.setup();
    const videoGate = deferredSignal();
    server.use(
      http.post(`${apiBase}/api/cuts/cut-1/videos`, async () => {
        await videoGate.promise;
        return HttpResponse.json(
          generationJob({
            id: "video-job-2",
            kind: "VIDEO",
            version: 2,
            status: "QUEUED",
          }),
          { status: 202 },
        );
      }),
    );
    renderCut(cutWithImageAndVideoVersions());

    await user.click(screen.getByRole("button", { name: "Regenerate video" }));

    const imageSelection = screen.getByRole("button", { name: "Select Image v2" });
    expect(imageSelection).toBeDisabled();
    expect(screen.getByRole("button", { name: "Regenerate image" })).toBeEnabled();

    videoGate.release();
    await waitFor(() => expect(imageSelection).toBeEnabled());
  });
});
