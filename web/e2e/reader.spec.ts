import { randomUUID } from "node:crypto";
import path from "node:path";

import { expect, type Browser, type Page, test } from "@playwright/test";

const FIXTURES = process.env.E2E_FIXTURES ?? "";
const REAL_PAPER = process.env.E2E_REAL_PAPER ?? "";
// Same tolerance as the backend ink test: glyph ink and PDFium's text box are not identical.
const INK_TOLERANCE = 0.012;

const fixture = (name: string) => path.join(FIXTURES, name);

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

async function createProject(page: Page, name = "E2E project"): Promise<string> {
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  return page.url();
}

async function upload(page: Page, file: string) {
  await page.getByTestId("upload-input").setInputFiles(file);
  await expect(page.getByText("正在上传")).toBeHidden();
}

async function openOnlyPaper(page: Page, status: string) {
  const row = page.getByTestId("paper-row");
  await expect(row.getByTestId("job-status")).toHaveAttribute("data-status", status);
  await row.getByRole("link").click();
  await expect(page.getByTestId("page-indicator")).toBeVisible();
}

async function waitForRender(page: Page, pageNumber: number) {
  await expect(page.getByTestId("pdf-page")).toHaveAttribute(
    "data-rendered-page",
    String(pageNumber),
  );
}

/** Bounds of the dark pixels PDF.js really drew, normalized to the page. Null if blank. */
function canvasInk(page: Page): Promise<number[] | null> {
  return page.getByTestId("pdf-page").locator("canvas").evaluate((canvas: HTMLCanvasElement) => {
    const { width, height } = canvas;
    const { data } = canvas.getContext("2d")!.getImageData(0, 0, width, height);
    let [left, top, right, bottom] = [width, height, -1, -1];
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const at = (y * width + x) * 4;
        if (0.3 * data[at] + 0.59 * data[at + 1] + 0.11 * data[at + 2] < 200) {
          left = Math.min(left, x);
          top = Math.min(top, y);
          right = Math.max(right, x + 1);
          bottom = Math.max(bottom, y + 1);
        }
      }
    }
    return right < 0 ? null : [left / width, top / height, right / width, bottom / height];
  });
}

async function highlightBox(page: Page): Promise<number[]> {
  const frame = await page.getByTestId("pdf-page").boundingBox();
  const box = await page.getByTestId("pdf-highlight").boundingBox();
  expect(frame).not.toBeNull();
  expect(box).not.toBeNull();
  return [
    (box!.x - frame!.x) / frame!.width,
    (box!.y - frame!.y) / frame!.height,
    (box!.x + box!.width - frame!.x) / frame!.width,
    (box!.y + box!.height - frame!.y) / frame!.height,
  ];
}

async function freshProject(browser: Browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = await signUp(page);
  const url = await createProject(page);
  return { context, page, email, url };
}

test("D1 a new user imports a paper and reads it, and it survives a reload", async ({ page }) => {
  await signUp(page);
  await expect(page.getByText("还没有项目")).toBeVisible();

  await createProject(page);
  await expect(page.getByText("还没有文献")).toBeVisible();

  await upload(page, REAL_PAPER || fixture("multipage.pdf"));
  await openOnlyPaper(page, "succeeded");
  await waitForRender(page, 1);
  expect(await canvasInk(page)).not.toBeNull();

  const blocks = page.getByTestId("block");
  await expect(blocks.first()).toBeVisible();
  const projectId = page.url().split("/projects/")[1].split("/")[0];
  const paperId = page.url().split("/papers/")[1];
  const base = `/api/v1/projects/${projectId}/papers/${paperId}`;
  const paper = await (await page.request.get(base)).json();
  const stored = await (
    await page.request.get(`${base}/versions/${paper.current_version.id}/blocks?limit=2000`)
  ).json();
  await expect(blocks).toHaveCount(stored.total);
  await expect(blocks.first()).toHaveText(stored.items[0].text);

  await page.reload();
  await waitForRender(page, 1);
  await expect(blocks).toHaveCount(stored.total);
});

test("D1 the real paper has real blocks", async ({ page }) => {
  test.skip(!REAL_PAPER, "samples/pdfs/1706.03762.pdf is not present");
  await signUp(page);
  await createProject(page);
  await upload(page, REAL_PAPER);
  await openOnlyPaper(page, "succeeded");
  await expect(page.getByTestId("page-indicator")).toHaveText("第 1 / 15 页");
  await expect(page.getByTestId("block").filter({ hasText: "Attention" }).first()).toBeVisible();
});

