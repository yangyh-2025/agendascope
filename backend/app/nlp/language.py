"""fastText lid.176 语言识别封装（T2.1）。

置信度 < 阈值（默认 0.8）时回落源默认语言：language 落源默认语言，
language_confidence 落模型原始置信度——置信度值本身即低置信留痕（详细设计 2.6
articles.language_confidence COMMENT），detected_language 另存日志备查。
"""
from dataclasses import dataclass

from app.core.logging import get_logger
from app.nlp.config import get_nlp_settings

logger = get_logger("nlp.language")

# 送检文本截断长度：lid.176 为词袋线性模型，头部文本足够判语言，截断控制耗时
_MAX_CHARS = 2000


@dataclass(frozen=True)
class LanguageResult:
    language: str  # 最终采用语言（低置信时=源默认语言）
    confidence: float  # 模型原始置信度（<0.8 即低置信留痕）
    low_confidence: bool
    detected_language: str  # 模型原始判定（回落时备查）


def _normalize_label(label: str) -> str:
    """__label__zh-CN / __label__en → 主语言子标签（与 sources.language BCP-47 主码对齐）。"""
    return label.replace("__label__", "").lower().replace("_", "-").split("-")[0]


class LanguageDetector:
    def __init__(self, model_path: str | None = None, threshold: float | None = None):
        settings = get_nlp_settings()
        self.threshold = threshold if threshold is not None else settings.lang_confidence_threshold
        path = model_path or str(settings.lid_model_path)
        # fasttext-predict 包提供 predict-only 的 fasttext 模块（预编译 wheel，免 C++ 编译），
        # API 与官方 fasttext 对齐；若环境改装官方 fasttext 包同样兼容
        import fasttext

        self._model = fasttext.load_model(path)
        logger.info("lid_model_loaded", path=path, threshold=self.threshold)

    def detect(self, text: str, source_default: str = "en") -> LanguageResult:
        """识别文本语言；低置信回落 source_default 并标记 low_confidence。"""
        cleaned = " ".join((text or "").split())[:_MAX_CHARS]  # fastText predict 不接受换行符
        if not cleaned:
            return LanguageResult(source_default, 0.0, True, "")
        labels, probs = self._model.predict(cleaned, k=1)
        label = labels[0] if isinstance(labels[0], str) else labels[0][0]
        detected = _normalize_label(label)
        confidence = min(float(probs[0] if len(probs) else 0.0), 1.0)
        low = confidence < self.threshold
        language = source_default if low else detected
        if low:
            logger.info(
                "language_low_confidence_fallback",
                detected=detected, confidence=round(confidence, 3), fallback=source_default,
            )
        return LanguageResult(language, confidence, low, detected)

    def detect_article(
        self, title: str, content: str | None, summary: str | None, source_default: str
    ) -> LanguageResult:
        """文章级识别：标题 + 正文头部（正文缺失时用摘要）拼接送检。"""
        body = content or summary or ""
        return self.detect(f"{title}\n{body}", source_default=source_default)
