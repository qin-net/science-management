// Group meetings and the home-page countdown (specs/010, V1-V5), driven through the real UI.
import { randomUUID } from "node:crypto";

import { expect, type Browser, type Page, test } from "@playwright/test";

// A fixed zone makes "shown in local time" checkable.
test.use({ timezoneId: "Asia/Shanghai", locale: "zh-CN" });

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

async function createProject(page: Page, name: string): Promise<string> {
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
  return page.url();
}

const form = (page: Page) => page.getByTestId("meeting-form");

async function addMeeting(page: Page, title: string, local: string, location = "") {
  await form(page).getByLabel("标题", { exact: true }).fill(title);
  await form(page).getByLabel(/开始时间/).fill(local);
  if (location) await form(page).getByLabel(/地点/).fill(location);
  await form(page).getByRole("button", { name: "登记组会" }).click();
  await expect(form(page).getByLabel("标题", { exact: true })).toHaveValue("");
}

/** The browser's own local "YYYY-MM-DDTHH:mm" for now plus an offset, so no clock is assumed here. */
function localAfter(page: Page, minutes: number): Promise<string> {
  return page.evaluate((offset) => {
    const d = new Date(Date.now() + offset * 60_000);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }, minutes);
}

async function viewerOf(browser: Browser, owner: Page, projectUrl: string) {
  const context = await browser.newContext({ timezoneId: "Asia/Shanghai", locale: "zh-CN" });
  const page = await context.newPage();
  const email = await signUp(page, "Viewer");
  await owner.goto(projectUrl);
  await owner.getByLabel("添加已注册用户（邮箱）").fill(email);
  await owner.locator('select[name="role"]').selectOption("viewer");
  await owner.getByRole("button", { name: "添加成员" }).click();
  await expect(owner.getByRole("listitem").filter({ hasText: email })).toBeVisible();
  return { context, page };
}

test("V1: a meeting is added, shown in local time, and still there after a reload", async ({ page }) => {
  await signUp(page);
  await createProject(page, "Lab");
  await expect(page.getByTestId("meetings-empty")).toBeVisible();
  await addMeeting(page, "周会 <b>x</b>", "2030-06-01T09:30", "A101 & <i>room</i>");

  const row = page.getByTestId("meeting");
  await expect(row).toHaveCount(1);
  await expect(row.getByTestId("meeting-title")).toHaveText("周会 <b>x</b>");
  await expect(row.getByTestId("meeting-time")).toContainText("2030/6/1 09:30");
  await expect(row.getByTestId("meeting-location")).toHaveText("地点：A101 & <i>room</i>");

  await page.reload();
  await expect(page.getByTestId("meeting")).toHaveCount(1);
  await expect(page.getByTestId("meeting-time")).toContainText("2030/6/1 09:30");
});

test("V2: the home page counts down to the next meeting and links to its project", async ({ page }) => {
  await signUp(page);
  await createProject(page, "Soon");
  const minutes = 3 * 1440 + 2 * 60 + 30;
  await addMeeting(page, "近的", await localAfter(page, minutes));
  await addMeeting(page, "远的", await localAfter(page, minutes * 3));

  await page.goto("/");
  const card = page.getByTestId("next-meeting");
  await expect(card.getByTestId("next-meeting-title")).toHaveText("近的");
  await expect(card.getByTestId("next-meeting-countdown")).toHaveText("还有 3 天 2 小时");
  await expect(card.getByTestId("next-meeting-detail")).toContainText("Soon");
  await page.reload();
  await expect(card.getByTestId("next-meeting-countdown")).toHaveText("还有 3 天 2 小时");

  await card.getByTestId("next-meeting-title").click();
  await expect(page.getByRole("heading", { name: "Soon", level: 1 })).toBeVisible();
});

test("V3: a new workspace says there is no meeting, and nothing prints NaN", async ({ page }) => {
  await signUp(page);
  await createProject(page, "Empty");
  await page.goto("/");
  const card = page.getByTestId("next-meeting");
  await expect(card.getByTestId("next-meeting-empty")).toHaveText("没有安排组会");
  const text = (await card.innerText()) + (await page.getByTestId("activity").innerText());
  expect(text).not.toMatch(/NaN|undefined|null/);
});

test("V4: edit and delete survive a reload; a viewer only reads; a finished meeting is marked and skipped", async ({
  page,
  browser,
}) => {
  await signUp(page);
  const url = await createProject(page, "Edits");
  await addMeeting(page, "过去的", "2020-01-01T09:00");
  await addMeeting(page, "将来的", await localAfter(page, 2 * 60), "旧地点");

  // Finished ones sort after upcoming ones and are labelled.
  const rows = page.getByTestId("meeting");
  await expect(rows.nth(0).getByTestId("meeting-title")).toHaveText("将来的");
  await expect(rows.nth(1).getByTestId("meeting-past")).toHaveText("已结束");

  await rows.nth(0).getByTestId("meeting-edit").click();
  await rows.nth(0).getByLabel("标题", { exact: true }).fill("改过的");
  await rows.nth(0).getByLabel(/地点/).fill("");
  await rows.nth(0).getByRole("button", { name: "保存" }).click();
  await expect(rows.nth(0).getByTestId("meeting-title")).toHaveText("改过的");
  await expect(rows.nth(0).getByTestId("meeting-location")).toHaveCount(0);

  await page.goto("/");
  await expect(page.getByTestId("next-meeting-title")).toHaveText("改过的");
  await page.goto(url);
  await expect(page.getByTestId("meeting").nth(0).getByTestId("meeting-title")).toHaveText("改过的");

  // A viewer sees the list and none of the controls.
  const { context, page: viewer } = await viewerOf(browser, page, url);
  await viewer.goto(url);
  await expect(viewer.getByTestId("meeting")).toHaveCount(2);
  await expect(viewer.getByTestId("meeting-form")).toHaveCount(0);
  await expect(viewer.getByTestId("meeting-edit")).toHaveCount(0);
  await expect(viewer.getByTestId("meeting-delete")).toHaveCount(0);
  await expect(viewer.getByTestId("meeting-past")).toHaveCount(1);
  await context.close();

  page.once("dialog", (dialog) => void dialog.accept());
  await rows.nth(0).getByTestId("meeting-delete").click();
  await expect(rows).toHaveCount(1);
  await page.goto("/");
  // Only the finished meeting is left, so there is nothing to count down to.
  await expect(page.getByTestId("next-meeting-empty")).toBeVisible();
  await page.goto(url);
  await expect(page.getByTestId("meeting")).toHaveCount(1);
});

test("V5: an archived project's panel is read-only and its meetings leave the countdown", async ({ page }) => {
  await signUp(page);
  const url = await createProject(page, "Frozen");
  await addMeeting(page, "归档前", await localAfter(page, 60));
  await page.goto("/");
  await expect(page.getByTestId("next-meeting-title")).toHaveText("归档前");

  await page.goto(url);
  await page.getByRole("button", { name: "归档项目" }).click();
  await expect(page.getByText("已归档，只读")).toBeVisible();
  await expect(page.getByTestId("meeting")).toHaveCount(1);
  await expect(page.getByTestId("meeting-form")).toHaveCount(0);
  await expect(page.getByTestId("meeting-edit")).toHaveCount(0);
  await expect(page.getByTestId("meeting-delete")).toHaveCount(0);

  await page.goto("/");
  await expect(page.getByTestId("next-meeting-empty")).toBeVisible();
});
