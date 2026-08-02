import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SourceListItem } from "../api/sources";
import SourcesPage from "./SourcesPage";

function makeSource(overrides: Partial<SourceListItem>): SourceListItem {
  return {
    id: "s1",
    name: "示例媒体",
    name_zh: null,
    country_code: "US",
    media_type: "newspaper",
    language: "en",
    collect_mode: "rss",
    adapter_type: "rss",
    poll_interval_min: 5,
    audience_weight: 80,
    coverage_confidence: "high",
    status: "active",
    is_custom: false,
    last_success_at: "2026-07-24T10:00:00",
    health_24h: { success_rate: 0.95, articles_24h: 42, avg_latency_min: 3.5 },
    ...overrides,
  };
}

function stubSourcesPage(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
}

describe("媒体源管理页", () => {
  beforeEach(() => {
    localStorage.setItem(
      "agendascope.tokens",
      JSON.stringify({ access_token: "a", refresh_token: "r", expires_in: 1800 }),
    );
  });

  it("渲染后端返回的源列表（名称/国家/类型/健康状态/最近采集时间）", async () => {
    stubSourcesPage({
      code: 0,
      message: "ok",
      data: {
        total: 2,
        page: 1,
        page_size: 20,
        items: [
          makeSource({ id: "s1", name: "The Example Times", status: "active" }),
          makeSource({
            id: "s2",
            name: "Daily Sample",
            name_zh: "每日样例",
            country_code: "GB",
            media_type: "online",
            status: "failed",
            last_success_at: null,
          }),
        ],
      },
    });

    render(
      <MemoryRouter>
        <SourcesPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("The Example Times")).toBeInTheDocument();
    expect(screen.getByText("Daily Sample")).toBeInTheDocument();
    expect(screen.getByText("每日样例")).toBeInTheDocument();
    // 国家/类型/状态中文标签至少出现一次(表格行 + 筛选下拉)
    expect(screen.getAllByText("美国").length).toBeGreaterThan(0);
    expect(screen.getAllByText("英国").length).toBeGreaterThan(0);
    expect(screen.getAllByText("报纸").length).toBeGreaterThan(0);
    expect(screen.getAllByText("网络媒体").length).toBeGreaterThan(0);
    expect(screen.getAllByText("正常").length).toBeGreaterThan(0);
    expect(screen.getAllByText("失败").length).toBeGreaterThan(0);
    // 表头齐全
    for (const col of ["名称", "国家", "类型", "健康状态", "最近采集时间"]) {
      expect(screen.getByText(col)).toBeInTheDocument();
    }
  });

  it("接口报错时展示后端 message", async () => {
    stubSourcesPage({ code: 9001, data: null, message: "服务器内部错误" }, 500);

    render(
      <MemoryRouter>
        <SourcesPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("服务器内部错误");
  });
});
