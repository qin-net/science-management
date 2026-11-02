// Paper summaries (specs/008, Z1-Z4), driven through the real UI against the local fake model.
import { randomUUID } from "node:crypto";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

const FIXTURES = process.env.E2E_FIXTURES ?? "";
const CONTROL = process.env.E2E_CONTROL ?? "";
const fixture = (name: string) => path.join(FIXTURES, name);

async function control(page: Page, route: string) {
  const response = await page.request.get(`${CONTROL}${route}`);
  expect(response.ok(), `control ${route}`).toBeTruthy();
}

async function openReader(page: Page, file: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: "没有账号？去注册" }).click();
  await page.getByLabel("显示名称").fill("E2E User");
  await page.getByLabel("邮箱").fill(`e2e-${randomUUID()}@example.com`);
  await page.getByLabel(/密码/).fill("correct horse battery");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("heading", { name: "新建研究项目" })).toBeVisible();
  const name = "Summary project";
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  await page.getByTestId("upload-input").setInputFiles(fixture(file));
  await expect(page.getByTestId("paper-row").getByTestId("job-status")).toHaveAttribute(
    "data-status",
    "succeeded",
  );
  await page.getByTestId("paper-row").getByRole("link").first().click();
  await expect(page.getByTestId("page-indicator")).toBeVisible();
}

test("Z1 and Z2: a cited summary shows its evidence and can be saved as a note", async ({
  page,
}) => {
  await control(page, "/model?mode=ok");
  await openReader(page, "multipage.pdf");
  await expect(page.getByTestId("summary-privacy")).toContainText("Anthropic");

  await page.getByTestId("summary-generate").click();
  const result = page.getByTestId("summary-result");
  await expect(result.getByTestId("ask-segment")).toHaveCount(2);
  await expect(result.getByTestId("ask-unsupported")).toHaveCount(1);
  await result.getByText(/有依据/).click();
  await expect(result.getByTestId("ask-evidence-text")).toContainText("Marker text");

  await page.getByTestId("summary-save").click();
  await expect(page.getByTestId("summary-save")).toHaveText("已存为笔记");
  const notes = page.getByTestId("note");
  await expect(notes).toHaveCount(1);
  await expect(notes.first()).toContainText("核心贡献总结（模型生成，请核对原文）");
  await expect(notes.first()).toContainText("Answer with evidence.（第 1 页）");
  await expect(notes.first()).toContainText("【无出处】Answer without a source.");

  await page.reload();
  await expect(page.getByTestId("note")).toHaveCount(1);
});

test("Z3: a failing model shows an error and offers nothing to save", async ({ page }) => {
  await control(page, "/model?mode=down");
  await openReader(page, "multipage.pdf");
  await page.getByTestId("summary-generate").click();
  await expect(page.getByTestId("summary-error")).toContainText("模型服务暂时不可用");
  await expect(page.getByTestId("summary-result")).toHaveCount(0);
  await expect(page.getByTestId("summary-save")).toHaveCount(0);
});

test("Z4: markup in the summary and in the saved note stays text", async ({ page }) => {
  await control(page, "/model?mode=hostile");
  await openReader(page, "hostile.pdf");
  await page.getByTestId("summary-generate").click();
  await expect(page.getByTestId("summary-result")).toContainText(
    "<img src=x onerror=window.__pwned=1>claim",
  );
  await page.getByTestId("summary-save").click();
  await expect(page.getByTestId("note")).toHaveCount(1);
  await expect(page.getByTestId("note").first()).toContainText("<img src=x");
  await expect(page.locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBe(
    undefined,
  );
});
