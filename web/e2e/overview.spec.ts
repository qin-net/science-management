// The home-page overview (specs/005, N1-N5), driven through the real UI.
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
  await expect(
    page.getByRole("heading", { name: "新建研究项目" }),
  ).toBeVisible();
  return email;
}

async function createProject(page: Page, name: string): Promise<string> {
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  return page.url();
}

const form = (page: Page) => page.getByTestId("task-form");

async function addTask(
  page: Page,
  title: string,
  extra: { due?: string; assignee?: string } = {},
) {
  await form(page).getByLabel("标题", { exact: true }).fill(title);
  if (extra.due) await form(page).getByLabel("期限").fill(extra.due);
  if (extra.assignee)
    await form(page)
      .getByLabel("负责人")
      .selectOption({ label: extra.assignee });
  await form(page).getByRole("button", { name: "新建任务" }).click();
  await expect(form(page).getByLabel("标题", { exact: true })).toHaveValue("");
}

const total = (page: Page, key: string) =>
  page.locator(
    `[data-testid="overview-total"][data-key="${key}"] .total-value`,
  );

async function viewerOf(browser: Browser, owner: Page, projectUrl: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = await signUp(page, "Viewer");
  await owner.goto(projectUrl);
  await owner.getByLabel("添加已注册用户（邮箱）").fill(email);
  await owner.locator('select[name="role"]').selectOption("viewer");
  await owner.getByRole("button", { name: "添加成员" }).click();
  await expect(
    owner.getByRole("listitem").filter({ hasText: email }),
  ).toBeVisible();
  return { context, page };
}

test("N1: the home page shows the stored counts, and a reload shows the same", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page, "Alpha");
  await page
    .getByTestId("upload-input")
    .setInputFiles(fixture("multipage.pdf"));
  await expect(
    page.getByTestId("paper-row").getByTestId("job-status"),
  ).toHaveAttribute("data-status", "succeeded");
  await addTask(page, "一", { assignee: "E2E User" });
  await addTask(page, "二", { due: "2020-01-01" });

  await page.goto("/");
  await expect(total(page, "projects")).toHaveText("1");
  await expect(total(page, "papers")).toHaveText("1");
  await expect(total(page, "open")).toHaveText("2");
  await expect(total(page, "overdue")).toHaveText("1");
  const line = page.getByTestId("project-counts");
  await expect(line).toContainText("文献 1（已解析 1）");
  await expect(line).toContainText("待办 2");
  await expect(line).toContainText("逾期 1");

  await page.reload();
  await expect(total(page, "open")).toHaveText("2");
  await expect(page.getByTestId("project-counts")).toContainText("逾期 1");
  await expect(
    page.getByTestId("recent-events").getByTestId("overview-event").first(),
  ).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/NaN|undefined/);
});

test("N2: my overdue task comes first with a mark and links to its project", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page, "Beta");
  await addTask(page, "不急", { due: "2999-01-01", assignee: "E2E User" });
  await addTask(page, "没期限", { assignee: "E2E User" });
  await addTask(page, "已经晚了", { due: "2020-01-01", assignee: "E2E User" });
  await addTask(page, "没人要", { due: "2019-01-01" });

  await page.goto("/");
  const mine = page.getByTestId("my-tasks").getByTestId("my-task");
  await expect(mine).toHaveCount(3);
  await expect(mine.nth(0).getByTestId("my-task-title")).toHaveText("已经晚了");
  await expect(mine.nth(0).getByTestId("my-task-overdue")).toHaveText("已逾期");
  await expect(mine.nth(1).getByTestId("my-task-title")).toHaveText("不急");
  await expect(mine.nth(2).getByTestId("my-task-title")).toHaveText("没期限");
  await expect(page.getByTestId("my-task-overdue")).toHaveCount(1);

  await mine.nth(0).getByTestId("my-task-title").click();
  await expect(
    page.getByRole("heading", { name: "Beta", level: 1 }),
  ).toBeVisible();
});

test("N3: a new workspace shows the empty states and no broken values", async ({
  page,
}) => {
  await signUp(page);
  await expect(page.getByTestId("overview-totals")).toHaveCount(0);
  await expect(page.getByTestId("my-tasks-empty")).toHaveText(
    "没有分给你的未完成任务",
  );
  await expect(page.getByTestId("events-empty")).toHaveText("还没有动态");
  await expect(page.getByText("还没有项目，先创建一个吧。")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/NaN|undefined|null/);
});

test("N4: a viewer sees the overview, with an explained empty list of their own", async ({
  browser,
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page, "Gamma");
  await addTask(page, "别人的", { due: "2020-01-01", assignee: "E2E User" });
  const viewer = await viewerOf(browser, page, projectUrl);

  await viewer.page.goto("/");

  await expect(viewer.page.getByTestId("project-counts")).toContainText(
    "逾期 1",
  );
  await expect(total(viewer.page, "overdue")).toHaveText("1");
  await expect(viewer.page.getByTestId("my-tasks-empty")).toHaveText(
    "没有分给你的未完成任务",
  );
  await viewer.context.close();
});

test("N5: changing a task moves the numbers, and the definitions can be opened", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page, "Delta");
  await addTask(page, "会完成", { due: "2020-01-01", assignee: "E2E User" });
  await page.goto("/");
  await expect(total(page, "overdue")).toHaveText("1");
  await expect(total(page, "open")).toHaveText("1");

  await page.getByRole("link", { name: "Delta" }).first().click();
  await page
    .getByTestId("task")
    .getByTestId("task-status")
    .selectOption("done");
  await expect(
    page.locator(
      '[data-testid="task-column"][data-status="done"] [data-testid="task"]',
    ),
  ).toHaveCount(1);
  await page.goto("/");
  await expect(total(page, "overdue")).toHaveText("0");
  await expect(total(page, "open")).toHaveText("0");
  await expect(page.getByTestId("project-counts")).toContainText("完成 1");
  await expect(page.getByTestId("my-tasks-empty")).toBeVisible();

  const basis = page.getByTestId("overview-basis");
  await expect(basis.getByText(/逾期：期限早于/)).toBeHidden();
  await basis.getByText("统计口径").click();
  await expect(basis.getByText(/逾期：期限早于/)).toBeVisible();
  await expect(basis.getByText(/已解析：文献的当前版本/)).toBeVisible();
});
