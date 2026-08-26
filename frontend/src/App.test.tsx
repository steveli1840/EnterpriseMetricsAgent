import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("Analytical Workbench", () => {
  it("shows empty state until a conversation is created", () => {
    render(<App demoMode />);

    expect(screen.getAllByText("MetricLens").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/点击.*新建分析.*开始/)).toBeInTheDocument();
  });

  it("creates a new analysis and shows governed answer with evidence rail", async () => {
    const user = userEvent.setup();
    render(<App demoMode />);

    await user.click(screen.getByText("＋ 新建分析"));

    const textarea = screen.getByPlaceholderText("输入业务问题…");
    await user.type(textarea, "各州已交付收入");
    await user.click(screen.getByText("发送"));

    expect((await screen.findAllByText("已交付收入")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("heading", { name: "证据轨" })).toBeInTheDocument();

    await user.click(screen.getByText(/查看 SQL|收起 SQL/));
    expect(screen.getByText(/SELECT customer_state/)).toBeInTheDocument();
  });

  it("switches between governance sections", async () => {
    const user = userEvent.setup();
    render(<App demoMode />);
    await user.click(screen.getByRole("button", { name: "指标" }));
    expect(screen.getByRole("heading", { name: "指标目录" })).toBeInTheDocument();
  });
});
