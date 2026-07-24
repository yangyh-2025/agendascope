"""T2.1 语言识别单元测试：真实加载 lid.176 跑真实推理（模型缺失时跳过）。"""
import pytest

from app.nlp.config import get_nlp_settings
from app.nlp.language import LanguageDetector, _normalize_label

MODEL_PATH = get_nlp_settings().lid_model_path
pytestmark = pytest.mark.skipif(not MODEL_PATH.exists(), reason="lid.176 模型未下载（models/）")


@pytest.fixture(scope="module")
def detector():
    return LanguageDetector()


def test_detect_english(detector):
    result = detector.detect("The government announced a new climate policy yesterday.")
    assert result.language == "en"
    assert result.confidence >= 0.8
    assert not result.low_confidence


def test_detect_chinese(detector):
    result = detector.detect("国务院常务会议昨日审议通过了新的能源发展规划。")
    assert result.language == "zh"
    assert result.confidence >= 0.8


def test_detect_arabic(detector):
    result = detector.detect("أعلنت الحكومة عن سياسة جديدة للطاقة المتجددة في البلاد.")
    assert result.language == "ar"


def test_detect_russian(detector):
    result = detector.detect("Правительство объявило о новой политике в области климата.")
    assert result.language == "ru"


def test_detect_japanese(detector):
    result = detector.detect("政府は昨日、新しい気候変動政策を発表した。")
    assert result.language == "ja"


def test_detect_article_prefers_content(detector):
    result = detector.detect_article(
        "News", "Die Regierung hat gestern eine neue Klimapolitik angekündigt.", None, "en"
    )
    assert result.language == "de"


def test_low_confidence_fallback_to_source_default(detector):
    """置信度低于阈值：回落源默认语言、low_confidence 置位、原始判定与置信度留痕。"""
    strict = LanguageDetector(threshold=1.01)  # 阈值高于任何可能置信度，强制走回落路径
    result = strict.detect("The parliament passed the energy bill.", source_default="fr")
    assert result.language == "fr"
    assert result.low_confidence
    assert 0.0 < result.confidence < 1.01
    assert result.detected_language == "en"  # 模型原始判定留痕备查


def test_empty_text_falls_back(detector):
    result = detector.detect("   ", source_default="en")
    assert result.language == "en"
    assert result.low_confidence
    assert result.confidence == 0.0


def test_newlines_and_long_text(detector):
    text = "央行宣布降息。\n\n" + "这是正文内容。" * 500
    result = detector.detect(text)
    assert result.language == "zh"


def test_normalize_label():
    assert _normalize_label("__label__zh-CN") == "zh"
    assert _normalize_label("__label__en") == "en"
    assert _normalize_label("__label__pt_BR") == "pt"
