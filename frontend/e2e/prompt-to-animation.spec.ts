import { expect, test, type Locator, type Page } from "@playwright/test";

const CUT_ORDERS = [1, 2, 3, 4, 5, 6] as const;

function cutRegion(page: Page, order: number): Locator {
  return page.getByRole("region", { name: `Cut ${order}`, exact: true });
}

function generationJob(cut: Locator, kind: "Image" | "Video", version: number): Locator {
  return cut.getByRole("article", { name: `${kind} generation v${version}`, exact: true });
}

async function chooseCutScenario(cut: Locator, order: number, scenario: string): Promise<void> {
  await cut.getByLabel(`Mock scenario for Cut ${order}`).selectOption(scenario);
}

test("REQ-01..REQ-17: batches six consistent cuts, retries, regenerates, restores, and plays", async ({
  page,
}) => {
  // REQ-08, REQ-15: the mode is visible and switchable. Whether Live is selectable depends on
  // the operator's keys, so only the current-mode invariant is asserted here; the
  // unavailable-Live case is covered by the App unit tests.
  await page.goto("/");
  await expect(page.getByText("Mock mode")).toBeVisible();
  await expect(page.getByRole("group", { name: "Generation mode" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mock" })).toBeDisabled();

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

  // REQ-13: the scene declares a recurring cast, shown to the user.
  const cast = page.getByRole("region", { name: "Characters in every cut" });
  await expect(cast).toBeVisible();
  const leadName = (await cast.getByRole("heading", { level: 4 }).first().innerText()).trim();
  expect(leadName.length).toBeGreaterThan(0);

  // REQ-12: one batch enqueues all six image jobs.
  await page.getByRole("button", { name: "Generate all images" }).click();
  await expect(page.getByText("Images 6/6 done")).toBeVisible({ timeout: 60_000 });

  // REQ-13: every cut prompt carries the same character sheet, and cuts 2-6 reference the anchor.
  for (const order of CUT_ORDERS) {
    const job = generationJob(cutRegion(page, order), "Image", 1);
    await expect(job).toContainText(leadName);
    await expect(job).toContainText("stylized 2D animation still");
    await expect(job).toContainText("Avoid: photorealistic rendering");
  }
  for (const order of CUT_ORDERS.slice(1)) {
    await expect(generationJob(cutRegion(page, order), "Image", 1)).toContainText("Reference");
  }
  await expect(generationJob(cutRegion(page, 1), "Image", 1)).not.toContainText("Reference");

  // REQ-05: attempts are exhausted and the job reaches a stable final failure.
  const first = cutRegion(page, 1);
  await chooseCutScenario(first, 1, "ALWAYS_FAIL");
  await first.getByRole("button", { name: "Generate video" }).click();
  await expect(generationJob(first, "Video", 1).getByText("Failed after 3/3 attempts")).toBeVisible();

  // REQ-06: regeneration adds the next version and preserves the failed history.
  await chooseCutScenario(first, 1, "SUCCESS");
  await first.getByRole("button", { name: "Regenerate video" }).click();
  await expect(generationJob(first, "Video", 2).getByText("Succeeded · 1/3 attempts")).toBeVisible();
  await expect(generationJob(first, "Video", 1).getByText("Failed after 3/3 attempts")).toBeVisible();

  // REQ-04, REQ-12: one batch finishes the remaining videos from each Cut's selected image.
  await page.getByRole("button", { name: "Generate all videos" }).click();
  await expect(page.getByText("Videos 6/6 done")).toBeVisible({ timeout: 60_000 });
  for (const order of CUT_ORDERS.slice(1)) {
    await expect(generationJob(cutRegion(page, order), "Video", 1)).toContainText("Source image");
  }

  // REQ-17: each job records the mode it ran under.
  await expect(generationJob(cutRegion(page, 2), "Image", 1).getByText("MOCK")).toBeVisible();

  // REQ-07: the player unlocks only once all six compatible videos are selected.
  await expect(page.getByText("6 of 6 videos ready")).toBeVisible();
  await expect(page.getByRole("button", { name: "Play sequence" })).toBeEnabled();

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

test("REQ-13: a cut generated outside a batch holds for the scene anchor", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Animation prompt").fill("A rainy rooftop goodbye between two friends");
  await page.getByRole("button", { name: "Create scene" }).click();
  await expect(page.getByRole("region", { name: /^Cut \d$/ })).toHaveCount(6);

  // Cut 1 was never requested, so Cut 3 has no anchor to match. It must say so rather than
  // quietly generate a character nobody else in the scene resembles.
  const third = cutRegion(page, 3);
  await third.getByRole("button", { name: "Generate image" }).click();

  const held = generationJob(third, "Image", 1);
  await expect(held).toContainText("Waiting for the Cut 1 image");
  await expect(held.getByText(/^Queued/)).toBeVisible();
});

test("REQ-16: a Mock generation can complete through the webhook instead of polling", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("Animation prompt")
    .fill("A quiet library afternoon between two students");
  await page.getByRole("button", { name: "Create scene" }).click();
  await expect(page.getByRole("region", { name: /^Cut \d$/ })).toHaveCount(6);

  const first = cutRegion(page, 1);
  await chooseCutScenario(first, 1, "SUCCEED_VIA_WEBHOOK");
  await first.getByRole("button", { name: "Generate image" }).click();

  // Mock polling for this scenario never returns success, so only a delivered callback can
  // move the job to SUCCEEDED.
  await expect(generationJob(first, "Image", 1).getByText(/^Succeeded/)).toBeVisible({
    timeout: 60_000,
  });
});
