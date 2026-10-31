// Project, metadata, version and member controls, driven through the real UI.
// Each case reloads or re-reads the server afterwards, so a control that only
// changes local state cannot pass.
import { randomUUID } from "node:crypto";
import path from "node:path";

import { expect, type Browser, type Page, test } from "@playwright/test";

const FIXTURES = process.env.E2E_FIXTURES ?? "";
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

async function openPaper(page: Page, link = page.getByTestId("paper-row").getByRole("link")) {
  await link.click();
  await expect(page.getByTestId("page-indicator")).toBeVisible();
}

// Next.js also renders a role="alert" route announcer, so match the app's own notice.
const notice = (page: Page) => page.locator('.notice.error[role="alert"]');

async function secondUser(browser: Browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = await signUp(page);
  return { context, page, email };
}

test("project: edit goal and name, archive makes it read-only, restore makes it editable", async ({
  page,
}) => {
  await signUp(page);
  const url = await createProject(page, "Original name");
  await upload(page, fixture("rotate-0.pdf"));

  await page.getByLabel("项目名称").fill("Renamed project");
  await page.getByLabel("研究目标").fill("Find out whether boxes match ink");
  await page.getByLabel("研究背景").fill("Background text");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("已保存")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Renamed project", level: 1 })).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("项目名称")).toHaveValue("Renamed project");
  await expect(page.getByLabel("研究目标")).toHaveValue("Find out whether boxes match ink");
  await expect(page.getByLabel("研究背景")).toHaveValue("Background text");

  await page.getByRole("button", { name: "归档项目" }).click();
  await expect(page.getByText("已归档，只读")).toBeVisible();
  await expect(page.getByLabel("项目名称")).toBeDisabled();
  await expect(page.getByRole("button", { name: "保存", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("upload-input")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "删除" })).toHaveCount(0);

  // The state is on the server, not in the page.
  await page.goto(url);
  await expect(page.getByText("已归档，只读")).toBeVisible();
  await expect(page.getByTestId("upload-input")).toHaveCount(0);

  await page.getByRole("button", { name: "恢复项目" }).click();
  await expect(page.getByText("已归档，只读")).toBeHidden();
  await expect(page.getByLabel("项目名称")).toBeEnabled();
  await expect(page.getByTestId("upload-input")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("upload-input")).toBeVisible();
});

test("metadata: manual edits persist and are marked manual, a duplicate DOI is refused", async ({
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await upload(page, fixture("rotate-0.pdf"));
  await openPaper(page);
  const firstPaperPath = new URL(page.url()).pathname;

  await page.getByLabel(/^标题/).fill("A manually set title");
  await page.getByLabel(/^作者/).fill("Ada Lovelace, Alan Turing");
  await page.getByLabel("年份").fill("2017");
  await page.getByLabel("DOI").fill("https://doi.org/10.1234/ABC.def");
  await page.getByLabel("arXiv ID").fill("1706.03762");
  await page.getByRole("button", { name: "保存信息" }).click();
  await expect(page.getByLabel("DOI")).toHaveValue("10.1234/abc.def");
  await expect(page.locator("label", { hasText: "标题（手动填写）" })).toBeVisible();

  await page.reload();
  await expect(page.getByLabel(/^标题/)).toHaveValue("A manually set title");
  await expect(page.getByLabel(/^作者/)).toHaveValue("Ada Lovelace, Alan Turing");
  await expect(page.getByLabel("年份")).toHaveValue("2017");
  await expect(page.getByLabel("DOI")).toHaveValue("10.1234/abc.def");
  await expect(page.getByLabel("arXiv ID")).toHaveValue("1706.03762");

  await page.getByLabel("DOI").fill("not a doi");
  await page.getByRole("button", { name: "保存信息" }).click();
  await expect(notice(page)).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("DOI")).toHaveValue("10.1234/abc.def");

  await page.goto(projectUrl);
  await upload(page, fixture("rotate-90.pdf"));
  await expect(page.getByTestId("paper-row")).toHaveCount(2);
  await openPaper(
    page,
    page.locator(`[data-testid="paper-row"] a:not([href="${firstPaperPath}"])`),
  );
  await page.getByLabel("DOI").fill("10.1234/ABC.DEF");
  await page.getByRole("button", { name: "保存信息" }).click();
  await expect(notice(page)).toContainText("项目里已有相同 DOI 的文献");

  await page.reload();
  await expect(page.getByLabel("DOI")).toHaveValue("");
});

