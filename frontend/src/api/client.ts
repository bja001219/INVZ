import type {
  ApiErrorBody,
  Config,
  GenerationJob,
  GenerationKind,
  MockScenario,
  Scene,
} from "./types";

const fallbackMessage = "Request failed. Please try again.";

export const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  return typeof body.code === "string" && typeof body.message === "string";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, init);
  } catch {
    throw new ApiError("REQUEST_FAILED", fallbackMessage);
  }

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ApiError("REQUEST_FAILED", fallbackMessage);
    }
    if (isApiErrorBody(body)) throw new ApiError(body.code, body.message);
    throw new ApiError("REQUEST_FAILED", fallbackMessage);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("RESPONSE_INVALID", fallbackMessage);
  }
}

function jsonRequest(method: "POST" | "PUT", body: object): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  getConfig: () => request<Config>("/api/config"),
  createScene: (prompt: string) =>
    request<Scene>("/api/scenes", jsonRequest("POST", { prompt })),
  getScene: (sceneId: string) =>
    request<Scene>(`/api/scenes/${encodeURIComponent(sceneId)}`),
  createGeneration: (
    cutId: string,
    kind: GenerationKind,
    mockScenario?: MockScenario,
  ) =>
    request<GenerationJob>(
      `/api/cuts/${encodeURIComponent(cutId)}/${kind === "IMAGE" ? "images" : "videos"}`,
      jsonRequest("POST", mockScenario ? { mockScenario } : {}),
    ),
  selectImage: (cutId: string, imageId: string) =>
    request<Scene>(
      `/api/cuts/${encodeURIComponent(cutId)}/selected-image`,
      jsonRequest("PUT", { imageId }),
    ),
  selectVideo: (cutId: string, videoId: string) =>
    request<Scene>(
      `/api/cuts/${encodeURIComponent(cutId)}/selected-video`,
      jsonRequest("PUT", { videoId }),
    ),
};

export function resolveMediaUrl(url: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  return new URL(url, `${apiBaseUrl}/`).href;
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : fallbackMessage;
}
