import { http, HttpResponse } from "msw";
import { act, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SceneWorkspace } from "./SceneWorkspace";
import { generationJob, sceneDetail } from "../../test/fixtures";
import { createTestQueryClient, renderWithClient } from "../../test/render";
import { server } from "../../test/server";

describe("SceneWorkspace", () => {
  it("renders six accessible cuts in story order", async () => {
    const scene = sceneDetail({ cuts: [...sceneDetail().cuts].reverse() });
    server.use(
      http.get("http://localhost:8000/api/scenes/scene-1", () =>
        HttpResponse.json(scene),
      ),
    );

    renderWithClient(<SceneWorkspace sceneId="scene-1" generationMode="LIVE" />);

    const cuts = await screen.findAllByRole("region", { name: /^Cut \d$/ });
    expect(cuts).toHaveLength(6);
    expect(cuts.map((cut) => within(cut).getByRole("heading", { level: 3 }).textContent)).toEqual([
      "Cut 1",
      "Cut 2",
      "Cut 3",
      "Cut 4",
      "Cut 5",
      "Cut 6",
    ]);
    expect(screen.queryByLabelText(/Mock scenario/)).not.toBeInTheDocument();
  });

  it("polls at one second while active and stops after the job becomes terminal", async () => {
    vi.useFakeTimers();
    try {
      let receivedSceneRequests = 0;
      const activeScene = sceneDetail();
      activeScene.cuts[0] = {
        ...activeScene.cuts[0],
        imageJobs: [generationJob({ status: "PROCESSING" })],
      };
      const terminalScene = sceneDetail();
      terminalScene.cuts[0] = {
        ...terminalScene.cuts[0],
        imageJobs: [generationJob({ status: "SUCCEEDED" })],
      };
      server.use(
        http.get("http://localhost:8000/api/scenes/scene-1", () => {
          receivedSceneRequests += 1;
          return HttpResponse.json(terminalScene);
        }),
      );
      const queryClient = createTestQueryClient();
      queryClient.setQueryDefaults(["scene", "scene-1"], { staleTime: Infinity });
      queryClient.setQueryData(["scene", "scene-1"], activeScene);

      renderWithClient(
        <SceneWorkspace sceneId="scene-1" generationMode="LIVE" />,
        queryClient,
      );
      expect(screen.getByText(/Processing.*1\/3 attempts/)).toBeInTheDocument();

      await act(async () => vi.advanceTimersByTimeAsync(999));
      expect(receivedSceneRequests).toBe(0);

      await act(async () => vi.advanceTimersByTimeAsync(1));
      await vi.waitFor(() => {
        expect(receivedSceneRequests).toBe(1);
        expect(screen.getByText(/Succeeded.*1\/3 attempts/)).toBeInTheDocument();
      });

      await act(async () => vi.advanceTimersByTimeAsync(3000));
      expect(receivedSceneRequests).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
