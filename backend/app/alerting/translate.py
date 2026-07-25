"""离线翻译 HTTP 客户端（T4.19，argos-translate 独立容器集成）。

argos-translate 为 AGPL-3.0，必须以独立容器进程隔离（不 import argostranslate 包到
backend），后端经 HTTP POST 调用本地 argos 服务（默认 http://argos:5000/translate）。

翻译失效返回原文不阻塞（try/except + 记 warning）。
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

logger = get_logger("alerting.translate")


@dataclass(frozen=True)
class TranslateConfig:
    """argos-translate 容器 HTTP 接入配置。"""

    base_url: str = "http://argos:5000"
    timeout_seconds: float = 10.0
    default_source: str = "zh"
    default_target: str = "en"


def _normalize_locale(locale: str) -> str:
    """将 zh-CN / en-US 等 BCP-47 转为 argos 期望的 zh / en。"""
    if not locale:
        return "zh"
    return locale.split("-")[0].lower()


def translate_text(
    text: str,
    source_locale: str,
    target_locale: str,
    cfg: TranslateConfig | None = None,
    http_client: httpx.Client | None = None,
) -> str:
    """调用 argos 服务翻译单条文本；失败返回原文。

    Args:
        text: 待翻译文本（空串直接返回）
        source_locale: 源语言（zh-CN / zh / en-US / en）
        target_locale: 目标语言
        cfg: TranslateConfig；None 时用默认
        http_client: 测试注入；None 时新建
    """
    if not text:
        return text
    cfg = cfg or TranslateConfig()
    source = _normalize_locale(source_locale)
    target = _normalize_locale(target_locale)
    if source == target:
        return text

    payload = {"q": text, "source": source, "target": target, "format": "text"}
    url = cfg.base_url.rstrip("/") + "/translate"

    client = http_client
    own = False
    if client is None:
        client = httpx.Client(timeout=cfg.timeout_seconds, follow_redirects=False)
        own = True
    try:
        resp = client.post(url, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "argos_translate_http_error",
                status=resp.status_code, body=resp.text[:200],
            )
            return text
        data = resp.json()
        translated = data.get("translatedText") or data.get("translated_text") or ""
        return str(translated) if translated else text
    except Exception as exc:  # noqa: BLE001 翻译失效显示原文不阻塞
        logger.warning("argos_translate_unavailable", error=str(exc)[:200])
        return text
    finally:
        if own:
            client.close()


def translate_topic_name(
    name_zh: str | None,
    name_auto: str,
    target_locale: str,
    cfg: TranslateConfig | None = None,
    http_client: httpx.Client | None = None,
) -> str:
    """议题名中→英 / 英→中 快捷入口；任一侧缺失时返回另一侧。"""
    target = _normalize_locale(target_locale)
    if target == "zh":
        # 英→中：通常 name_zh 已有；缺失时回退 auto（不强行调 argos 英→中避免质量差）
        return name_zh or name_auto
    # 中→英：name_zh → en
    source_text = name_zh or name_auto
    if not source_text:
        return name_auto
    return translate_text(
        source_text, source_locale="zh", target_locale=target,
        cfg=cfg, http_client=http_client,
    )


def translate_summary(
    summary_zh: str | None,
    target_locale: str,
    cfg: TranslateConfig | None = None,
    http_client: httpx.Client | None = None,
) -> str:
    """摘要中→英（订阅日报/周报展示用）；空摘要返回空串。"""
    if not summary_zh:
        return ""
    target = _normalize_locale(target_locale)
    if target == "zh":
        return summary_zh
    return translate_text(
        summary_zh, source_locale="zh", target_locale=target,
        cfg=cfg, http_client=http_client,
    )


__all__ = [
    "TranslateConfig",
    "translate_summary",
    "translate_text",
    "translate_topic_name",
]
