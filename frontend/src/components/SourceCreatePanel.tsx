import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import {
  crawlPreview,
  createSource,
  type CrawlPreviewResult,
  type MediaType,
  type SourceCreatePayload,
} from "../api/sources";
import "./SourceCreatePanel.css";

const MEDIA_TYPES: { value: MediaType; label: string }[] = [
  { value: "newspaper", label: "报纸" },
  { value: "agency", label: "通讯社" },
  { value: "broadcast", label: "广电" },
  { value: "online", label: "网络媒体" },
];

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const fields = (err.detail as { fields?: Record<string, string> } | null)?.fields;
    if (fields) {
      const detail = Object.entries(fields)
        .map(([k, v]) => `${k}: ${v}`)
        .join("；");
      return `${err.message}（${detail}）`;
    }
    return err.message;
  }
  return fallback;
}

/**
 * 自助配源面板（US-02）：粘贴 URL → 试运行（crawl-preview）→ 核对样例 → 确认入库。
 */
export default function SourceCreatePanel({ onCreated }: { onCreated: () => void }) {
  const [url, setUrl] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<CrawlPreviewResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [nameZh, setNameZh] = useState("");
  const [countryCode, setCountryCode] = useState("");
  const [language, setLanguage] = useState("");
  const [mediaType, setMediaType] = useState<MediaType>("online");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const runPreview = async (e: FormEvent) => {
    e.preventDefault();
    if (previewing) return;
    setPreviewing(true);
    setPreviewError(null);
    setPreview(null);
    try {
      const result = await crawlPreview(url.trim());
      setPreview(result);
      // 用域名预填名称，用户可再修改
      if (!name) {
        try {
          setName(new URL(url.trim()).hostname);
        } catch {
          /* URL 非法时交给后端校验报错，此处不预填 */
        }
      }
    } catch (err) {
      setPreviewError(errorMessage(err, "试运行失败"));
    } finally {
      setPreviewing(false);
    }
  };

  const submitCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!preview || creating) return;
    setCreating(true);
    setCreateError(null);
    const entryPoints = preview.resolved_config.entry_points ?? [];
    const payload: SourceCreatePayload = {
      name: name.trim(),
      name_zh: nameZh.trim() || undefined,
      country_code: countryCode.trim().toUpperCase(),
      homepage_url: url.trim(),
      media_type: mediaType,
      language: language.trim(),
      adapter_type: preview.adapter_type,
      collect_mode: "rss",
      crawl_config: preview.resolved_config,
      ...(preview.adapter_type === "rss" && entryPoints[0]
        ? { feed_url: entryPoints[0] }
        : {}),
    };
    try {
      await createSource(payload);
      onCreated();
    } catch (err) {
      setCreateError(errorMessage(err, "入库失败"));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="create-panel">
      <form className="create-preview-form" onSubmit={runPreview}>
        <input
          name="url"
          type="url"
          required
          placeholder="粘贴媒体首页或 RSS 地址，如 https://example.com/feed.xml"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button type="submit" disabled={previewing}>
          {previewing ? "试运行中…" : "试运行"}
        </button>
      </form>

      {previewError && (
        <p className="page-error" role="alert">
          {previewError}
        </p>
      )}

      {preview && (
        <div className="preview-result">
          <p className="preview-meta">
            适配方式 <strong>{preview.adapter_type}</strong> · 耗时 {preview.elapsed_ms} ms · 样例{" "}
            {preview.samples.length} 条
          </p>
          {preview.warnings.length > 0 && (
            <ul className="preview-warnings">
              {preview.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          <table className="preview-samples">
            <thead>
              <tr>
                <th>样例标题</th>
                <th>正文长度</th>
                <th>抽取</th>
              </tr>
            </thead>
            <tbody>
              {preview.samples.map((s) => (
                <tr key={s.url}>
                    <td className="preview-sample-title">{s.title ?? s.url}</td>
                  <td>{s.content_len}</td>
                  <td>{s.ok ? "成功" : "失败"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <form className="create-form" onSubmit={submitCreate}>
            <div className="create-form-grid">
              <label>
                名称 *
                <input value={name} onChange={(e) => setName(e.target.value)} required />
              </label>
              <label>
                中文名
                <input value={nameZh} onChange={(e) => setNameZh(e.target.value)} />
              </label>
              <label>
                国家码 *（ISO 两位大写）
                <input
                  value={countryCode}
                  onChange={(e) => setCountryCode(e.target.value)}
                  placeholder="如 US / CN / GB"
                  pattern="[A-Za-z]{2}"
                  required
                />
              </label>
              <label>
                语言 *（如 en / zh）
                <input
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  minLength={2}
                  required
                />
              </label>
              <label>
                媒体类型 *
                <select
                  value={mediaType}
                  onChange={(e) => setMediaType(e.target.value as MediaType)}
                >
                  {MEDIA_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {createError && (
              <p className="page-error" role="alert">
                {createError}
              </p>
            )}
            <button type="submit" disabled={creating}>
              {creating ? "入库中…" : "确认入库"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
