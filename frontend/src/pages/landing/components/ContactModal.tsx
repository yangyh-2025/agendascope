interface ContactModalProps {
  open: boolean;
  onClose: () => void;
  email: string;
}

/** 联系我们弹窗:先显示邮箱,再点邮箱才跳转 mailto(保护邮箱不被爬虫直接抓)。 */
export default function ContactModal({ open, onClose, email }: ContactModalProps) {
  if (!open) return null;
  return (
    <div
      className="lp-modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="lp-modal-title"
    >
      <div className="lp-modal" onClick={(e) => e.stopPropagation()}>
        <button
          className="lp-modal-close"
          onClick={onClose}
          aria-label="关闭"
        >
          ×
        </button>
        <h3 id="lp-modal-title" className="lp-modal-title">联系我们</h3>
        <p className="lp-modal-lede">
          如果你对 AgendaScope 感兴趣,或希望洽谈合作、获取私有化部署方案,
          欢迎通过以下邮箱联系:
        </p>
        <a href={`mailto:${email}`} className="lp-modal-email">
          {email}
        </a>
        <p className="lp-modal-hint">
          点击邮箱即可唤起本地邮件客户端
        </p>
      </div>
    </div>
  );
}
