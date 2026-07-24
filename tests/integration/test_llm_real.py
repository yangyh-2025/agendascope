"""真实模型推理集成测试（T2.12–T2.15 核心用例）。

真实加载 models/Qwen2.5-0.5B-Instruct（cpu-dev 档）跑真实推理，禁止 Mock。
模型未下载时跳过（CI 无权重环境）；本地开发与验收必须实际跑通。
实测延迟记录到 CHANGELOG（性能目标：CPU 单议题 P95 ≤60s，估算）。
"""
import time

import pytest

from app.llm.annotator import TopicAnnotator
from app.llm.engine import LLMEngine
from app.llm.prompts import DEFAULT_CATEGORIES
from app.llm.settings import LLMSettings

TITLES = [
    "俄乌双方就停火协议展开新一轮谈判",
    "俄乌谈判在伊斯坦布尔重启 停火成焦点",
    "乌克兰与俄罗斯代表就停火条件交换意见",
    "多方斡旋推动俄乌停火谈判取得进展",
    "欧洲多国呼吁俄乌尽快达成停火",
    "Kremlin confirms new round of ceasefire talks with Ukraine",
]
TOP_WORDS = ["停火", "谈判", "俄乌", "斡旋", "ceasefire"]


@pytest.fixture(scope="session")
def real_annotator():
    settings = LLMSettings()
    engine = LLMEngine(settings)
    if not engine.model_dir_exists():
        pytest.skip(f"模型未下载: {settings.resolved_model_dir()}（需先下载 Qwen2.5-0.5B-Instruct）")
    engine.load()
    return TopicAnnotator(engine=engine, settings=settings)


def test_real_naming(real_annotator):
    result = real_annotator.name_topic(TITLES, TOP_WORDS)
    assert result.success, f"真实推理命名应通过 JSON Schema 校验（含 1 次重试）: {result.error}"
    name = str(result.value)
    assert 2 <= len(name) <= 60
    assert not name.startswith("关键词:"), "LLM 命名不得是兜底标签"
    assert result.model_name == "Qwen2.5-0.5B-Instruct"
    assert result.prompt_version == "topic-naming-v1"
    assert result.latency_s > 0


def test_real_classification(real_annotator):
    result = real_annotator.classify_topic(TITLES, TOP_WORDS, name="俄乌停火谈判")
    assert result.success, f"真实推理分类应命中预置体系: {result.error}"
    assert result.value in DEFAULT_CATEGORIES


def test_real_summary(real_annotator):
    result = real_annotator.summarize_topic(TITLES, TOP_WORDS, name="俄乌停火谈判")
    assert result.success, f"真实推理摘要应通过校验: {result.error}"
    summary = str(result.value)
    assert 10 <= len(summary) <= 500
    assert any("一" <= ch <= "鿿" for ch in summary), "摘要必须是中文"


def test_real_full_annotation_latency(real_annotator):
    """完整标注（命名+分类+摘要）真实推理计时，输出实测数据供性能核对。"""
    started = time.monotonic()
    annotation = real_annotator.annotate_topic(TITLES, TOP_WORDS)
    elapsed = time.monotonic() - started
    latencies = {
        "naming_s": round(annotation.name.latency_s, 2),
        "category_s": round(annotation.category.latency_s, 2),
        "summary_s": round(annotation.summary.latency_s, 2) if annotation.summary else None,
        "total_s": round(elapsed, 2),
    }
    print(f"\n[实测] Qwen2.5-0.5B CPU 单议题标注延迟: {latencies}")
    assert not annotation.degraded, f"全链路真实推理不应降级: {annotation.name.error}"
    # CPU 估算目标单议题 P95 ≤60s（完整标注为 3 次生成，放宽到 180s 门禁）
    assert elapsed < 180, f"完整标注耗时 {elapsed:.1f}s 超出门禁"
