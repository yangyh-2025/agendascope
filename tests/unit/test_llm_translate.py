"""app.alerting.llm_translate 纯逻辑单元测试（T4.19 LLM 翻译）。

用 _FakeTranslateLLM 替身：
  - llm 可用 → 返回译文
  - llm=None / monitor.degraded / LLM 调用抛错 → 返回原文（不阻塞订阅发送）
  - db=None 时仍可翻译（只是不写留痕）
"""
from app.alerting.llm_translate import llm_translate

TEXT = "US announces new export controls on advanced chips to China."


class _FakeTranslateLLM:
    """翻译替身：可控 translated，degraded/fail 可开关。"""

    class _Engine:
        model_name = "test-model"
        is_loaded = True

        def __init__(self, parent):
            self._parent = parent

        def generate_structured(self, system, user, output_model):
            from app.llm.errors import LLMError
            from app.llm.schemas import TranslateOutput

            if self._parent._fail:
                raise LLMError("注入失败")
            return TranslateOutput(**self._parent._result), 0.01

    class _Monitor:
        def __init__(self, degraded):
            self.degraded = degraded

        def record(self, *a, **k):
            pass

    def __init__(self, translated="美国宣布对华先进芯片出口实施新管制。", degraded=False, fail=False):
        self._result = {"translated": translated}
        self._degraded = degraded
        self._fail = fail
        self.engine = self._Engine(self)
        self.monitor = self._Monitor(degraded)


class TestLLMTranslate:
    def test_llm_available_returns_translation(self, db):
        llm = _FakeTranslateLLM()
        assert llm_translate(db, TEXT, target_lang="zh", llm_annotator=llm) == "美国宣布对华先进芯片出口实施新管制。"

    def test_llm_available_writes_judgement(self, db):
        from app.models.llm import LLMJudgement

        llm = _FakeTranslateLLM()
        llm_translate(db, TEXT, target_lang="zh", llm_annotator=llm)
        db.flush()
        rows = db.query(LLMJudgement).all()
        assert len(rows) == 1
        assert rows[0].task_type == "translate"
        assert rows[0].success is True
        assert rows[0].output_payload == {"translated": "美国宣布对华先进芯片出口实施新管制。"}

    def test_llm_none_returns_original(self, db):
        assert llm_translate(db, TEXT, target_lang="zh", llm_annotator=None) == TEXT

    def test_llm_degraded_returns_original(self, db):
        llm = _FakeTranslateLLM(degraded=True)
        assert llm_translate(db, TEXT, target_lang="zh", llm_annotator=llm) == TEXT

    def test_llm_error_returns_original(self, db):
        llm = _FakeTranslateLLM(fail=True)
        assert llm_translate(db, TEXT, target_lang="zh", llm_annotator=llm) == TEXT

    def test_db_none_still_translates(self):
        llm = _FakeTranslateLLM()
        assert llm_translate(None, TEXT, target_lang="zh", llm_annotator=llm) == "美国宣布对华先进芯片出口实施新管制。"

    def test_non_zh_target_returns_original(self, db):
        llm = _FakeTranslateLLM()
        assert llm_translate(db, TEXT, target_lang="en", llm_annotator=llm) == TEXT

    def test_empty_text_returns_empty(self, db):
        llm = _FakeTranslateLLM()
        assert llm_translate(db, "", target_lang="zh", llm_annotator=llm) == ""
