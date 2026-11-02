// Citation export (specs/006, X1-X3), driven through the real UI.
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, type Browser, type Page, test } from "@playwright/test";

const FIXTURES = process.env.E2E_FIXTURES ?? "";
const fixture = (name: string) => path.join(FIXTURES, name);

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

async function createProject(page: Page, name = "Export project"): Promise<string> {
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  return page.url();
}

async function uploadSample(page: Page) {
  await page.getByTestId("upload-input").setInputFiles(fixture("multipage.pdf"));
  await expect(page.getByTestId("paper-row").getByTestId("job-status")).toHaveAttribute(
    "data-status",
    "succeeded",
  );
}

/** Give the uploaded paper a title and authors through the metadata form. */
async function describe(page: Page, projectUrl: string, title: string, authors: string) {
  await page.getByTestId("paper-row").getByRole("link").first().click();
  // The form is rebuilt when the paper finishes loading; typing before that is lost.
  await expect(page.getByTestId("page-indicator")).toBeVisible();
  await page.getByLabel(/^标题/).fill(title);
  await page.getByLabel(/^作者/).fill(authors);
  await page.getByLabel("年份").fill("2021");
  await page.getByRole("button", { name: "保存信息" }).click();
  await expect(page.getByLabel(/^标题（手动填写）/)).toHaveValue(title);
  await page.reload();
  await expect(page.getByLabel(/^标题/)).toHaveValue(title);
  await page.goto(projectUrl);
}

async function downloaded(page: Page, button: string) {
  const pending = page.waitForEvent("download");
  await page.getByTestId(button).click();
  const download = await pending;
  return {
    name: download.suggestedFilename(),
    text: readFileSync((await download.path())!, "utf8"),
  };
}

test("X1: BibTeX and RIS download with the paper's real metadata", async ({ page }) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await uploadSample(page);
  await describe(page, projectUrl, "Graph Methods for Labs", "Ada Lovelace, Alan Turing");

  const bib = await downloaded(page, "export-bibtex");
  expect(bib.name).toBe("references.bib");
  expect(bib.text).toContain("@misc{lovelace2021graph,");
  expect(bib.text).toContain("title = {{Graph Methods for Labs}}");
  expect(bib.text).toContain("author = {Ada Lovelace and Alan Turing}");
  expect(bib.text).toContain("year = {2021}");

  const ris = await downloaded(page, "export-ris");
  expect(ris.name).toBe("references.ris");
  expect(ris.text).toContain("TY  - GEN");
  expect(ris.text).toContain("T1  - Graph Methods for Labs");
  expect(ris.text).toContain("AU  - Ada Lovelace");
  expect(ris.text).toContain("AU  - Alan Turing");
  expect(ris.text).toContain("PY  - 2021");
  expect(ris.text).toContain("ER  - ");
});

test("X2: no papers disables export, and a paper without a title explains instead of downloading", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await expect(page.getByTestId("export-bibtex")).toBeDisabled();
  await expect(page.getByTestId("export-ris")).toBeDisabled();
  await expect(page.getByTestId("export-citations")).toContainText("没有可导出的内容");

  await uploadSample(page);
  await expect(page.getByTestId("export-bibtex")).toBeEnabled();
  let downloads = 0;
  page.on("download", () => (downloads += 1));
  await page.getByTestId("export-bibtex").click();
  await expect(page.locator(".notice.error[role='alert']")).toContainText(
    "没有带标题的文献可以导出",
  );
  expect(downloads).toBe(0);
});

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

test("X3: a viewer can export, and markup or braces in a title stay plain text in the file", async ({
  browser,
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await uploadSample(page);
  const nasty = "<img src=x onerror=alert(1)> } { @misc{x,";
  await describe(page, projectUrl, nasty, "Zed");

  const viewer = await viewerOf(browser, page, projectUrl);
  let dialogs = 0;
  viewer.page.on("dialog", async (dialog) => {
    dialogs += 1;
    await dialog.dismiss();
  });
  await viewer.page.goto(projectUrl);
  const bib = await downloaded(viewer.page, "export-bibtex");
  expect(bib.text).toContain("<img src=x onerror=alert(1)>");
  expect(bib.text).toContain("\\textbraceright{}");
  expect(bib.text).toContain("\\textbraceleft{}");
  expect(bib.text.match(/^@misc\{/gm)).toHaveLength(1);
  await expect(viewer.page.locator("img[src='x']")).toHaveCount(0);
  expect(dialogs).toBe(0);
  await viewer.context.close();
});
