import { useCallback, useEffect, useState } from "react";
import {
  createApiKey,
  listApiKeys,
  revokeApiKey,
  type ApiKeyCreated,
  type ApiKeyItem,
} from "../../api/apiKeys";
import { ApiError } from "../../api/client";

function fmt(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 16).replace("T", " ");
}

function keyStatus(k: ApiKeyItem): { label: string; cls: string } {
  if (k.revoked_at) return { label: "已吊销", cls: "revoked" };
  if (k.expires_at && new Date(k.expires_at) < new Date()) return { label: "已过期", cls: "expired" };
  return { label: "使用中", cls: "active" };
}

export default function KeysPage() {
  const [items, setItems] = useState<ApiKeyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [rateLimit, setRateLimit] = useState(60);
  const [expiresDays, setExpiresDays] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listApiKeys()
      .then((r) => setItems(r.items))
      .catch((e) => setError(e instanceof ApiError ? e.message : "加载失败"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const r = await createApiKey({
        name: name.trim(),
        rate_limit_per_minute: rateLimit,
        expires_in_days: expiresDays ? Number(expiresDays) : undefined,
      });
      setCreated(r);
      setShowCreate(false);
      setName("");
      setExpiresDays("");
      load();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (k: ApiKeyItem) => {
    if (!confirm(`确认吊销 Key「${k.name}」？吊销后立即失效，不可恢复。`)) return;
    try {
      await revokeApiKey(k.id);
      load();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "吊销失败");
    }
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div>
      <div className="dev-keys-header">
        <div>
          <h1>我的 API Key</h1>
          <p>创建 Key 后立即复制保存——服务端只存哈希，无法找回。</p>
        </div>
        <button className="dev-keys-create-btn" onClick={() => setShowCreate(true)}>
          + 新建 Key
        </button>
      </div>

      {created && (
        <div className="dev-key-created">
          <h4>✓ Key 已创建（仅此一次显示完整 Key）</h4>
          <div className="dev-key-created-key">{created.api_key}</div>
          <button className="dev-btn primary" onClick={() => handleCopy(created.api_key)}>
            {copied ? "已复制" : "复制 Key"}
          </button>
          <p className="dev-key-created-warn">
            ⚠️ 关闭此提示后将无法再次查看完整 Key，请妥善保存。
          </p>
          <button className="dev-btn" style={{ marginTop: 8 }} onClick={() => setCreated(null)}>
            我已保存，关闭
          </button>
        </div>
      )}

      {error && <p style={{ color: "#f56c6c" }}>{error}</p>}
      {!error && loading && <p>加载中…</p>}
      {!error && !loading && items.length === 0 && (
        <div className="dev-keys-empty">
          暂无 API Key，点右上角"新建 Key"开始接入。
        </div>
      )}

      {items.length > 0 && (
        <div className="dev-keys-table">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>前缀</th>
                <th>限流/分</th>
                <th>最后使用</th>
                <th>过期时间</th>
                <th>状态</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((k) => {
                const st = keyStatus(k);
                return (
                  <tr key={k.id}>
                    <td>{k.name}</td>
                    <td>
                      <span className="dev-key-prefix">{k.prefix}…</span>
                    </td>
                    <td>{k.rate_limit_per_minute}</td>
                    <td>{fmt(k.last_used_at)}</td>
                    <td>{fmt(k.expires_at)}</td>
                    <td>
                      <span className={`dev-key-status ${st.cls}`}>{st.label}</span>
                    </td>
                    <td>
                      {!k.revoked_at && (
                        <button className="dev-key-revoke-btn" onClick={() => handleRevoke(k)}>
                          吊销
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <div className="dev-modal-mask" onClick={() => setShowCreate(false)}>
          <div className="dev-modal" onClick={(e) => e.stopPropagation()}>
            <h3>新建 API Key</h3>
            <div className="dev-modal-field">
              <label>名称（用于区分用途，如"研究项目"）</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：毕业论文分析"
                maxLength={100}
              />
            </div>
            <div className="dev-modal-field">
              <label>每分钟限流（1-600，默认 60）</label>
              <input
                type="number"
                value={rateLimit}
                onChange={(e) => setRateLimit(Math.max(1, Math.min(600, Number(e.target.value) || 60)))}
                min={1}
                max={600}
              />
            </div>
            <div className="dev-modal-field">
              <label>有效期（天，留空为永久）</label>
              <input
                type="number"
                value={expiresDays}
                onChange={(e) => setExpiresDays(e.target.value)}
                placeholder="例如 90"
                min={1}
                max={3650}
              />
            </div>
            <div className="dev-modal-actions">
              <button className="dev-btn" onClick={() => setShowCreate(false)}>
                取消
              </button>
              <button
                className="dev-btn primary"
                onClick={handleCreate}
                disabled={creating || !name.trim()}
              >
                {creating ? "创建中…" : "创建"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
