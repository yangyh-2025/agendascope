"""AgendaScope 预警、订阅与报告模块（Phase 4 M4-3，T4.14-T4.17+T4.19）。

子模块：
- engine        15 min 周期评估引擎（三条件 AND 叠加 + 防抖 + 预警风暴）
- notifier      通知通道（站内 / SMTP 邮件 / Webhook，指数退避 + 降级）
- subscription  日报/周报订阅推送 + 退订
- report        报告导出（议题深度 / 跨国对比 / 周期周报，PDF/DOCX，水印+口径声明）
- translate     argos-translate 离线翻译 HTTP 客户端（独立容器调用，失效返回原文）
"""