test("D2 selecting a block jumps to its page and marks it", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  await upload(page, fixture("multipage.pdf"));
  await openOnlyPaper(page, "succeeded");

  await page.getByTestId("block").filter({ hasText: "Marker text 3" }).click();
  await expect(page.getByTestId("page-indicator")).toHaveText("第 3 / 4 页");
  await waitForRender(page, 3);
  await expect(page.getByTestId("pdf-highlight")).toBeVisible();

  await page.getByRole("button", { name: "上一页" }).click();
  await waitForRender(page, 2);
  await expect(page.getByTestId("pdf-highlight")).toBeHidden();
});

for (const name of [
  "rotate-0",
  "rotate-90",
  "rotate-180",
  "rotate-270",
  "crop",
  "crop-rotate-90",
]) {
  test(`D2 the highlight sits on the ink PDF.js drew: ${name}`, async ({ page }) => {
    await signUp(page);
    await createProject(page, name);
    await upload(page, fixture(`${name}.pdf`));
    await openOnlyPaper(page, "succeeded");
    await waitForRender(page, 1);

    await page.getByTestId("block").first().click();
    await expect(page.getByTestId("pdf-highlight")).toBeVisible();

    const ink = await canvasInk(page);
    expect(ink).not.toBeNull();
    const box = await highlightBox(page);
    box.forEach((value, index) => {
      expect(Math.abs(value - ink![index])).toBeLessThan(INK_TOLERANCE);
    });
  });
}

test("D3 a failed parse leaves the PDF readable, shows the real error, invents no blocks", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await upload(page, fixture("scanned.pdf"));

  const row = page.getByTestId("paper-row");
  await expect(row.getByTestId("job-error")).toHaveAttribute("data-code", "no_extractable_text");
  await openOnlyPaper(page, "failed");
  await waitForRender(page, 1);

  expect(await canvasInk(page)).not.toBeNull();
  await expect(page.getByTestId("job-error")).toHaveAttribute("data-code", "no_extractable_text");
  await expect(page.getByTestId("blocks-unavailable")).toBeVisible();
  await expect(page.getByTestId("block")).toHaveCount(0);
  await expect(page.getByTestId("pdf-highlight")).toHaveCount(0);
});

for (const name of ["corrupt", "encrypted"]) {
  test(`D3 an unreadable PDF reports the failure in both places: ${name}`, async ({ page }) => {
    await signUp(page);
    await createProject(page);
    await upload(page, fixture(`${name}.pdf`));
    await openOnlyPaper(page, "failed");
    await expect(page.getByTestId("job-error")).toHaveAttribute("data-code", "pdf_unreadable");
    await expect(page.getByTestId("pdf-error")).toBeVisible();
    await expect(page.getByTestId("block")).toHaveCount(0);
  });
}

test("rejected uploads explain why and create nothing", async ({ page }) => {
  await signUp(page);
  await createProject(page);

  await upload(page, fixture("not-a-pdf.txt"));
  await expect(page.getByTestId("upload-error")).toContainText("这不是有效的 PDF");
  await expect(page.getByTestId("paper-row")).toHaveCount(0);

  await upload(page, fixture("rotate-0.pdf"));
  await expect(page.getByTestId("paper-row")).toHaveCount(1);
  await upload(page, fixture("rotate-0.pdf"));
  await expect(page.getByTestId("upload-error")).toContainText("项目里已有相同文件");
  await expect(page.getByTestId("paper-row")).toHaveCount(1);
});

test("an outsider sees not-found, and a viewer sees no write controls", async ({
  browser,
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await upload(page, fixture("rotate-0.pdf"));
  await openOnlyPaper(page, "succeeded");
  const paperUrl = page.url();

  const outsider = await freshProject(browser);
  await outsider.page.goto(projectUrl);
  await expect(outsider.page.getByTestId("not-found")).toBeVisible();
  await outsider.page.goto(paperUrl);
  await expect(outsider.page.getByTestId("not-found")).toBeVisible();

  await page.goto(projectUrl);
  await page.getByLabel("添加已注册用户（邮箱）").fill(outsider.email);
  await page.locator('select[name="role"]').selectOption("viewer");
  await page.getByRole("button", { name: "添加成员" }).click();
  await expect(page.getByText(outsider.email)).toBeVisible();

  await outsider.page.goto(projectUrl);
  await expect(outsider.page.getByTestId("paper-row")).toHaveCount(1);
  await expect(outsider.page.getByTestId("upload-input")).toHaveCount(0);
  await expect(outsider.page.getByRole("button", { name: "删除" })).toHaveCount(0);
  await expect(outsider.page.getByRole("button", { name: "重新解析" })).toHaveCount(0);
  await outsider.context.close();
});

test("an expired session goes back to login", async ({ page }) => {
  await signUp(page);
  await page.context().clearCookies();
  await page.reload();
  await expect(page.getByRole("heading", { name: "登录 ResearchOS" })).toBeVisible();
});
