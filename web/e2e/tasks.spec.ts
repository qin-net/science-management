// Project tasks and the board (specs/004, M1-M8), driven through the real UI.
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

async function createProject(page: Page, name = "Tasks project"): Promise<string> {
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  return page.url();
}

async function addMember(owner: Page, projectUrl: string, email: string, role: string) {
  await owner.goto(projectUrl);
  await owner.getByLabel("添加已注册用户（邮箱）").fill(email);
  await owner.locator('select[name="role"]').selectOption(role);
  await owner.getByRole("button", { name: "添加成员" }).click();
  await expect(owner.getByRole("listitem").filter({ hasText: email })).toBeVisible();
}

async function otherUser(browser: Browser, owner: Page, projectUrl: string, role: string, name: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = await signUp(page, name);
  await addMember(owner, projectUrl, email, role);
  return { context, page };
}

const board = (page: Page) => page.getByTestId("tasks-panel");
const column = (page: Page, status: string) =>
  page.locator(`[data-testid="task-column"][data-status="${status}"]`);
const form = (page: Page) => page.getByTestId("task-form");

async function addTask(page: Page, title: string, extra: { description?: string; due?: string } = {}) {
  await form(page).getByLabel("标题", { exact: true }).fill(title);
  if (extra.description) {
    await form(page).getByLabel("说明").fill(extra.description);
  }
  if (extra.due) {
    await form(page).getByLabel("期限").fill(extra.due);
  }
  await form(page).getByRole("button", { name: "新建任务" }).click();
  await expect(form(page).getByLabel("标题", { exact: true })).toHaveValue("");
}

test("M1: a new task lands in the to-do column, survives a reload, and the count is right", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await expect(board(page).getByTestId("tasks-empty")).toBeVisible();

  await addTask(page, "整理相关工作");
  await addTask(page, "跑基线实验");
  await expect(column(page, "todo").getByTestId("task")).toHaveCount(2);
  await expect(column(page, "todo").getByTestId("task-count")).toHaveText("2");
  await expect(column(page, "doing").getByTestId("task-count")).toHaveText("0");

  await page.reload();
  await expect(column(page, "todo").getByTestId("task")).toHaveCount(2);
  await expect(column(page, "todo").getByTestId("task-title").first()).toHaveText("跑基线实验");
  await expect(column(page, "todo").getByTestId("task-count")).toHaveText("2");
});

test("M2: blocking asks for a reason, and done moves the card and stays after a reload", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await addTask(page, "申请数据集");

  const card = column(page, "todo").getByTestId("task");
  await card.getByTestId("task-status").selectOption("blocked");
  const save = card.getByRole("button", { name: "标为受阻" });
  await expect(save).toBeDisabled();
  await expect(column(page, "todo").getByTestId("task")).toHaveCount(1);
  await card.getByLabel("受阻原因").fill("等数据集授权");
  await save.click();

  await expect(column(page, "blocked").getByTestId("task")).toHaveCount(1);
  await expect(column(page, "blocked").getByTestId("task-blocked-reason")).toHaveText(
    "受阻：等数据集授权",
  );
  await expect(column(page, "blocked").getByTestId("task-count")).toHaveText("1");

  await column(page, "blocked").getByTestId("task-status").selectOption("done");
  await expect(column(page, "done").getByTestId("task")).toHaveCount(1);
  await expect(column(page, "blocked").getByTestId("task")).toHaveCount(0);
  await expect(column(page, "done").getByTestId("task-blocked-reason")).toHaveCount(0);

  await page.reload();
  await expect(column(page, "done").getByTestId("task-title")).toHaveText("申请数据集");
  await expect(column(page, "done").getByTestId("task-count")).toHaveText("1");
});

test("M3: a viewer sees the board but has no form, status menu, edit or delete", async ({
  browser,
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await addTask(page, "只读可见");
  const viewer = await otherUser(browser, page, projectUrl, "viewer", "Viewer");

  await viewer.page.goto(projectUrl);
  await expect(column(viewer.page, "todo").getByTestId("task-title")).toHaveText("只读可见");
  await expect(form(viewer.page)).toHaveCount(0);
  await expect(board(viewer.page).getByTestId("task-status")).toHaveCount(0);
  await expect(board(viewer.page).getByTestId("task-edit")).toHaveCount(0);
  await expect(board(viewer.page).getByTestId("task-delete")).toHaveCount(0);
  await viewer.context.close();
});

test("M4: a stale edit is refused, says so, and keeps what was typed", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  await addTask(page, "初稿");

  const other = await page.context().newPage();
  await other.goto(page.url());
  await expect(column(other, "todo").getByTestId("task-title")).toHaveText("初稿");

  const edit = async (target: Page, title: string) => {
    const card = column(target, "todo").getByTestId("task");
    await card.getByTestId("task-edit").click();
    await card.getByLabel("标题", { exact: true }).fill(title);
    await card.getByRole("button", { name: "保存" }).click();
  };
  await edit(page, "第一次修改");
  await expect(column(page, "todo").getByTestId("task-title")).toHaveText("第一次修改");

  await edit(other, "第二个人的修改");
  await expect(other.locator('.notice.error[role="alert"]')).toContainText("内容已被他人修改");
  await expect(column(other, "todo").getByTestId("task").getByLabel("标题", { exact: true })).toHaveValue(
    "第二个人的修改",
  );

  await page.reload();
  await expect(column(page, "todo").getByTestId("task-title")).toHaveText("第一次修改");
});