test("versions: uploading a new file adds v2 as the current version, v1 stays readable", async ({
  page,
}) => {
  await signUp(page);
  await createProject(page);
  await upload(page, fixture("rotate-0.pdf"));
  await openPaper(page);
  await expect(page.getByRole("button", { name: "v1（当前）" })).toBeVisible();

  await page.getByLabel("上传新版本").setInputFiles(fixture("rotate-90.pdf"));
  await expect(page.getByRole("button", { name: "v2（当前）" })).toBeVisible();
  await expect(page.getByRole("button", { name: "v1", exact: true })).toBeEnabled();
  await expect(page.getByText("rotate-90.pdf")).toBeVisible();

  await page.getByRole("button", { name: "v1", exact: true }).click();
  await expect(page.getByRole("button", { name: "v1", exact: true })).toBeDisabled();
  await expect(page.getByTestId("pdf-page")).toHaveAttribute("data-rendered-page", "1");

  await page.reload();
  await expect(page.getByRole("button", { name: "v2（当前）" })).toBeVisible();

  // The same bytes again are refused and do not create v3.
  await page.getByLabel("上传新版本").setInputFiles(fixture("rotate-90.pdf"));
  await expect(notice(page)).toBeVisible();
  await expect(page.getByRole("button", { name: /^v3/ })).toHaveCount(0);
});

test("members: add, change role, remove; the removed user loses access", async ({
  browser,
  page,
}) => {
  await signUp(page);
  const projectUrl = await createProject(page);
  await upload(page, fixture("rotate-0.pdf"));

  const guest = await secondUser(browser);
  await guest.page.goto(projectUrl);
  await expect(guest.page.getByTestId("not-found")).toBeVisible();

  await page.getByLabel("添加已注册用户（邮箱）").fill("nobody-" + randomUUID() + "@example.com");
  await page.getByRole("button", { name: "添加成员" }).click();
  await expect(notice(page)).toContainText("没有使用此邮箱的注册用户");

  await page.getByLabel("添加已注册用户（邮箱）").fill(guest.email);
  await page.locator('select[name="role"]').selectOption("viewer");
  await page.getByRole("button", { name: "添加成员" }).click();
  const member = page.getByRole("listitem").filter({ hasText: guest.email });
  await expect(member).toBeVisible();
  await expect(member.getByRole("combobox")).toHaveValue("viewer");

  await guest.page.goto(projectUrl);
  await expect(guest.page.getByTestId("paper-row")).toHaveCount(1);
  await expect(guest.page.getByTestId("upload-input")).toHaveCount(0);

  await member.getByRole("combobox").selectOption("member");
  await expect(member.getByRole("combobox")).toHaveValue("member");
  await page.reload();
  await expect(member.getByRole("combobox")).toHaveValue("member");

  await guest.page.goto(projectUrl);
  await expect(guest.page.getByTestId("upload-input")).toBeVisible();

  await member.getByRole("button", { name: "移除" }).click();
  await expect(member).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(guest.email)).toHaveCount(0);

  await guest.page.goto(projectUrl);
  await expect(guest.page.getByTestId("not-found")).toBeVisible();
  await guest.context.close();
});

test("members: the last owner cannot be removed or demoted", async ({ page }) => {
  const email = await signUp(page);
  await createProject(page);

  const self = page.getByRole("listitem").filter({ hasText: email });
  await self.getByRole("button", { name: "移除" }).click();
  await expect(notice(page)).toContainText("工作区至少要保留一位所有者");
  await expect(self).toBeVisible();

  await self.getByRole("combobox").selectOption("viewer");
  await expect(notice(page)).toContainText("工作区至少要保留一位所有者");
  await page.reload();
  await expect(self.getByRole("combobox")).toHaveValue("owner");
});
