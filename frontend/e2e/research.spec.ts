import { test, expect, type Page } from "@playwright/test";

async function login(page: Page, email = "research@example.test") {
  await page.goto("/login");
  await page.getByPlaceholder("your@email.com").fill(email);
  await page.getByPlaceholder("输入密码").fill("BrowserFixture-Only-2026");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("研究项目选择")).toBeVisible();
}

test("real API: MCP pool, evaluation, strategy, blocked export, reload and access isolation", async ({ page, browser }) => {
  await login(page);
  const selector = page.getByLabel("研究项目选择");
  await selector.selectOption({ label: "MCP 共享测试项目 · demo_global_equity" });
  const projectId = await selector.inputValue();
  await page.getByRole("button", { name: "共同因子池", exact: true }).click();
  await expect(page.getByText("MCP 写入的固定样例", { exact: true })).toBeVisible();
  await page.getByLabel("收藏到项目").click();
  await expect(page.getByLabel("取消项目收藏")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("取消项目收藏")).toBeVisible();

  // Keep a real refresh token while forcing the access token to fail validation.
  await page.evaluate(() => localStorage.setItem("quantgpt_access_token", "expired-browser-fixture"));
  await page.reload();
  await expect(page.getByLabel("取消项目收藏")).toBeVisible();

  await page.getByRole("button", { name: "项目研究", exact: true }).click();
  await page.getByRole("button", { name: "准备离线合成数据" }).click();
  await expect(page.getByLabel("研究因子表达式")).toHaveValue("rank(close / ts_mean(close, 20))");
  await page.getByLabel("研究因子表达式").fill("rank(close / ts_mean(close, 20)) + rank(volume)");
  await page.getByLabel("经济假设", { exact: true }).fill("合成流程中的趋势延续假设；趋势逆转时可能失效。此输入仅检验事前登记。");
  const evaluationRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/evaluations"));
  await page.getByRole("button", { name: "评价因子", exact: true }).click();
  expect((await evaluationRequest).postDataJSON().definitions[0].fields.map((field: { name: string }) => field.name).sort()).toEqual(["close", "volume"]);
  await expect(page.getByRole("heading", { name: "因子评价 · backtested_train" })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "收藏评价到共同池" }).click();
  await expect(page.getByText("已收藏到项目共同因子池，保留服务端评价引用。")).toBeVisible();
  await page.getByRole("button", { name: "使用评价证据运行策略" }).click();
  await expect(page.getByLabel("项目策略结果")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "加载净值曲线" }).click();
  await expect(page.locator(".recharts-line")).toBeVisible();
  await page.getByRole("button", { name: "检查并申请研究导出" }).click();
  await expect(page.getByText("导出被阻断", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "优化保存的信号", exact: true }).click();
  await expect(page.getByRole("heading", { name: "组合约束可行" })).toBeVisible();
  await page.getByLabel("优化单股权重上限").fill("0.01");
  await page.getByLabel("优化现金权重上限").fill("0");
  await page.getByRole("button", { name: "优化保存的信号", exact: true }).click();
  await expect(page.getByRole("heading", { name: "组合优化被阻断" })).toBeVisible();
  const runId = await page.getByLabel("服务端策略运行 ID").inputValue();
  await page.screenshot({ path: "../test-results/research-workflow.png", fullPage: true });
  await page.reload();
  await expect(page.getByLabel("服务端策略运行 ID")).toHaveValue(runId);
  await expect(page.getByLabel("项目策略结果")).toBeVisible();
  await page.getByRole("button", { name: "共同因子池", exact: true }).click();
  await page.getByRole("button", { name: "研究评价收藏" }).click();
  await expect(page.getByText("证据状态：research_only")).toBeVisible();

  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await login(otherPage, "outsider@example.test");
  await expect(otherPage.getByLabel("研究项目选择").locator("option")).toHaveCount(1);
  const forbiddenStatus = await otherPage.evaluate(async ({ projectId, runId }) => {
    const response = await fetch(`/api/v1/research/projects/${projectId}/strategy-runs/${runId}`, { headers: { Authorization: `Bearer ${localStorage.getItem("quantgpt_access_token")}` } });
    return response.status;
  }, { projectId, runId });
  expect(forbiddenStatus).toBe(404);
  await other.close();

  await page.getByRole("button", { name: "退出", exact: true }).click();
  await login(page);
  await page.getByLabel("研究项目选择").selectOption(projectId);
  await page.getByRole("button", { name: "项目研究", exact: true }).click();
  await expect(page.getByLabel("服务端策略运行 ID")).toHaveValue(runId);
});

