/** 日志查看器：读应用日志文件尾部并按级别过滤；4004（未启用文件日志）显示配置引导。 */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import { fetchSystemLogs, type LogLevel, type LogTail } from "../../api/systemAdmin";

const LEVELS: LogLevel[] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LINE_OPTIONS = [100, 200, 500, 1000];
/** 后端 CODE_DATA_INSUFFICIENT：日志文件输出未启用（LOG_FILE_PATH 未配置）。 */
const CODE_LOG_DISABLED = 4004;

export default function LogsPanel() {
  const [level, setLevel] = useState<LogLevel>("INFO");
  const [lines, setLines] = useState(200);
  const [data, setData] = useState<LogTail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback((lv: LogLevel, n: number) => {
    setError(null);
    setDisabled(false);
    setLoading(true);
    fetchSystemLogs(lv, n)
      .then(setData)
      .catch((err) => {
        setData(null);
        if (err instanceof ApiError && err.code === CODE_LOG_DISABLED) {
          setDisabled(true);
        } else {
          setError(err instanceof ApiError ? err.message : "日志加载失败");
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(level, lines);
    // 仅首载自动查询，之后由“查询”按钮触发，避免切换下拉时连续打请求
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section className="sys-panel">
      <div className="sys-panel-head">
        <h3>日志查看</h3>
      </div>

      <div className="sys-filters">
        <label>
          <span>级别</span>
          <select value={level} onChange={(e) => setLevel(e.target.value as LogLevel)}>
            {LEVELS.map((lv) => (
              <option key={lv} value={lv}>{lv}</option>
            ))}
          </select>
        </label>
        <label>
          <span>行数</span>
          <select value={lines} onChange={(e) => setLines(Number(e.target.value))}>
            {LINE_OPTIONS.map((n) => (
              <option key={n} value={n}>最近 {n} 行</option>
            ))}
          </select>
        </label>
        <button type="button" disabled={loading} onClick={() => load(level, lines)}>
          {loading ? "查询中…" : "查询"}
        </button>
      </div>

      {disabled && (
        <div className="sys-notice" role="alert">
          <p>文件日志输出未启用，无法在线查看日志。</p>
          <p>
            请在部署环境配置 <code>LOG_FILE_PATH</code>（指向应用日志文件路径）并重启服务后，
            再回到本页查看；当前可通过容器/进程标准输出获取日志。
          </p>
        </div>
      )}
      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && !disabled && !data && <p className="page-loading">加载中…</p>}

      {data && (
        <>
          <p className="sys-log-meta">
            {data.log_file} · 命中 {data.matched} 行（≥ {data.level}）
            {data.truncated && " · 已截断，仅显示尾部"}
          </p>
          {data.items.length === 0 ? (
            <p className="sys-empty">该级别下暂无日志</p>
          ) : (
            <pre className="log-viewer">{data.items.join("\n")}</pre>
          )}
        </>
      )}
    </section>
  );
}
