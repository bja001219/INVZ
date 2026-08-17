import type {
  CharacterProfile,
  Cut,
  CutImage,
  CutVideo,
  GenerationJob,
  Scene,
} from "../api/types";

const timestamp = "2026-08-14T12:00:00Z";

export function twoProfiles(): CharacterProfile[] {
  return [
    {
      name: "Mina",
      role: "female lead high-school student",
      ageRange: "17",
      hairColor: "dark brown",
      hairStyle: "long straight",
      outfit: "navy sailor uniform with a red ribbon",
      build: "slim",
      faceImpression: "soft calm expression",
      signatureProp: "a stack of library books",
    },
    {
      name: "Jun",
      role: "male lead high-school student",
      ageRange: "17",
      hairColor: "black",
      hairStyle: "short messy",
      outfit: "grey blazer uniform",
      build: "tall lean",
      faceImpression: "bright open smile",
      signatureProp: "a worn canvas backpack",
    },
  ];
}

export function generationJob(
  overrides: Partial<GenerationJob> = {},
): GenerationJob {
  return {
    id: "image-job-1",
    kind: "IMAGE",
    version: 1,
    status: "SUCCEEDED",
    prompt: "A silver rocket waits on the moon",
    generationMode: "MOCK",
    sourceImageId: null,
    referenceImageId: null,
    batchId: null,
    waitingForAnchor: false,
    attemptCount: 1,
    maxAttempts: 3,
    nextRunAt: null,
    lastErrorCode: null,
    lastErrorMessage: null,
    mockScenario: "SUCCESS",
    ...overrides,
  };
}

export function cutDetail(overrides: Partial<Cut> = {}): Cut {
  return {
    id: "cut-1",
    order: 1,
    shotDescription: "The two leads meet at the school gate",
    videoMotion: "slow push in",
    imagePrompt: "A silver rocket waits on the moon",
    videoPrompt: "The rocket rises through a field of stars",
    durationSec: 5,
    selectedImageId: null,
    selectedVideoId: null,
    imageJobs: [],
    videoJobs: [],
    images: [],
    videos: [],
    ...overrides,
  };
}

export function sceneDetail(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "scene-1",
    userPrompt: "moon voyage",
    title: "Moon Voyage",
    scenario: "A lone rocket crosses the night sky.",
    characterProfiles: twoProfiles(),
    cuts: Array.from({ length: 6 }, (_, index) =>
      cutDetail({ id: `cut-${index + 1}`, order: index + 1 }),
    ),
    ...overrides,
  };
}

export function cutWithoutImage(): Cut {
  return cutDetail();
}

export function cutWithFailedVideo(
  overrides: Pick<GenerationJob, "attemptCount" | "maxAttempts">,
): Cut {
  const imageJob = generationJob();
  const image: CutImage = {
    id: "image-1",
    generationJobId: imageJob.id,
    url: "/media/mock/cut-image.png",
    inputPrompt: imageJob.prompt,
    createdAt: timestamp,
  };
  return cutDetail({
    selectedImageId: image.id,
    imageJobs: [imageJob],
    images: [image],
    videoJobs: [
      generationJob({
        id: "video-job-1",
        kind: "VIDEO",
        version: 1,
        status: "FAILED",
        prompt: "The rocket rises through a field of stars",
        sourceImageId: image.id,
        lastErrorCode: "GENERATION_PROVIDER_FAILED",
        lastErrorMessage: "Generation provider failed",
        ...overrides,
      }),
    ],
  });
}

/**
 * The scene anchor as the backend actually links it: Cut 1 owns the image and a later cut's job
 * references it across the cut boundary, so a same-cut lookup cannot resolve the label.
 */
export function sceneWithAnchorReference(): Scene {
  const anchorJob = generationJob({ id: "image-job-1", version: 1 });
  const anchorImage: CutImage = {
    id: "image-1",
    generationJobId: anchorJob.id,
    url: "/media/mock/cut-image.png",
    inputPrompt: anchorJob.prompt,
    createdAt: timestamp,
  };
  return sceneDetail({
    cuts: [
      cutDetail({
        id: "cut-1",
        order: 1,
        selectedImageId: anchorImage.id,
        imageJobs: [anchorJob],
        images: [anchorImage],
      }),
      cutDetail({
        id: "cut-3",
        order: 3,
        shotDescription: "The two leads share an umbrella",
        imageJobs: [
          generationJob({
            id: "image-job-3",
            version: 1,
            generationMode: "LIVE",
            referenceImageId: anchorImage.id,
          }),
        ],
      }),
    ],
  });
}

export function cutWithImageAndVideoVersions(): Cut {
  const imageJobOne = generationJob();
  const imageJobTwo = generationJob({ id: "image-job-2", version: 2 });
  const imageOne: CutImage = {
    id: "image-1",
    generationJobId: imageJobOne.id,
    url: "/media/mock/cut-image.png",
    inputPrompt: imageJobOne.prompt,
    createdAt: timestamp,
  };
  const imageTwo: CutImage = {
    ...imageOne,
    id: "image-2",
    generationJobId: imageJobTwo.id,
  };
  const videoJob = generationJob({
    id: "video-job-1",
    kind: "VIDEO",
    sourceImageId: imageOne.id,
  });
  const video: CutVideo = {
    id: "video-1",
    cutImageId: imageOne.id,
    generationJobId: videoJob.id,
    url: "/media/mock/cut-video.mp4",
    inputPrompt: videoJob.prompt,
    createdAt: timestamp,
  };
  return cutDetail({
    selectedImageId: imageOne.id,
    selectedVideoId: video.id,
    imageJobs: [imageJobTwo, imageJobOne],
    videoJobs: [videoJob],
    images: [imageTwo, imageOne],
    videos: [video],
  });
}
