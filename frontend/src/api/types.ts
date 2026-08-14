export type GenerationMode = "MOCK" | "LIVE";

export type MockScenario =
  | "SUCCESS"
  | "FAIL_TWICE_THEN_SUCCEED"
  | "ALWAYS_FAIL";

export type GenerationKind = "IMAGE" | "VIDEO";

export type JobStatus =
  | "QUEUED"
  | "SUBMITTING"
  | "PROCESSING"
  | "RETRY_WAIT"
  | "SUCCEEDED"
  | "FAILED";

export interface Config {
  generationMode: GenerationMode;
}

export interface GenerationJob {
  id: string;
  kind: GenerationKind;
  version: number;
  status: JobStatus;
  prompt: string;
  sourceImageId: string | null;
  attemptCount: number;
  maxAttempts: number;
  nextRunAt: string | null;
  lastErrorCode: string | null;
  lastErrorMessage: string | null;
  mockScenario: MockScenario | null;
}

export interface CutImage {
  id: string;
  generationJobId: string;
  url: string;
  inputPrompt: string;
  createdAt: string;
}

export interface CutVideo {
  id: string;
  cutImageId: string;
  generationJobId: string;
  url: string;
  inputPrompt: string;
  createdAt: string;
}

export interface Cut {
  id: string;
  order: number;
  imagePrompt: string;
  videoPrompt: string;
  durationSec: 5;
  selectedImageId: string | null;
  selectedVideoId: string | null;
  imageJobs: GenerationJob[];
  videoJobs: GenerationJob[];
  images: CutImage[];
  videos: CutVideo[];
}

export interface Scene {
  id: string;
  userPrompt: string;
  title: string;
  scenario: string;
  cuts: Cut[];
}

export interface ApiErrorBody {
  code: string;
  message: string;
}