test("M5: markup in a task is shown as text and never runs", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  let dialogs = 0;
  page.on("dialog", async (dialog) => {
    dialogs += 1;
    await dialog.dismiss();
  });
  const hostile = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>';
  await addTask(page, hostile, { description: hostile });

  await expect(board(page).getByTestId("task-title")).toHaveText(hostile);
  await expect(board(page).getByTestId("task-description")).toHaveText(hostile);
  await expect(board(page).locator("img, script")).toHaveCount(0);
  await page.reload();
  await expect(board(page).getByTestId("task-title")).toHaveText(hostile);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBe(
    undefined,
  );
  expect(dialogs).toBe(0);
});

test("M6: a late task is marked overdue, and finishing it clears the mark", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  await addTask(page, "过期的", { due: "2020-01-01" });
  await addTask(page, "还早", { due: "2999-01-01" });

  const late = board(page).getByTestId("task").filter({ hasText: "过期的" });
  const early = board(page).getByTestId("task").filter({ hasText: "还早" });
  await expect(late.getByTestId("task-overdue")).toHaveText("已逾期");
  await expect(late.getByTestId("task-due")).toHaveText("期限 2020-01-01");
  await expect(early.getByTestId("task-overdue")).toHaveCount(0);
  await expect(board(page).getByTestId("tasks-overdue-count")).toContainText("1 个逾期");

  await late.getByTestId("task-status").selectOption("done");
  await expect(column(page, "done").getByTestId("task")).toHaveCount(1);
  await expect(board(page).getByTestId("task-overdue")).toHaveCount(0);
  await expect(board(page).getByTestId("tasks-overdue-count")).toHaveCount(0);
});

test("M7: filtering by assignee narrows the cards but not the project counts", async ({
  browser,
  page,
}) => {
  await signUp(page, "Owner A");
  const projectUrl = await createProject(page);
  const member = await otherUser(browser, page, projectUrl, "member", "Member B");
  await member.context.close();

  await page.goto(projectUrl);
  await form(page).getByLabel("标题", { exact: true }).fill("给 B 的");
  await form(page).getByLabel("负责人").selectOption({ label: "Member B" });
  await form(page).getByRole("button", { name: "新建任务" }).click();
  await expect(form(page).getByLabel("标题", { exact: true })).toHaveValue("");
  await addTask(page, "没人认领");

  await expect(board(page).getByTestId("task")).toHaveCount(2);
  await board(page).getByTestId("tasks-filter").selectOption({ label: "Member B" });
  await expect(board(page).getByTestId("task")).toHaveCount(1);
  await expect(board(page).getByTestId("task-title")).toHaveText("给 B 的");
  await expect(board(page).getByTestId("task-assignee")).toHaveText("Member B");
  await expect(column(page, "todo").getByTestId("task-count")).toHaveText("2");

  await board(page).getByTestId("tasks-filter").selectOption("none");
  await expect(board(page).getByTestId("task-title")).toHaveText("没人认领");
  await expect(board(page).getByTestId("task-assignee")).toHaveText("未分配");
  await expect(column(page, "todo").getByTestId("task-count")).toHaveText("2");
});

test("M8: deleting a paper keeps its tasks and drops the link", async ({ page }) => {
  await signUp(page);
  await createProject(page);
  await page.getByTestId("upload-input").setInputFiles(fixture("multipage.pdf"));
  await expect(page.getByTestId("paper-row").getByTestId("job-status")).toHaveAttribute(
    "data-status",
    "succeeded",
  );

  await form(page).getByLabel("标题", { exact: true }).fill("精读这篇");
  await form(page).getByLabel("关联文献").selectOption({ index: 1 });
  await form(page).getByRole("button", { name: "新建任务" }).click();
  await expect(board(page).getByTestId("task-paper")).toHaveCount(1);

  let message = "";
  page.once("dialog", async (dialog) => {
    message = dialog.message();
    await dialog.accept();
  });
  await page.getByTestId("paper-row").getByRole("button", { name: "删除" }).click();
  await expect(page.getByTestId("paper-row")).toHaveCount(0);
  expect(message).toContain("关联的任务会保留");

  await expect(board(page).getByTestId("task-title")).toHaveText("精读这篇");
  await expect(board(page).getByTestId("task-paper")).toHaveCount(0);
  await page.reload();
  await expect(board(page).getByTestId("task-title")).toHaveText("精读这篇");
  await expect(board(page).getByTestId("task-paper")).toHaveCount(0);
});
