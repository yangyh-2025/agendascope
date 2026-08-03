"""TopicAnnotator 编排逻辑单元测试（T2.13–T2.16）。

打桩层：仅替换引擎的 ``_generate`` 原始文本生成（等价于打桩模型前向），
prompt 构建、JSON 解析重试、降级链、健康监控、兜底标签全部为真实逻辑。
真实模型推理用例见 tests/integration/test_llm_real.py。
"""

import pytest

from app.llm.annotator import NAMING_FALLBACK, NAMING_LLM, TopicAnnotator
from app.llm.engine import LLMEngine
from app.llm.health import DegradationMonitor
from app.llm.prompts import DEFAULT_CATEGORIES
from app.llm.settings import LLMSettings

TITLES = [
    "俄乌双方就停火协议展开新一轮谈判",
    "俄乌谈判在伊斯坦布尔重启 停火成焦点",
    "乌克兰与俄罗斯代表就停火条件交换意见",
    "多方斡旋推动俄乌停火谈判取得进展",
    "欧洲多国呼吁俄乌尽快达成停火",
]
TOP_WORDS = ["停火", "谈判", "俄乌"]


class StubEngine(LLMEngine):
    """脚本化原始输出的引擎：load 为空操作，_generate 按队列吐出预设文本。"""

    def __init__(self, outputs: list[str], settings: LLMSettings | None = None):
        super().__init__(settings or LLMSettings())
        self._outputs = list(outputs)
        self.calls: list[str] = []

    @property
    def is_loaded(self) -> bool:  # type: ignore[override]
        return True

    @property
    def model_name(self) -> str:  # type: ignore[override]
        return "stub-qwen"

    def load(self) -> None:
        return None

    def _generate(self, messages: list[dict[str, str]], max_new_tokens: int | None = None) -> str:
        self.calls.append(messages[-1]["content"])
        if not self._outputs:
            raise AssertionError("StubEngine 输出脚本已耗尽")
        return self._outputs.pop(0)


def _annotator(outputs: list[str], monitor: DegradationMonitor | None = None) -> TopicAnnotator:
    settings = LLMSettings(health_min_samples=5, health_window_size=10)
    engine = StubEngine(outputs, settings)
    return TopicAnnotator(
        engine=engine,
        monitor=monitor or DegradationMonitor(settings),
        settings=settings,
    )


def test_naming_success_records_version_and_method():
    annotator = _annotator(['{"name": "俄乌停火谈判"}'])
    result = annotator.name_topic(TITLES, TOP_WORDS)
    assert result.success
    assert result.value == "俄乌停火谈判"
    assert result.naming_method == NAMING_LLM
    assert result.model_name == "stub-qwen"
    assert result.prompt_version == "topic-naming-v2"


def test_naming_retry_once_after_parse_failure():
    annotator = _annotator(["这不是 JSON", '{"name": "俄乌停火谈判"}'])
    result = annotator.name_topic(TITLES, TOP_WORDS)
    assert result.success, "首次解析失败重试 1 次后应成功"
    assert result.value == "俄乌停火谈判"
    assert len(annotator.engine.calls) == 2  # type: ignore[attr-defined]


def test_naming_single_point_fallback_after_retry_exhausted():
    annotator = _annotator(["垃圾输出一", "垃圾输出二"])
    result = annotator.name_topic(TITLES, TOP_WORDS)
    assert not result.success, "重试 1 次仍失败应单点降级"
    assert result.naming_method == NAMING_FALLBACK
    assert str(result.value).startswith("关键词:")
    assert "停火" in str(result.value)


def test_fallback_when_monitor_degraded_skips_inference():
    settings = LLMSettings(health_min_samples=5, health_window_size=10)
    monitor = DegradationMonitor(settings)
    monitor.mark_unavailable("模型加载失败")
    annotator = _annotator([], monitor=monitor)
    result = annotator.name_topic(TITLES, TOP_WORDS)
    assert result.naming_method == NAMING_FALLBACK
    assert not annotator.engine.calls, "降级期不得调用推理"  # type: ignore[attr-defined]


def test_unavailable_engine_triggers_fallback_and_monitor():
    class BrokenEngine(StubEngine):
        @property
        def is_loaded(self) -> bool:  # type: ignore[override]
            return False

        def load(self) -> None:
            from app.llm.errors import LLMUnavailableError

            raise LLMUnavailableError("模型目录不存在")

    settings = LLMSettings()
    engine = BrokenEngine([], settings)
    monitor = DegradationMonitor(settings)
    annotator = TopicAnnotator(engine=engine, monitor=monitor, settings=settings)
    result = annotator.name_topic(TITLES, TOP_WORDS)
    assert result.naming_method == NAMING_FALLBACK
    assert monitor.degraded is True, "加载失败应立即判降级"


def test_classification_within_taxonomy():
    annotator = _annotator(['{"category": "政治安全"}'])
    result = annotator.classify_topic(TITLES, TOP_WORDS, name="俄乌停火谈判")
    assert result.success
    assert result.value in DEFAULT_CATEGORIES


def test_classification_drift_falls_back_to_other():
    annotator = _annotator(['{"category": "体育娱乐"}', '{"category": "体育娱乐"}'])
    result = annotator.classify_topic(TITLES, TOP_WORDS)
    assert not result.success, "自造类别按失败处理"
    assert result.value == "其他", "分类降级兜底为「其他」"


def test_summary_success_and_degraded_none():
    ok = _annotator(['{"summary": "俄乌双方重启停火谈判。多方斡旋下谈判取得进展。"}'])
    result = ok.summarize_topic(TITLES, TOP_WORDS, name="俄乌停火谈判")
    assert result.success and "谈判" in str(result.value)

    settings = LLMSettings()
    monitor = DegradationMonitor(settings)
    monitor.mark_unavailable("x")
    degraded = TopicAnnotator(
        engine=StubEngine([], settings), monitor=monitor, settings=settings
    ).summarize_topic(TITLES, TOP_WORDS)
    assert degraded.value is None, "降级期摘要不伪造内容"


def test_annotate_topic_full_pipeline():
    annotator = _annotator([
        '{"name": "俄乌停火谈判"}',
        '{"category": "政治安全"}',
        '{"summary": "俄乌双方重启停火谈判。多方斡旋下谈判取得进展。"}',
    ])
    annotation = annotator.annotate_topic(TITLES, TOP_WORDS)
    assert not annotation.degraded
    assert annotation.name.value == "俄乌停火谈判"
    assert annotation.category.value == "政治安全"
    assert annotation.summary is not None and annotation.summary.success
    assert annotation.keywords, "关键词随标注一并产出"
    assert annotation.inputs["titles"] == TITLES


def test_token_budget_truncates_titles():
    settings = LLMSettings(max_context_tokens=50)
    engine = StubEngine(['{"name": "俄乌停火谈判"}'], settings)
    annotator = TopicAnnotator(engine=engine, settings=settings)
    long_titles = ["很长的标题" * 30 for _ in range(10)]
    result = annotator.name_topic(long_titles, TOP_WORDS)
    assert result.success
    prompt_sent = engine.calls[0]
    assert prompt_sent.count("很长的标题") < 30 * 10, "超出预算的标题应被裁剪"
    assert "1. " in prompt_sent, "至少保留首条标题"


@pytest.mark.parametrize("bad_output", ["{}", '{"name": ""}', '{"name": "一"}'])
def test_invalid_naming_schema_triggers_retry_then_fallback(bad_output: str):
    annotator = _annotator([bad_output, bad_output])
    result = annotator.name_topic(TITLES, TOP_WORDS)
    assert result.naming_method == NAMING_FALLBACK
