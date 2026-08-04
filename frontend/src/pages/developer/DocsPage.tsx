import { DOC_SECTIONS, ERROR_CODES_DOC, OPEN_API_BASE } from "../../api/openApiDocs";

export default function DocsPage() {
  return (
    <div className="dev-docs">
      <h1>AgendaScope 数据开放 API</h1>
      <p className="dev-docs-intro">
        Base URL: <code>{OPEN_API_BASE}</code>
        <br />
        本平台持续采集 172 国 408 个主流媒体的舆情数据，识别议题、追踪议程、分析实体关系。
        所有数据以 RESTful API 形式开放，使用 X-API-Key 鉴权，每分钟独立限流。
      </p>

      {DOC_SECTIONS.map((sec) => (
        <section key={sec.id} className="dev-docs-section" id={sec.id}>
          <h2>{sec.title}</h2>
          <p>{sec.description}</p>
          {sec.endpoints.map((ep, i) => (
            <div key={i} className="dev-endpoint">
              <div className="dev-endpoint-line">
                <span className="dev-method">{ep.method}</span>
                <span className="dev-path">{ep.path}</span>
              </div>
              <div className="dev-summary">{ep.summary}</div>
              {ep.params && ep.params.length > 0 && (
                <div className="dev-params">
                  <table>
                    <thead>
                      <tr>
                        <th>参数</th>
                        <th>类型</th>
                        <th>说明</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ep.params.map((p) => (
                        <tr key={p.name}>
                          <td>
                            <code>{p.name}</code>
                          </td>
                          <td>{p.type}</td>
                          <td>{p.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {ep.example && <div className="dev-example">{ep.example}</div>}
            </div>
          ))}
        </section>
      ))}

      <section className="dev-errors">
        <h2>响应结构 & 错误码</h2>
        <pre>{ERROR_CODES_DOC}</pre>
      </section>
    </div>
  );
}
