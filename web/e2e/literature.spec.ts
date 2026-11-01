// Literature search and metadata import (specs/002, J1-J6 and G5), driven through the real UI.
// The three sources are a local stand-in that replays recorded responses; scripts/e2e.py runs it
// and exposes a small control URL so each test can make one source slow, limited or broken.
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

const setMode = (page: Page, source: string, mode: string) =>
  control(page, `/set?source=${source}&mode=${mode}`);

test.beforeEach(async ({ page }) => {
  await control(page, "/reset");
});

async function signUp(page: Page): Promise<string> {
  const email = `e2e-${randomUUID()}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "没有账号？去注册" }).click();
  await page.getByLabel("显示名称").fill("E2E User");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel(/密码/).fill("correct horse battery");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("heading", { name: "新建研究项目" })).toBeVisible();
  return email;
}

async function createProject(page: Page, name = "Literature project"): Promise<string> {
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  return page.url();
}

async function search(page: Page, query = "attention is all you need") {
  const panel = page.getByTestId("literature-search");
  await panel.getByLabel("关键词").fill(query);
  await panel.getByRole("button", { name: "检索", exact: true }).click();
}

const status = (page: Page, source: string) =>
  page.locator(`[data-testid="source-status"][data-source="${source}"]`);

const transformer = (page: Page) =>
  page.getByTestId("search-result").filter({ hasText: "arXiv 1706.03762" });

test("J1: all three sources are listed with status and time, results are merged and labeled", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await search(page);

  await expect(page.getByTestId("search-response")).toHaveAttribute("data-outcome", "results");
  for (const source of ["arxiv", "openalex", "crossref"]) {
    await expect(status(page, source)).toHaveAttribute("data-status", "ok");
    await expect(status(page, source).getByTestId("source-duration")).toContainText("ms");
  }
  await expect(page.getByTestId("ranking-note")).toHaveText("按各来源名次融合，不是相关性评分。");
  expect(await page.getByTestId("search-result").count()).toBeGreaterThan(3);

  // The arXiv record has no DOI and no venue: both are shown as unknown, not blank.
  await expect(transformer(page).getByTestId("missing")).toContainText("期刊/会议");
  await expect(transformer(page).getByTestId("missing")).toContainText("DOI");
  await expect(transformer(page)).toContainText("期刊/会议未知");
  const text = await page.getByTestId("search-response").innerText();
  expect(text).not.toContain("undefined");
  expect(text).not.toContain("null");
  expect(text).not.toContain("NaN");
});

test("J2: failing sources say why while the working source still shows results", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await setMode(page, "openalex", "rate_limited");
  await setMode(page, "crossref", "slow");
  await search(page);

  await expect(page.getByTestId("search-response")).toHaveAttribute("data-outcome", "partial");
  await expect(status(page, "arxiv")).toHaveAttribute("data-status", "ok");
  await expect(status(page, "openalex")).toHaveAttribute("data-status", "rate_limited");
  await expect(status(page, "openalex").getByTestId("source-message")).toContainText("秒后再试");
  await expect(status(page, "crossref")).toHaveAttribute("data-status", "timeout");
  await expect(status(page, "crossref").getByTestId("source-message")).toContainText("没有响应");
  expect(await page.getByTestId("search-result").count()).toBeGreaterThan(0);

  // Searching again inside the cooldown does not ask OpenAlex again.
  const before = (await control(page, "/seen?source=openalex")).count;
  await setMode(page, "crossref", "ok");
  await search(page, "transformer");
  await expect(status(page, "openalex")).toHaveAttribute("data-status", "cooling_down");
  expect((await control(page, "/seen?source=openalex")).count).toBe(before);
});

test("J2: when every source fails the page lists reasons and never says nothing was found", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  for (const source of ["arxiv", "openalex", "crossref"]) {
    await setMode(page, source, "unavailable");
  }
  await search(page);

  await expect(page.getByTestId("outcome-all-failed")).toContainText("所有来源都没有返回结果");
  await expect(page.getByTestId("source-status")).toHaveCount(3);
  for (const source of ["arxiv", "openalex", "crossref"]) {
    await expect(status(page, source)).toHaveAttribute("data-status", "unavailable");
  }
  await expect(page.getByText("没有找到匹配的文献")).toHaveCount(0);
  await expect(page.getByTestId("search-result")).toHaveCount(0);

  // Sources that really have nothing are a different message.
  await control(page, "/reset");
  for (const source of ["arxiv", "openalex", "crossref"]) {
    await setMode(page, source, "empty");
  }
  await search(page, "zzz no such paper");
  await expect(page.getByTestId("outcome-no-match")).toHaveText("没有找到匹配的文献");
  await expect(page.getByTestId("outcome-all-failed")).toHaveCount(0);
});

test("J3, J5: import adds a paper without a PDF; it opens as metadata and takes a PDF upload", async ({
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await search(page);

  const result = transformer(page);
  await expect(result).toHaveCount(1);
  await result.getByRole("button", { name: "导入" }).click();
  await expect(result.getByTestId("in-project")).toHaveText("已在项目中");
  await expect(result.getByRole("button", { name: "导入" })).toHaveCount(0);

  const row = page.getByTestId("paper-row");
  await expect(row).toHaveCount(1);
  await expect(row.getByTestId("no-pdf")).toHaveText("无 PDF");
  await expect(row.getByTestId("job-status")).toHaveCount(0);

  // Still there after a reload, and a fresh search recognises it.
  await page.goto(projectUrl);
  await expect(page.getByTestId("paper-row").getByTestId("no-pdf")).toBeVisible();
  await search(page);
  await expect(transformer(page).getByTestId("in-project")).toBeVisible();
  await expect(transformer(page).getByRole("button", { name: "导入" })).toHaveCount(0);

  // The paper page shows the metadata and links, with no reader.
  await page.getByTestId("paper-row").getByRole("link").click();
  await expect(page.getByTestId("no-pdf-page")).toBeVisible();
  await expect(page.getByTestId("paper-abstract")).toContainText("dominant sequence transduction");
  await expect(page.getByTestId("page-indicator")).toHaveCount(0);
  const external = page.getByTestId("no-pdf-page").locator("a[target='_blank']");
  expect(await external.count()).toBeGreaterThan(0);
  for (const link of await external.all()) {
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(await link.getAttribute("href")).toMatch(/^https?:\/\//);
  }
  await expect(page.locator("label", { hasText: "标题（来自 arXiv）" })).toBeVisible();

  // Uploading a PDF turns it into the normal reader flow. The metadata from the source stays.
  await page.getByLabel("上传 PDF").setInputFiles(fixture("rotate-0.pdf"));
  await expect(page.getByTestId("page-indicator")).toBeVisible();
  await expect(page.getByRole("button", { name: "v1（当前）" })).toBeVisible();
  await expect(page.getByLabel("arXiv ID")).toHaveValue("1706.03762");
  await expect(page.locator("label", { hasText: "标题（来自 arXiv）" })).toBeVisible();
});

test("J3: a result that already sits in the project cannot be imported twice", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await search(page);
  await transformer(page).getByRole("button", { name: "导入" }).click();
  await expect(transformer(page).getByTestId("in-project")).toBeVisible();

  // A second tab that still shows the import button hits the duplicate and is pointed at the paper.
  const second = await page.context().newPage();
  await second.goto(page.url());
  await search(second);
  await expect(transformer(second).getByTestId("in-project")).toBeVisible();
  await expect(page.getByTestId("paper-row")).toHaveCount(1);
});

async function secondUser(browser: Browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = await signUp(page);
  return { context, page, email };
}

test("J4: a viewer has no search box and a non-member sees not-found", async ({
  browser,
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await expect(page.getByTestId("literature-search")).toBeVisible();

  const guest = await secondUser(browser);
  await guest.page.goto(projectUrl);
  await expect(guest.page.getByTestId("not-found")).toBeVisible();
  await expect(guest.page.getByTestId("literature-search")).toHaveCount(0);

  await page.getByLabel("添加已注册用户（邮箱）").fill(guest.email);
  await page.locator('select[name="role"]').selectOption("viewer");
  await page.getByRole("button", { name: "添加成员" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: guest.email })).toBeVisible();

  await guest.page.goto(projectUrl);
  await expect(guest.page.getByRole("heading", { name: "文献", exact: false }).first()).toBeVisible();
  await expect(guest.page.getByTestId("literature-search")).toHaveCount(0);
  await guest.context.close();

  // Nothing above reached the sources.
  for (const source of ["arxiv", "openalex", "crossref"]) {
    expect((await control(page, `/seen?source=${source}`)).count).toBe(0);
  }
});

test("J6: while a search runs the button is disabled and no results are shown yet", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await setMode(page, "arxiv", "delayed");
  await search(page);

  const button = page.getByTestId("literature-search").getByRole("button", { name: "检索中…" });
  await expect(button).toBeDisabled();
  await expect(page.getByTestId("search-progress")).toContainText("正在向");
  await expect(page.getByTestId("search-result")).toHaveCount(0);
  await expect(page.getByTestId("source-status")).toHaveCount(0);

  await expect(page.getByTestId("search-response")).toBeVisible();
  await expect(page.getByTestId("search-progress")).toHaveCount(0);
  await expect(
    page.getByTestId("literature-search").getByRole("button", { name: "检索", exact: true }),
  ).toBeEnabled();
});

test("G5: markup in titles from a source is shown as text and never runs", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  let dialogs = 0;
  page.on("dialog", async (dialog) => {
    dialogs += 1;
    await dialog.dismiss();
  });
  for (const source of ["arxiv", "openalex", "crossref"]) {
    await setMode(page, source, "markup");
  }
  await search(page);

  await expect(page.getByTestId("search-response")).toBeVisible();
  await expect(page.getByText("PWNED-TITLE").first()).toBeVisible();
  await expect(page.getByTestId("search-response").locator("img, script, svg")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBe(
    undefined,
  );
  expect(dialogs).toBe(0);
});
