import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { setSessionExpiredHandler } from "./api/client";
import { useAuthStore } from "./stores/auth";
import { applyCssVariables } from "./theme/tokens";
import "./theme/global.css";

applyCssVariables();

// 会话彻底失效（refresh 被拒）时清空登录态，由路由守卫跳回登录页
setSessionExpiredHandler(() => {
  useAuthStore.getState().expireSession();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
