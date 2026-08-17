import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BatchControls, batchProgress } from "./BatchControls";
import { CharacterPanel } from "./CharacterPanel";
import { cutDetail, generationJob, twoProfiles } from "../../test/fixtures";
import { renderWithClient } from "../../test/render";
import { server } from "../../test/server";
import type { Cut, GenerationJob } from "../../api/types";

function cutsWith(imageJobs: (GenerationJob | null)[]): Cut[] {
  return imageJobs.map((job, index) =>
    cutDetail({
      id: `cut-${index + 1}`,
      order: index + 1,
      selectedImageId: job?.status === "SUCCEEDED" ? `image-${index + 1}` : null,
      imageJobs: job ? [job] : [],
    }),
  );
}

const sixSucceeded = () =>
  cutsWith(
    Array.from({ length: 6 }, (_, index) =>
      generationJob({ id: `image-job-${index + 1}`, status: "SUCCEEDED" }),
    ),
  );

describe("batchProgress", () => {
  it("counts only the newest job version per cut", () => {
    const cuts = [
      cutDetail({
        imageJobs: [
          generationJob({ id: "v1", version: 1, status: "FAILED" }),
          generationJob({ id: "v2", version: 2, status: "SUCCEEDED" }),
        ],
      }),
    ];

    expect(batchProgress(cuts, "IMAGE")).toEqual({ done: 1, failed: 0, active: 0 });
  });

  it("separates running work from finished and failed work", () => {
    const cuts = cutsWith([
      generationJob({ id: "a", status: "SUCCEEDED" }),
      generationJob({ id: "b", status: "SUCCEEDED" }),
      generationJob({ id: "c", status: "PROCESSING" }),
      generationJob({ id: "d", status: "RETRY_WAIT" }),
      generationJob({ id: "e", status: "FAILED" }),
      null,
    ]);

    expect(batchProgress(cuts, "IMAGE")).toEqual({ done: 2, failed: 1, active: 2 });
  });
});

