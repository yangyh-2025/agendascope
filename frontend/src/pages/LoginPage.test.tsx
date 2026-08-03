import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getStoredTokens } from "../api/client";
import { useAuthStore } from "../stores/auth";
import LoginPage from "./LoginPage";

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>主界面</div>} />
        <Route path="/dashboard" element={<div>主界面</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function stubFetchOnce(body: unknown, status = 200) {
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

describe("登录页", () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ user: null, isAuthenticated: false });
  });

  it("登录成功：保存 token 并跳转主界面", async () => {
    stubFetchOnce({
      code: 0,
      message: "ok",
      data: {
        access_token: "access-1",
        refresh_token: "refresh-1",
        expires_in: 1800,
        user: { id: "u1", username: "admin", display_name: "管理员", role: "admin" },
      },
    });

    renderLogin();
    await userEvent.type(screen.getByLabelText("账号"), "admin");
    await userEvent.type(screen.getByLabelText("密码"), "Admin12345");
    await userEvent.click(screen.getByRole("button", { name: "登 录" }));

    expect(await screen.findByText("主界面")).toBeInTheDocument();
    expect(getStoredTokens()).toEqual({
      access_token: "access-1",
      refresh_token: "refresh-1",
      expires_in: 1800,
    });
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().user?.username).toBe("admin");
  });

  it("登录失败：展示后端返回的 message", async () => {
    stubFetchOnce({ code: 2003, data: null, message: "用户名或密码错误" }, 401);

    renderLogin();
    await userEvent.type(screen.getByLabelText("账号"), "admin");
    await userEvent.type(screen.getByLabelText("密码"), "wrong-pass");
    await userEvent.click(screen.getByRole("button", { name: "登 录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
    expect(getStoredTokens()).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
