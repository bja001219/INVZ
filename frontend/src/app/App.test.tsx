import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { sceneDetail } from "../test/fixtures";
import { renderWithClient } from "../test/render";
import { server } from "../test/server";

const apiBase = "http://localhost:8000";

function renderApp(options: {
  config?: { generationMode: "MOCK" | "LIVE" };
  scene?: ReturnType<typeof sceneDetail>;
} = {}) {
  const receivedSceneIds: string[] = [];
  const config = options.config ?? { generationMode: "MOCK" as const };
  const scene = options.scene ?? sceneDetail();

  server.use(
    http.get(`${apiBase}/api/config`, () => HttpResponse.json(config)),
    http.get(`${apiBase}/api/scenes/:sceneId`, ({ params }) => {
      receivedSceneIds.push(String(params.sceneId));
      return HttpResponse.json(scene);
    }),
    http.post(`${apiBase}/api/scenes`, () => HttpResponse.json(scene, { status: 201 })),
  );

  return { receivedSceneIds, ...renderWithClient(<App />) };
}

describe("App", () => {
  it("shows startup mode without a switch", async () => {
    renderApp({ config: { generationMode: "MOCK" } });

    expect(await screen.findByText("Mock mode")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("restores the scene from the URL after reload", async () => {
    window.history.replaceState({}, "", "/?scene=scene-1");
    const { receivedSceneIds } = renderApp({ scene: sceneDetail({ id: "scene-1" }) });

    expect(await screen.findByText("Moon Voyage")).toBeInTheDocument();
    expect(receivedSceneIds).toEqual(["scene-1"]);
  });

  it("writes a newly created scene id to the URL", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.type(screen.getByLabelText("Animation prompt"), "moon voyage");
    await user.click(screen.getByRole("button", { name: "Create scene" }));

    expect(await screen.findByText("Moon Voyage")).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get("scene")).toBe("scene-1");
  });

  it("follows the URL when browser history changes", async () => {
    window.history.replaceState({}, "", "/?scene=scene-1");
    const { receivedSceneIds } = renderApp();
    await screen.findByText("Moon Voyage");

    window.history.pushState({}, "", "/?scene=scene-2");
    window.dispatchEvent(new PopStateEvent("popstate"));

    await waitFor(() => expect(receivedSceneIds).toEqual(["scene-1", "scene-2"]));
    expect(await screen.findByText("Moon Voyage")).toBeInTheDocument();
  });

  it("shows only a stable API message when scene creation fails", async () => {
    const user = userEvent.setup();
    renderApp();
    server.use(
      http.post(`${apiBase}/api/scenes`, () =>
        HttpResponse.json(
          { code: "SCENE_PROVIDER_FAILED", message: "Scene provider failed" },
          { status: 502 },
        ),
      ),
    );

    await user.type(screen.getByLabelText("Animation prompt"), "moon voyage");
    await user.click(screen.getByRole("button", { name: "Create scene" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Scene provider failed");
  });
});
