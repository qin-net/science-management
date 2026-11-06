// Block translations (specs/012, W1-W4), driven through the real UI against the local fake model.
import { randomUUID } from "node:crypto";
import path from "node:path";

import { expect, type Browser, type Page, test } from "@playwright/test";

const FIXTURES = process.env.E2E_FIXTURES ?? "";
const CONTROL = process.env.E2E_CONTROL ?? "";
const fixture = (name: string) => path.join(FIXTURES, name);

async function control(page: Page, route: string): Promise<{ count?: number }> {
  const response = await page.request.get(`${CONTROL}${route}`);
  expect(response.ok(), `control ${route}`).toBeTruthy();
  return response.json();
}

async function signUp(page: Page, name = "E2E User"): Promise<string> {
  const email = `e2e-${randomUUID()}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "没有账号？去注册" }).click();
  await page.getByLabel("显示名称").fill(name);
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel(/密码/).fill("correct horse battery");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("heading", { name: "新建研究项目" })).toBeVisible();
  return email;
}

/** Signs up, creates a project, uploads the file and opens the reader; returns the project URL. */
async function openReader(page: Page, file: string): Promise<string> {
  await signUp(page);
  const name = "Translation project";
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  const projectUrl = page.url();
  await page.getByTestId("upload-input").setInputFiles(fixture(file));
  const row = page.getByTestId("paper-row").first();
  await expect(row.getByTestId("job-status")).toHaveAttribute("data-status", "succeeded");
  await row.getByRole("link").click();
  await expect(page.getByTestId("page-indicator")).toBeVisible();
  return projectUrl;
}

async function viewerOf(browser: Browser, owner: Page, projectUrl: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = await signUp(page, "Viewer");
  await owner.goto(projectUrl);
  await owner.getByLabel("添加已注册用户（邮箱）").fill(email);
  await owner.locator('select[name="role"]').selectOption("viewer");
  await owner.getByRole("button", { name: "添加成员" }).click();
  await expect(owner.getByRole("listitem").filter({ hasText: email })).toBeVisible();
  return { context, page };
}

const translations = (page: Page) => page.getByTestId("block-translation");

test("W1: page 1 is translated under its block and a reload does not pay again", async ({
  page,
}) => {
  await control(page, "/model?mode=translate");
  await openReader(page, "multipage.pdf");
  await expect(page.getByTestId("translation-privacy")).toContainText("Anthropic");
  await expect(translations(page)).toHaveCount(0);

  await page.getByTestId("translation-run").click();
  await expect(translations(page)).toHaveCount(1);
  await expect(translations(page)).toContainText("机器翻译");
  await expect(translations(page)).toContainText("标记文本（译文）");
  // The source text is untouched and the translation sits under page 1's block.
  await expect(page.getByTestId("block").first()).toHaveText("Marker text 1");
  await expect(page.getByTestId("translation-run")).toBeDisabled();
  await expect(page.getByTestId("translation-done")).toBeVisible();
  expect((await control(page, "/model-seen")).count).toBe(1);

  await page.reload();
  await expect(translations(page)).toHaveCount(1);
  await expect(translations(page)).toContainText("标记文本（译文）");
  expect((await control(page, "/model-seen")).count).toBe(1);

  // Another language is a separate translation, and there is none yet.
  await page.getByLabel("译文语言").selectOption("en");
  await expect(translations(page)).toHaveCount(0);
  await expect(page.getByTestId("translation-run")).toBeEnabled();
});

test("W2: a viewer reads existing translations and has no way to start one", async ({
  page,
  browser,
}) => {
  await control(page, "/model?mode=translate");
  const projectUrl = await openReader(page, "multipage.pdf");
  await page.getByTestId("translation-run").click();
  await expect(translations(page)).toHaveCount(1);
  const paperUrl = page.url();

  const { context, page: viewer } = await viewerOf(browser, page, projectUrl);
  await viewer.goto(paperUrl);
  await expect(translations(viewer)).toHaveCount(1);
  await expect(translations(viewer)).toContainText("标记文本（译文）");
  await expect(viewer.getByTestId("translation-panel")).toBeVisible();
  await expect(viewer.getByTestId("translation-run")).toHaveCount(0);
  await expect(viewer.getByTestId("translation-privacy")).toHaveCount(0);
  await context.close();
});

test("W3: a failing model shows an error and no translation", async ({ page }) => {
  await control(page, "/model?mode=down");
  await openReader(page, "multipage.pdf");
  await page.getByTestId("translation-run").click();
  await expect(page.getByTestId("translation-error")).toContainText("模型服务暂时不可用");
  await expect(translations(page)).toHaveCount(0);
  await expect(page.getByTestId("translation-run")).toBeEnabled();
});

test("W4: markup in a translation stays text", async ({ page }) => {
  await control(page, "/model?mode=translate_hostile");
  await openReader(page, "multipage.pdf");
  await page.getByTestId("translation-run").click();
  await expect(translations(page)).toContainText("<img src=x onerror=window.__pwned=1>译文");
  await expect(page.locator(".reader-pane.left img")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBe(
    undefined,
  );
});