test("project creation and editable dates/capabilities in legacy strategy workbench", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "新建研究项目" }).click();
  await page.getByLabel("项目名称").fill("网页创建的研究项目");
  await page.getByRole("button", { name: "创建并选择" }).click();
  await expect(page.getByLabel("研究项目选择").locator("option:checked")).toHaveText("网页创建的研究项目 · us");
  await page.getByRole("button", { name: "策略工作台", exact: true }).click();
  await page.getByLabel("研究开始日期").fill("2024-02-01");
  await page.getByLabel("研究结束日期").fill("2024-06-28");
  await expect(page.getByLabel("研究开始日期")).toHaveValue("2024-02-01");
  await page.getByLabel("策略市场").selectOption("us");
  await expect(page.getByText("市场与字段", { exact: true })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "能力状态" })).toBeVisible();
});

test("missing data capability is shown as failed research and cannot run a strategy", async ({ page }) => {
  await login(page);
  await page.getByLabel("研究项目选择").selectOption({ label: "MCP 共享测试项目 · demo_global_equity" });
  await page.getByRole("button", { name: "项目研究", exact: true }).click();
  await page.getByRole("button", { name: "准备离线合成数据" }).click();
  await page.getByLabel("研究因子表达式").fill("rank(market_cap)");
  await page.getByRole("button", { name: "评价因子", exact: true }).click();
  await expect(page.getByRole("heading", { name: "因子评价 · rejected" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("market_cap");
  await expect(page.getByRole("button", { name: "使用评价证据运行策略" })).toBeDisabled();
});

test("failed combination submission preserves inputs and shows the error in place", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/composite-backtest", route => route.fulfill({
    status: 422, contentType: "application/json", body: JSON.stringify({ detail: "组合输入校验失败（浏览器测试）" }),
  }));
  await page.getByRole("button", { name: "多因子组合", exact: true }).click();
  const factors = page.getByPlaceholder("因子表达式", { exact: true });
  await factors.nth(0).fill("rank(close)");
  await factors.nth(1).fill("rank(volume)");
  await page.getByRole("button", { name: "开始组合回测", exact: true }).click();
  await expect(page.getByText("组合输入校验失败（浏览器测试）", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/#composite$/);
  await expect(factors.nth(0)).toHaveValue("rank(close)");
  await expect(factors.nth(1)).toHaveValue("rank(volume)");
  await expect(page.getByRole("button", { name: "开始组合回测", exact: true })).toBeEnabled();
});

test("task list summaries fetch the full result only when opening the detail drawer", async ({ page }) => {
  let detailReads = 0;
  await page.route(/\/api\/v1\/tasks\?/, route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ tasks: [
      { task_id: "summary-browser-test", status: "completed", summary_only: true, expression: "rank(close)" },
    ], total: 1, page: 1, page_size: 20 }),
  }));
  await page.route("**/api/v1/tasks/summary-browser-test", route => {
    detailReads++;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({
      task_id: "summary-browser-test", status: "completed", result: { detail_marker: "完整结果已按需读取" },
    }) });
  });
  await login(page);
  const row = page.getByRole("row").filter({ hasText: "summary-browser-test" });
  await expect(row).toBeVisible();
  expect(detailReads).toBe(0);
  await row.click();
  await expect(page.getByText('"detail_marker": "完整结果已按需读取"', { exact: false })).toBeVisible();
  expect(detailReads).toBe(1);
});

test("offline validation runs as a durable task and retains external acceptance blockers", async ({ page }) => {
  await login(page);
  await page.getByLabel("研究项目选择").selectOption({ label: "MCP 共享测试项目 · demo_global_equity" });
  await page.getByRole("button", { name: "项目研究", exact: true }).click();
  await page.getByRole("button", { name: "准备离线合成数据", exact: true }).click();
  await page.getByRole("button", { name: "评价因子", exact: true }).click();
  await expect(page.getByRole("heading", { name: "因子评价 · backtested_train" })).toBeVisible();
  const accepted = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/validation-runs"));
  await page.getByRole("button", { name: "运行离线验证", exact: true }).click();
  expect((await accepted).status()).toBe(202);
  await expect(page.getByRole("heading", { name: "离线验证结果已保存", exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("table", { name: "离线验证检查" })).toBeVisible();
  await expect(page.getByText("真实数据验收未完成", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新运行离线验证", exact: true })).toBeEnabled();
  await page.reload();
  await expect(page.getByRole("heading", { name: "离线验证结果已保存", exact: true })).toBeVisible();
  await expect(page.getByText("真实数据验收未完成", { exact: true })).toBeVisible();
});
