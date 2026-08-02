import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import SetupGuard from "./components/SetupGuard";
import AlertRulesPage from "./pages/AlertRulesPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import EventDetailPage from "./pages/EventDetailPage";
import LoginPage from "./pages/LoginPage";
import OverviewPage from "./pages/OverviewPage";
import PersonsPage from "./pages/PersonsPage";
import ReportsPage from "./pages/ReportsPage";
import RevisionsPage from "./pages/RevisionsPage";
import SetupPage from "./pages/SetupPage";
import SourcesPage from "./pages/SourcesPage";
import SystemPage from "./pages/SystemPage";
import TopicDetailPage from "./pages/TopicDetailPage";
import TopicsPage from "./pages/TopicsPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<SetupGuard />}>
            <Route element={<Layout />}>
              <Route index element={<OverviewPage />} />
              {/* 看板+地图已合并到 /,旧路由重定向 */}
              <Route path="map" element={<Navigate to="/" replace />} />
              <Route path="dashboard" element={<Navigate to="/" replace />} />
              {/* 议题分析(议题+议程事件 Tab 整合) */}
              <Route path="topics" element={<TopicsPage />} />
              <Route path="topics/:id" element={<TopicDetailPage />} />
              {/* 时间线已合并到议题页"对比模式",旧路由重定向 */}
              <Route path="timeline" element={<Navigate to="/topics" replace />} />
              {/* 议程事件已合并到议题页 Tab,旧路由重定向到 /topics?tab=events */}
              <Route path="events" element={<Navigate to="/topics?tab=events" replace />} />
              {/* 事件详情独立路由保留,供外链/书签跳转 */}
              <Route path="events/:id" element={<EventDetailPage />} />
              <Route path="revisions" element={<RevisionsPage />} />
              <Route path="persons" element={<PersonsPage />} />
              <Route path="analytics" element={<AnalyticsPage />} />
              <Route path="alerts" element={<AlertRulesPage />} />
              <Route path="reports" element={<ReportsPage />} />
              <Route path="sources" element={<SourcesPage />} />
              <Route path="system" element={<SystemPage />} />
            </Route>
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
