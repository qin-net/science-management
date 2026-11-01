// Notes and block-anchored annotations (specs/003, L1-L7), driven through the real UI.
import { randomUUID } from "node:crypto";
import path from "node:path";

import { expect, type Browser, type Page, test } from "@playwright/test";

const FIXTURES = process.env.E2E_FIXTURES ?? "";
const CONTROL = process.env.E2E_CONTROL ?? "";
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

async function createProject(page: Page, name = "Notes project"): Promise<string> {
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  return page.url();
}

async function uploadAndOpen(page: Page, file: string) {
  await page.getByTestId("upload-input").setInputFiles(fixture(file));
  const row = page.getByTestId("paper-row").first();
  await expect(row.getByTestId("job-status")).toHaveAttribute("data-status", "succeeded");
  await row.getByRole("link").click();
  await expect(page.getByTestId("page-indicator")).toBeVisible();
}

const notes = (page: Page) => page.getByTestId("notes-panel");

async function annotate(page: Page, blockText: string, body: string) {
  await page.getByTestId("block").filter({ hasText: blockText }).click();
  await notes(page).getByLabel("笔记内容").fill(body);
  await notes(page).getByTestId("annotate-selected").click();
}

test("L1: annotating a block shows page and quote, survives a reload, and jumps back", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");

  await annotate(page, "Marker text 3", "符号与第 3 节不一致");
  const note = notes(page).getByTestId("note");
  await expect(note).toHaveCount(1);
  await expect(note.getByTestId("note-kind")).toHaveText("批注");
  await expect(note.getByTestId("note-where")).toHaveText("第 3 页 · v1");
  await expect(note.getByTestId("note-jump")).toHaveText("Marker text 3");
  await expect(note.getByTestId("note-body")).toHaveText("符号与第 3 节不一致");
  await expect(notes(page).getByLabel("笔记内容")).toHaveValue("");

  await page.reload();
  await expect(page.getByTestId("page-indicator")).toHaveText("第 1 / 4 页");
  await expect(notes(page).getByTestId("note")).toHaveCount(1);
  await expect(page.getByTestId("pdf-highlight")).toHaveCount(0);

  await notes(page).getByTestId("note-jump").click();
  await expect(page.getByTestId("page-indicator")).toHaveText("第 3 / 4 页");
  await expect(page.getByTestId("pdf-page")).toHaveAttribute("data-rendered-page", "3");
  await expect(page.getByTestId("pdf-highlight")).toBeVisible();
});

test("L2: a paper note says it has no source, and the project panel links to the paper", async ({
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");

  await notes(page).getByLabel("笔记内容").fill("这篇值得精读");
  await notes(page).getByRole("button", { name: "添加文献笔记（无来源）" }).click();
  const note = notes(page).getByTestId("note");
  await expect(note.getByTestId("note-kind")).toHaveText("无来源");
  await expect(note.getByTestId("note-anchor")).toHaveCount(0);

  await page.goto(projectUrl);
  await notes(page).getByLabel("笔记内容").fill("整体方向笔记");
  await notes(page).getByRole("button", { name: "添加项目笔记" }).click();
  await expect(notes(page).getByTestId("note")).toHaveCount(2);
  const onPaper = notes(page).getByTestId("note").filter({ hasText: "这篇值得精读" });
  await expect(onPaper.getByTestId("note-kind")).toHaveText("无来源");
  await onPaper.getByTestId("note-paper").click();
  await expect(page.getByTestId("page-indicator")).toBeVisible();
  // Back on the paper page only that paper's note is listed.
  await expect(notes(page).getByTestId("note")).toHaveCount(1);
  await expect(notes(page).getByTestId("note-body")).toHaveText("这篇值得精读");
});

test("L3: when the text on the page changed, the note says so and does not jump", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");
  await annotate(page, "Marker text 2", "旧引用");
  await expect(notes(page).getByTestId("note-jump")).toBeVisible();

  const altered = await page.request.get(`${CONTROL}/alter-anchors`);
  expect(altered.ok()).toBeTruthy();
  await page.reload();

  const note = notes(page).getByTestId("note");
  await expect(note.getByTestId("note-anchor")).toHaveAttribute("data-status", "stale");
  await expect(note.getByTestId("note-stale")).toContainText("原文位置已变化");
  await expect(note).toContainText("Marker text 2");
  await expect(note.getByTestId("note-jump")).toHaveCount(0);
  const text = await note.innerText();
  for (const bad of ["undefined", "null", "NaN"]) {
    expect(text).not.toContain(bad);
  }
  await expect(page.getByTestId("page-indicator")).toHaveText("第 1 / 4 页");
});