describe("BatchControls", () => {
  it("reports batch progress across the six cuts", () => {
    const cuts = cutsWith([
      generationJob({ id: "a", status: "SUCCEEDED" }),
      generationJob({ id: "b", status: "SUCCEEDED" }),
      generationJob({ id: "c", status: "SUCCEEDED" }),
      generationJob({ id: "d", status: "SUCCEEDED" }),
      generationJob({ id: "e", status: "PROCESSING" }),
      generationJob({ id: "f", status: "FAILED" }),
    ]);

    renderWithClient(
      <BatchControls cuts={cuts} generationMode="MOCK" sceneId="scene-1" />,
    );

    expect(
      screen.getByText("Images 4/6 done · 1 running · 1 failed"),
    ).toBeInTheDocument();
  });

  it("disables batch generation while the batch mutation is pending", async () => {
    const user = userEvent.setup();
    let received = 0;
    server.use(
      http.post("http://localhost:8000/api/scenes/scene-1/images", async () => {
        received += 1;
        await new Promise(() => {});
        return HttpResponse.json({});
      }),
    );

    renderWithClient(
      <BatchControls cuts={sixSucceeded()} generationMode="MOCK" sceneId="scene-1" />,
    );
    const button = screen.getByRole("button", { name: "Generate all images" });
    await user.click(button);

    expect(button).toBeDisabled();
    await user.click(button);
    expect(received).toBe(1);
  });

  it("sends the selected batch scenario only in Mock mode", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    server.use(
      http.post("http://localhost:8000/api/scenes/scene-1/images", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          id: "batch-1",
          kind: "IMAGE",
          requestedCount: 6,
          createdJobIds: [],
          skipped: [],
        });
      }),
    );

    renderWithClient(
      <BatchControls cuts={sixSucceeded()} generationMode="MOCK" sceneId="scene-1" />,
    );
    await user.selectOptions(
      screen.getByLabelText(/Mock scenario for batch/),
      "ALWAYS_FAIL",
    );
    await user.click(screen.getByRole("button", { name: "Generate all images" }));

    await vi.waitFor(() => expect(bodies).toEqual([{ mockScenario: "ALWAYS_FAIL" }]));
  });

  it("omits the scenario control and field in Live mode", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    server.use(
      http.post("http://localhost:8000/api/scenes/scene-1/images", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          id: "batch-1",
          kind: "IMAGE",
          requestedCount: 6,
          createdJobIds: [],
          skipped: [],
        });
      }),
    );

    renderWithClient(
      <BatchControls cuts={sixSucceeded()} generationMode="LIVE" sceneId="scene-1" />,
    );
    expect(screen.queryByLabelText(/Mock scenario for batch/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Generate all images" }));

    await vi.waitFor(() => expect(bodies).toEqual([{}]));
  });

  it("names every cut the batch skipped and why", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("http://localhost:8000/api/scenes/scene-1/images", () =>
        HttpResponse.json(
          {
            id: "batch-2",
            kind: "IMAGE",
            requestedCount: 6,
            createdJobIds: [],
            skipped: [
              { cutId: "cut-1", reason: "GENERATION_ALREADY_ACTIVE" },
              { cutId: "cut-2", reason: "GENERATION_ALREADY_ACTIVE" },
            ],
          },
          { status: 202 },
        ),
      ),
    );

    renderWithClient(
      <BatchControls cuts={sixSucceeded()} generationMode="MOCK" sceneId="scene-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Generate all images" }));

    const skipped = await screen.findByRole("status", { name: "Skipped image cuts" });
    expect(within(skipped).getByText("2 of 6 cuts were skipped")).toBeInTheDocument();
    expect(
      within(skipped).getByText("Cut 1 · a generation is already running"),
    ).toBeInTheDocument();
    expect(
      within(skipped).getByText("Cut 2 · a generation is already running"),
    ).toBeInTheDocument();
  });

  it("reports a skip it cannot fully interpret rather than dropping it", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("http://localhost:8000/api/scenes/scene-1/videos", () =>
        HttpResponse.json(
          {
            id: "batch-3",
            kind: "VIDEO",
            requestedCount: 6,
            createdJobIds: [],
            skipped: [
              { cutId: "cut-3", reason: "SELECTED_IMAGE_REQUIRED" },
              { cutId: "cut-4", reason: "SOMETHING_NEW" },
              { cutId: "cut-99", reason: "ARTIFACT_MODE_MISMATCH" },
            ],
          },
          { status: 202 },
        ),
      ),
    );

    renderWithClient(
      <BatchControls cuts={sixSucceeded()} generationMode="MOCK" sceneId="scene-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Generate all videos" }));

    const skipped = await screen.findByRole("status", { name: "Skipped video cuts" });
    expect(within(skipped).getByText("Cut 3 · no image is selected")).toBeInTheDocument();
    expect(within(skipped).getByText("Cut 4 · SOMETHING_NEW")).toBeInTheDocument();
    expect(
      within(skipped).getByText("An unlisted cut · the selected image came from the other mode"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Skipped image cuts" })).not.toBeInTheDocument();
  });

  it("says nothing about skipped cuts when every cut started", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("http://localhost:8000/api/scenes/scene-1/images", () =>
        HttpResponse.json({
          id: "batch-1",
          kind: "IMAGE",
          requestedCount: 6,
          createdJobIds: ["job-1"],
          skipped: [],
        }),
      ),
    );

    renderWithClient(
      <BatchControls cuts={sixSucceeded()} generationMode="MOCK" sceneId="scene-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Generate all images" }));

    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Generate all images" })).toBeEnabled(),
    );
    expect(screen.queryByRole("status", { name: "Skipped image cuts" })).not.toBeInTheDocument();
  });

  it("blocks the video batch until at least one image is selected", () => {
    renderWithClient(
      <BatchControls
        cuts={cutsWith([null, null, null, null, null, null])}
        generationMode="MOCK"
        sceneId="scene-1"
      />,
    );

    expect(screen.getByRole("button", { name: "Generate all videos" })).toBeDisabled();
    expect(screen.getByText("Generate images before batching videos.")).toBeInTheDocument();
  });
});

describe("CharacterPanel", () => {
  it("shows every scene character with its defining traits", () => {
    renderWithClient(<CharacterPanel profiles={twoProfiles()} />);

    expect(screen.getByText("Mina")).toBeInTheDocument();
    expect(screen.getByText("Jun")).toBeInTheDocument();
    expect(screen.getByText("dark brown long straight")).toBeInTheDocument();
    expect(screen.getByText("a worn canvas backpack")).toBeInTheDocument();
  });

  it("renders nothing when a scene has no character sheet", () => {
    const { container } = renderWithClient(<CharacterPanel profiles={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
