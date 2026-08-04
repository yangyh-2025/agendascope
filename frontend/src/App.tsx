import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import SetupGuard from "./components/SetupGuard";
import AlertRulesPage from "./pages/AlertRulesPage";
import DeveloperLayout from "./pages/developer/DeveloperLayout";
import DocsPage from "./pages/developer/DocsPage";
import KeysPage from "./pages/developer/KeysPage";
import EventDetailPage from "./pages/EventDetailPage";
import EventsPage from "./pages/EventsPage";
import LandingPage from "./pages/landing/LandingPage";
import LoginPage from "./pages/LoginPage";
import OverviewPage from "./pages/OverviewPage";
import PersonsPage from "./pages/PersonsPage";
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
        {/* 产品介绍首页(公开) */}
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route element={<RequireAuth />}>
          {/* 数据开放平台（独立布局，非主系统子页面） */}
          <Route path="/developer" element={<DeveloperLayout />}>
            <Route index element={<Navigate to="/developer/docs" replace />} />
            <Route path="docs" element={<DocsPage />} />
            <Route path="keys" element={<KeysPage />} />
          </Route>
          <Route element={<SetupGuard />}>
            <Route element={<Layout />}>
              <Route path="dashboard" element={<OverviewPage />} />
              {/* 旧路由重定向,避免书签失效 */}
              <Route path="map" element={<Navigate to="/dashboard" replace />} />
              <Route path="overview" element={<Navigate to="/dashboard" replace />} />
              {/* 议题分析(议题+议程事件拆分为两个独立入口) */}
              <Route path="topics" element={<TopicsPage />} />
              <Route path="topics/:id" element={<TopicDetailPage />} />
              {/* 时间线已合并到议题页"对比模式",旧路由重定向 */}
              <Route path="timeline" element={<Navigate to="/topics" replace />} />
              {/* 议程事件独立列表入口 */}
              <Route path="events" element={<EventsPage />} />
              <Route path="events/:id" element={<EventDetailPage />} />
              <Route path="revisions" element={<RevisionsPage />} />
              <Route path="persons" element={<PersonsPage />} />
              <Route path="alerts" element={<AlertRulesPage />} />
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
