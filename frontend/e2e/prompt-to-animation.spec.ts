import { expect, test, type Locator, type Page } from "@playwright/test";

const CUT_ORDERS = [1, 2, 3, 4, 5, 6] as const;

function cutRegion(page: Page, order: number): Locator {
  return page.getByRole("region", { name: `Cut ${order}`, exact: true });
}

function generationJob(cut: Locator, kind: "Image" | "Video", version: number): Locator {
  return cut.getByRole("article", { name: `${kind} generation v${version}`, exact: true });
}

async function chooseScenario(cut: Locator, order: number, scenario: string): Promise<void> {
  await cut.getByLabel(`Mock scenario for Cut ${order}`).selectOption(scenario);
}

test("REQ-01..REQ-08: creates, retries, regenerates, restores, and plays a scene", async ({
  page,
}) => {
  // REQ-08: the startup mode is read-only and no runtime switch exists.
  await page.goto("/");
  await expect(page.getByText("Mock mode")).toBeVisible();
  await expect(page.getByRole("switch")).toHaveCount(0);

  // REQ-01, REQ-02: one prompt becomes exactly six five-second Cuts.
  await page
    .getByLabel("Animation prompt")
    .fill("A paper astronaut explores a watercolor moon");
  await page.getByRole("button", { name: "Create scene" }).click();
  await expect(page.getByRole("region", { name: /^Cut \d$/ })).toHaveCount(6);
  for (const order of CUT_ORDERS) {
    await expect(cutRegion(page, order).getByText("5 sec")).toBeVisible();
  }
  const sceneId = new URL(page.url()).searchParams.get("scene");
  expect(sceneId).toBeTruthy();

  // REQ-05: a retryable failure is retried and the third attempt succeeds.
  const first = cutRegion(page, 1);
  await chooseScenario(first, 1, "FAIL_TWICE_THEN_SUCCEED");
  await first.getByRole("button", { name: "Generate image" }).click();
  await expect(generationJob(first, "Image", 1).getByText("Succeeded · 3/3 attempts")).toBeVisible();

  // REQ-03: every Cut gets an image, and the first success is selected.
  for (const order of CUT_ORDERS.slice(1)) {
    const cut = cutRegion(page, order);
    await chooseScenario(cut, order, "SUCCESS");
    await cut.getByRole("button", { name: "Generate image" }).click();
    await expect(generationJob(cut, "Image", 1).getByText("Succeeded · 1/3 attempts")).toBeVisible();
  }
  for (const order of CUT_ORDERS) {
    await expect(cutRegion(page, order).getByText("Selected").first()).toBeVisible();
  }

  // REQ-05: attempts are exhausted and the job reaches a stable final failure.
  await chooseScenario(first, 1, "ALWAYS_FAIL");
  await first.getByRole("button", { name: "Generate video" }).click();
  await expect(generationJob(first, "Video", 1).getByText("Failed after 3/3 attempts")).toBeVisible();

  // REQ-06: regeneration adds the next version and preserves the failed history.
  await chooseScenario(first, 1, "SUCCESS");
  await first.getByRole("button", { name: "Regenerate video" }).click();
  await expect(generationJob(first, "Video", 2).getByText("Succeeded · 1/3 attempts")).toBeVisible();
  await expect(generationJob(first, "Video", 1).getByText("Failed after 3/3 attempts")).toBeVisible();

  // REQ-04: the remaining videos are generated from each Cut's selected image.
  for (const order of CUT_ORDERS.slice(1)) {
    const cut = cutRegion(page, order);
    await cut.getByRole("button", { name: "Generate video" }).click();
    await expect(generationJob(cut, "Video", 1).getByText("Succeeded · 1/3 attempts")).toBeVisible();
    // Lineage: the video job records the image version it was generated from.
    await expect(generationJob(cut, "Video", 1)).toContainText("Source image");
    await expect(generationJob(cut, "Video", 1)).toContainText("Image v1");
  }

  // REQ-07: the player unlocks only once all six compatible videos are selected.
  await expect(page.getByText("6 of 6 videos ready")).toBeVisible();
  const play = page.getByRole("button", { name: "Play sequence" });
  await expect(play).toBeEnabled();

  // The Scene id lives in the URL, so a reload restores the same workspace.
  await page.reload();
  await expect(page.getByRole("region", { name: /^Cut \d$/ })).toHaveCount(6);
  expect(new URL(page.url()).searchParams.get("scene")).toBe(sceneId);
  await expect(page.getByText("6 of 6 videos ready")).toBeVisible();

  // REQ-07: six five-second videos play in Cut order and end on a restartable state.
  await expect(page.getByText("Cut 1 of 6")).toBeVisible();
  await page.getByRole("button", { name: "Play sequence" }).click();
  await expect(page.getByRole("button", { name: "Pause sequence" })).toBeVisible();
  for (const order of CUT_ORDERS.slice(1)) {
    await expect(page.getByText(`Cut ${order} of 6`)).toBeVisible({ timeout: 20_000 });
  }
  await expect(page.getByRole("button", { name: "Restart sequence" })).toBeEnabled({
    timeout: 20_000,
  });
  await expect(page.getByText("Playback could not start")).toHaveCount(0);
  await expect(page.getByText("Cut video could not be loaded")).toHaveCount(0);
});
