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
  config?: { generationMode: "MOCK" | "LIVE"; liveAvailable: boolean };
  scene?: ReturnType<typeof sceneDetail>;
} = {}) {
  const receivedSceneIds: string[] = [];
  const config = options.config ?? {
    generationMode: "MOCK" as const,
    liveAvailable: false,
  };
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
  it("shows the current mode and offers Live only when the backend has keys", async () => {
    renderApp({ config: { generationMode: "MOCK", liveAvailable: false } });

    expect(await screen.findByText("Mock mode")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Live" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Mock" })).toBeDisabled();
    expect(screen.getByText("Live needs backend API keys")).toBeInTheDocument();
  });

  it("switches the runtime mode through the backend", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    renderApp({ config: { generationMode: "MOCK", liveAvailable: true } });
    server.use(
      http.put(`${apiBase}/api/config`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ generationMode: "LIVE", liveAvailable: true });
      }),
    );

    await user.click(await screen.findByRole("button", { name: "Live" }));

    expect(await screen.findByText("Live mode")).toBeInTheDocument();
    expect(bodies).toEqual([{ generationMode: "LIVE" }]);
    expect(screen.getByRole("button", { name: "Live" })).toBeDisabled();
  });

  it("keeps the current mode and reports why a switch was refused", async () => {
    const user = userEvent.setup();
    renderApp({ config: { generationMode: "MOCK", liveAvailable: true } });
    server.use(
      http.put(`${apiBase}/api/config`, () =>
        HttpResponse.json(
          { code: "LIVE_MODE_UNAVAILABLE", message: "Live mode is not configured" },
          { status: 409 },
        ),
      ),
    );

    await user.click(await screen.findByRole("button", { name: "Live" }));

    expect(await screen.findByText("Live mode is not configured")).toBeInTheDocument();
    expect(screen.getByText("Mock mode")).toBeInTheDocument();
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
