// Cancelling a queued parse (specs/001 section 7, the known gap: the "cancel parse" button had no
// browser test because a small PDF parses in 0.1 s). The control URL holds new parse jobs in the
// queue so the button is there to click.
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

test("cancel: a queued parse can be cancelled and the button disappears", async ({ page }) => {
  await control(page, "/hold-jobs");
  try {
    await page.goto("/login");
    await page.getByRole("button", { name: "没有账号？去注册" }).click();
    await page.getByLabel("显示名称").fill("E2E User");
    await page.getByLabel("邮箱").fill(`e2e-${randomUUID()}@example.com`);
    await page.getByLabel(/密码/).fill("correct horse battery");
    await page.getByRole("button", { name: "注册", exact: true }).click();
    await expect(page.getByRole("heading", { name: "新建研究项目" })).toBeVisible();
    const name = "Cancel project";
    await page.getByLabel("项目名称").fill(name);
    await page.getByRole("button", { name: "创建项目" }).click();
    await page.getByRole("link", { name }).first().click();
    await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();

    await page.getByTestId("upload-input").setInputFiles(fixture("multipage.pdf"));
    // The job waits an hour, so it is still queued and the button is on the page.
    const status = page.getByTestId("paper-row").getByTestId("job-status");
    await expect(status).toHaveAttribute("data-status", "queued");

    await page.getByRole("button", { name: "取消解析" }).click();
    await expect(status).toHaveAttribute("data-status", "cancelled");
    await expect(page.getByRole("button", { name: "取消解析" })).toHaveCount(0);

    // It stays cancelled after a reload: the state is the server's, not the page's.
    await page.reload();
    await expect(page.getByTestId("paper-row").getByTestId("job-status")).toHaveAttribute(
      "data-status",
      "cancelled",
    );
  } finally {
    await control(page, "/release-jobs");
  }
});
