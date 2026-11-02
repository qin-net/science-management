// Cited questions (specs/007, Y1-Y4), driven through the real UI. The language model is a local
// stand-in (backend/tests/fake_model.py) steered through the control URL from scripts/e2e.py.
import { randomUUID } from "node:crypto";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

const FIXTURES = process.env.E2E_FIXTURES ?? "";
const CONTROL = process.env.E2E_CONTROL ?? "";
const fixture = (name: string) => path.join(FIXTURES, name);

async function control(page: Page, route: string): Promise<{ count?: number }> {
  const response = await page.request.get(`${CONTROL}${route}`);
  expect(response.ok(), `control ${route}`).toBeTruthy();
  return response.json();
}

async function signUp(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "没有账号？去注册" }).click();
  await page.getByLabel("显示名称").fill("E2E User");
  await page.getByLabel("邮箱").fill(`e2e-${randomUUID()}@example.com`);
  await page.getByLabel(/密码/).fill("correct horse battery");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("heading", { name: "新建研究项目" })).toBeVisible();
}

async function projectWithPaper(page: Page, file: string) {
  await signUp(page);
  const name = "Ask project";
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  await page.getByTestId("upload-input").setInputFiles(fixture(file));
  await expect(page.getByTestId("paper-row").getByTestId("job-status")).toHaveAttribute(
    "data-status",
    "succeeded",
  );
}

async function ask(page: Page, question = "What is studied?") {
  await page.getByTestId("ask-question").fill(question);
  await page.getByTestId("ask-submit").click();
}

test("Y1: a cited answer shows its evidence and opens the reader on that block", async ({
  page,
}) => {
  await control(page, "/model?mode=ok");
  await projectWithPaper(page, "multipage.pdf");
  await ask(page);

  const segments = page.getByTestId("ask-segment");
  await expect(segments).toHaveCount(2);
  await expect(segments.first()).toContainText("Answer with evidence.");
  await expect(page.getByTestId("ask-dropped")).toContainText("1");
  await expect(segments.nth(1).getByTestId("ask-unsupported")).toBeVisible();

  await segments.first().getByText(/有依据/).click();
  await expect(segments.first().getByTestId("ask-evidence-text")).toContainText("Marker text");
  await expect(segments.first()).toContainText("第 1 页");

  await segments.first().getByTestId("ask-evidence-link").click();
  await expect(page.getByTestId("page-indicator")).toBeVisible();
  await expect(page.locator('[data-testid="block"].selected')).toHaveCount(1);
});

test("Y1: an answer with no basis says so", async ({ page }) => {
  await control(page, "/model?mode=unanswerable");
  await projectWithPaper(page, "multipage.pdf");
  await ask(page);
  await expect(page.getByTestId("ask-unanswerable")).toBeVisible();
  await expect(page.getByTestId("ask-segment")).toHaveCount(0);
});

test("Y2: a failing or busy model is reported, not hidden", async ({ page }) => {
  await control(page, "/model?mode=down");
  await projectWithPaper(page, "multipage.pdf");
  await ask(page);
  await expect(page.getByTestId("ask-error")).toContainText("模型服务暂时不可用");
  await expect(page.getByTestId("ask-answer")).toHaveCount(0);

  await control(page, "/model?mode=busy");
  await ask(page);
  await expect(page.getByTestId("ask-error")).toContainText("繁忙");
  await expect(page.getByTestId("ask-answer")).toHaveCount(0);
});

test("Y3: markup in the answer and in the paper stays text", async ({ page }) => {
  await control(page, "/model?mode=hostile");
  await projectWithPaper(page, "hostile.pdf");
  await ask(page);
  const segment = page.getByTestId("ask-segment").first();
  await expect(segment).toContainText("<img src=x onerror=window.__pwned=1>claim");
  await segment.getByText(/有依据/).click();
  await expect(segment.getByTestId("ask-evidence-text")).toContainText("<img src=x");
  await expect(page.getByTestId("ask-panel").locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBe(
    undefined,
  );
});

test("Y4: the panel says who receives the question", async ({ page }) => {
  await control(page, "/model?mode=ok");
  await projectWithPaper(page, "multipage.pdf");
  await expect(page.getByTestId("ask-privacy")).toContainText("Anthropic");
  const seen = (await control(page, "/model-seen")).count;
  expect(seen).toBe(0); // nothing is sent until someone asks
});
