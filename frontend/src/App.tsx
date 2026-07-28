import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import AnalyticsPage from "./pages/AnalyticsPage";
import DashboardPage from "./pages/DashboardPage";
import EventDetailPage from "./pages/EventDetailPage";
import EventsPage from "./pages/EventsPage";
import LoginPage from "./pages/LoginPage";
import MapPage from "./pages/MapPage";
import ReportsPage from "./pages/ReportsPage";
import SourcesPage from "./pages/SourcesPage";
import SystemPage from "./pages/SystemPage";
import TopicDetailPage from "./pages/TopicDetailPage";
import TopicsPage from "./pages/TopicsPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route path="map" element={<MapPage />} />
            <Route path="topics" element={<TopicsPage />} />
            <Route path="topics/:id" element={<TopicDetailPage />} />
            <Route path="events" element={<EventsPage />} />
            <Route path="events/:id" element={<EventDetailPage />} />
            <Route path="analytics" element={<AnalyticsPage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="sources" element={<SourcesPage />} />
            <Route path="system" element={<SystemPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
