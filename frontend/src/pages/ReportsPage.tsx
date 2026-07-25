/** 报告中心页（T4.18）：报告导出列表 + 新建。 */
import { useState } from "react";
import { request } from "../api/client";
import "./ReportsPage.css";

export default function ReportsPage() {
  const [template, setTemplate] = useState("issue_deep");
  const [format, setFormat] = useState("pdf");
  const [days, setDays] = useState(7);
  const [status, setStatus] = useState("");

  const submit = () => {
    request("/api/v1/report-exports", { method: "POST", body: JSON.stringify({ template, format, scope: { days } }) })
      .then((r: any) => setStatus(`已创建: ${r.data?.id}`))
      .catch((err: any) => setStatus(`创建失败: ${err.message}`));
  };

  return (
    <div className="reports-page">
      <h1>报告中心</h1>
      <div className="report-form">
        <select value={template} onChange={(e) => setTemplate(e.target.value)}>
          <option value="issue_deep">议题深度报告</option>
          <option value="cross_country_brief">跨国对比简报</option>
          <option value="weekly_monitor">周期监测周报</option>
        </select>
        <select value={format} onChange={(e) => setFormat(e.target.value)}>
          <option value="pdf">PDF</option>
          <option value="docx">Word</option>
        </select>
        <input type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} min={1} max={90} />
        <span className="watermark-hint">报告含水印"由 AgendaScope 观澜生成 + 数据口径声明"；不含全文，仅标题与摘录。</span>
        <button onClick={submit}>生成报告</button>
        {status && <p className="status-msg">{status}</p>}
      </div>
    </div>
  );
}
