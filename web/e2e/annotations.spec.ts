// Annotations on part of a block (specs/011, N1-N4), driven through the real UI.
import { randomUUID } from "node:crypto";
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

async function createProject(page: Page, name = "Ranges"): Promise<string> {
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
const blockWith = (page: Page, text: string) =>
  page.getByTestId("block").filter({ hasText: text });

/**
 * Selects `word` inside a block the way a mouse drag does: a real DOM Range over the block's text
 * nodes, then the mouseup the block listens for. The offsets are the browser's, not ours.
 */
async function selectWords(page: Page, blockText: string, word: string) {
  await blockWith(page, blockText).evaluate((element, target) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const at = node.textContent?.indexOf(target) ?? -1;
      if (at >= 0) {
        const range = document.createRange();
        range.setStart(node, at);
        range.setEnd(node, at + target.length);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
        element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
        return;
      }
    }
    throw new Error(`"${target}" is not in this block`);
  }, word);
}

test("N1: a selection inside a block can be annotated, is marked there, and survives a reload", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");

  await selectWords(page, "Marker text 3", "Marker");
  const button = notes(page).getByTestId("annotate-selection");
  await expect(button).toHaveText("批注所选文字（第 3 页）");
  await notes(page).getByLabel("笔记内容").fill("这个词的定义有问题");
  await button.click();

  const note = notes(page).getByTestId("note");
  await expect(note).toHaveCount(1);
  await expect(note.getByTestId("note-kind")).toHaveText("批注");
  await expect(note.getByTestId("note-where")).toContainText("第 3 页 · v1");
  await expect(note.getByTestId("note-where")).toContainText("部分文字");
  await expect(note.getByTestId("note-jump")).toHaveText("Marker");
  // The button goes away once the note exists, so the same range is not marked twice by accident.
  await expect(button).toHaveCount(0);

  const block = blockWith(page, "Marker text 3");
  await expect(block).toContainText("Marker text 3");
  await expect(block.getByTestId("block-mark")).toHaveCount(1);
  await expect(block.getByTestId("block-mark")).toHaveText("Marker");

  await page.reload();
  await expect(notes(page).getByTestId("note-jump")).toHaveText("Marker");
  await expect(blockWith(page, "Marker text 3").getByTestId("block-mark")).toHaveText("Marker");
});

test("N2: a whole-block note and a range note are told apart in the list", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");

  await blockWith(page, "Marker text 2").click();
  await notes(page).getByLabel("笔记内容").fill("整段");
  await notes(page).getByTestId("annotate-selected").click();
  await expect(notes(page).getByTestId("note")).toHaveCount(1);

  await selectWords(page, "Marker text 2", "text");
  await notes(page).getByLabel("笔记内容").fill("只是一部分");
  await notes(page).getByTestId("annotate-selection").click();
  await expect(notes(page).getByTestId("note")).toHaveCount(2);

  const whole = notes(page).getByTestId("note").filter({ hasText: "整段" });
  const part = notes(page).getByTestId("note").filter({ hasText: "只是一部分" });
  await expect(whole.getByTestId("note-where")).not.toContainText("部分文字");
  await expect(part.getByTestId("note-where")).toContainText("部分文字");
  await expect(whole.getByTestId("note-jump")).toHaveText("Marker text 2");
  await expect(part.getByTestId("note-jump")).toHaveText("text");
  await expect(blockWith(page, "Marker text 2").getByTestId("block-mark")).toHaveText("text");
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

test("N3: a viewer sees the marked text and the note, with no way to add one", async ({
  page,
  browser,
}) => {
  await signUp(page);
  const url = await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");
  await selectWords(page, "Marker text 1", "Marker");
  await notes(page).getByLabel("笔记内容").fill("别人写的");
  await notes(page).getByTestId("annotate-selection").click();
  await expect(notes(page).getByTestId("note")).toHaveCount(1);
  const paperUrl = page.url();

  const { context, page: viewer } = await viewerOf(browser, page, url);
  await viewer.goto(paperUrl);
  await expect(notes(viewer).getByTestId("note-jump")).toHaveText("Marker");
  await expect(blockWith(viewer, "Marker text 1").getByTestId("block-mark")).toHaveText("Marker");
  await selectWords(viewer, "Marker text 1", "text");
  await expect(notes(viewer).getByTestId("annotate-selection")).toHaveCount(0);
  await expect(notes(viewer).getByTestId("annotate-selected")).toHaveCount(0);
  await expect(notes(viewer).getByLabel("笔记内容")).toHaveCount(0);
  await context.close();
});

test("N4: a selection that crosses two blocks offers nothing and shows no error", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await uploadAndOpen(page, "multipage.pdf");

  const blocks = page.getByTestId("block");
  await expect(blocks).toHaveCount(4);
  await blocks.first().evaluate((element) => {
    const others = document.querySelectorAll('[data-testid="block"]');
    const other = others[1] ?? element;
    const first = document.createTreeWalker(element, NodeFilter.SHOW_TEXT).nextNode();
    const lastWalker = document.createTreeWalker(other, NodeFilter.SHOW_TEXT);
    let last = lastWalker.nextNode();
    for (let next = lastWalker.nextNode(); next; next = lastWalker.nextNode()) last = next;
    if (!first || !last) throw new Error("no text to select");
    const range = document.createRange();
    range.setStart(first, 0);
    range.setEnd(last, last.textContent?.length ?? 0);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });

  await expect(notes(page).getByTestId("annotate-selection")).toHaveCount(0);
  // Next.js renders a role="alert" route announcer of its own, so match the app's notice instead.
  await expect(page.locator('.notice.error[role="alert"]')).toHaveCount(0);
  await expect(notes(page).getByTestId("note")).toHaveCount(0);
});