async function viewerOf(browser: Browser, owner: Page, projectUrl: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = await signUp(page);
  await owner.goto(projectUrl);
  await owner.getByLabel("添加已注册用户（邮箱）").fill(email);
  await owner.locator('select[name="role"]').selectOption("viewer");
  await owner.getByRole("button", { name: "添加成员" }).click();
  await expect(owner.getByRole("listitem").filter({ hasText: email })).toBeVisible();
  return { context, page };
}

test("L4: a viewer reads notes but has no input and no edit or delete buttons", async ({
  browser,
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");
  await annotate(page, "Marker text 1", "只读可见");
  await expect(notes(page).getByTestId("note")).toHaveCount(1);
  const paperUrl = page.url();

  const viewer = await viewerOf(browser, page, projectUrl);
  for (const url of [projectUrl, paperUrl]) {
    await viewer.page.goto(url);
    await expect(notes(viewer.page).getByTestId("note-body")).toHaveText("只读可见");
    await expect(notes(viewer.page).getByLabel("笔记内容")).toHaveCount(0);
    await expect(notes(viewer.page).getByRole("button", { name: "编辑" })).toHaveCount(0);
    await expect(notes(viewer.page).getByRole("button", { name: "删除" })).toHaveCount(0);
  }
  await expect(viewer.page.getByTestId("annotate-selected")).toHaveCount(0);
  await viewer.context.close();
});

test("L5: a stale edit is refused, says so, and keeps what was typed", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");
  await notes(page).getByLabel("笔记内容").fill("初稿");
  await notes(page).getByRole("button", { name: "添加文献笔记（无来源）" }).click();
  await expect(notes(page).getByTestId("note")).toHaveCount(1);

  // The same account in a second tab, which loaded the note before the first tab changed it.
  const other = await page.context().newPage();
  await other.goto(page.url());
  await expect(notes(other).getByTestId("note-body")).toHaveText("初稿");

  await notes(page).getByRole("button", { name: "编辑" }).click();
  await notes(page).getByLabel("编辑笔记").fill("第一次修改");
  await notes(page).getByRole("button", { name: "保存" }).click();
  await expect(notes(page).getByTestId("note-body")).toHaveText("第一次修改");

  await notes(other).getByRole("button", { name: "编辑" }).click();
  await notes(other).getByLabel("编辑笔记").fill("第二个人的修改");
  await notes(other).getByRole("button", { name: "保存" }).click();
  await expect(other.locator('.notice.error[role="alert"]')).toContainText("内容已被他人修改");
  await expect(notes(other).getByLabel("编辑笔记")).toHaveValue("第二个人的修改");

  await page.reload();
  await expect(notes(page).getByTestId("note-body")).toHaveText("第一次修改");
});

test("L6: markup in a note body is shown as text and never runs", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");
  let dialogs = 0;
  page.on("dialog", async (dialog) => {
    dialogs += 1;
    await dialog.dismiss();
  });
  const hostile = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>';
  await notes(page).getByLabel("笔记内容").fill(hostile);
  await notes(page).getByRole("button", { name: "添加文献笔记（无来源）" }).click();

  await expect(notes(page).getByTestId("note-body")).toHaveText(hostile);
  await expect(notes(page).locator("img, script")).toHaveCount(0);
  await page.reload();
  await expect(notes(page).getByTestId("note-body")).toHaveText(hostile);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBe(
    undefined,
  );
  expect(dialogs).toBe(0);
});

test("L7: deleting a paper warns about its notes and removes them", async ({ page }) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");
  await annotate(page, "Marker text 1", "会随文献消失");
  await expect(notes(page).getByTestId("note")).toHaveCount(1);
  await notes(page).getByLabel("笔记内容").fill("也会消失");
  await notes(page).getByRole("button", { name: "添加文献笔记（无来源）" }).click();
  await expect(notes(page).getByTestId("note")).toHaveCount(2);

  await page.goto(projectUrl);
  await notes(page).getByLabel("笔记内容").fill("项目笔记留下");
  await notes(page).getByRole("button", { name: "添加项目笔记" }).click();
  await expect(notes(page).getByTestId("note")).toHaveCount(3);

  let message = "";
  page.once("dialog", async (dialog) => {
    message = dialog.message();
    await dialog.accept();
  });
  await page.getByTestId("paper-row").getByRole("button", { name: "删除" }).click();
  await expect(page.getByTestId("paper-row")).toHaveCount(0);
  expect(message).toContain("同时删除这篇文献的笔记");

  await expect(notes(page).getByTestId("note")).toHaveCount(1);
  await expect(notes(page).getByTestId("note-body")).toHaveText("项目笔记留下");
  await expect(notes(page).getByTestId("note-kind")).toHaveText("项目笔记");
});
