"""app.alerting.alert_summary 纯逻辑单元测试（T4.14 告警理由摘要）。

用 _FakeAlertLLM 替身（同 test_agenda_merge._FakeMergeLLM 风格）：
  - llm 可用 → 生成中文摘要
  - llm=None / monitor.degraded → 返回 None（调用方维持现状）
  - LLM 调用抛错（LLMError）→ 返回 None（不阻塞告警落库）
留痕写 db（llm_judgements）可选：本测试用 db fixture 验证成功路径留痕。
"""
from app.alerting.alert_summary import generate_alert_summary

DEFAULT_SUMMARY = "美方报道量较基线增长 300% 并进入该国显著性 Top 2，触发预警。"

ALERT_CONTEXT = {
    "rule_name": "美国报道量激增预警",
    "rule_conditions": [
        {"metric": "growth_rate", "value": 300.0, "threshold": 200.0},
        {"metric": "top_n", "value": 2, "threshold": 5.0},
    ],
    "matched_articles": ["美方宣布新一轮对华出口管制措施", "美商务部将多家中企列入实体清单"],
    "country_code": "US",
}


class _FakeAlertLLM:
    """告警摘要替身：可控 summary，degraded/fail 可开关。"""

    class _Engine:
        model_name = "test-model"
        is_loaded = True

        def __init__(self, parent):
            self._parent = parent

        def generate_structured(self, system, user, output_model):
            from app.llm.errors import LLMError
            from app.llm.schemas import AlertSummaryOutput

            if self._parent._fail:
                raise LLMError("注入失败")
            return AlertSummaryOutput(**self._parent._result), 0.01

    class _Monitor:
        def __init__(self, degraded):
            self.degraded = degraded

        def record(self, *a, **k):
            pass

    def __init__(self, summary=DEFAULT_SUMMARY, degraded=False, fail=False):
        self._result = {"summary": summary}
        self._degraded = degraded
        self._fail = fail
        self.engine = self._Engine(self)
        self.monitor = self._Monitor(degraded)


class TestGenerateAlertSummary:
    def test_llm_available_generates_summary(self, db):
        llm = _FakeAlertLLM()
        summary = generate_alert_summary(db, ALERT_CONTEXT, llm_annotator=llm)
        assert summary == DEFAULT_SUMMARY

    def test_llm_available_writes_judgement(self, db):
        from app.models.llm import LLMJudgement

        llm = _FakeAlertLLM()
        generate_alert_summary(db, ALERT_CONTEXT, llm_annotator=llm)
        db.flush()
        rows = db.query(LLMJudgement).all()
        assert len(rows) == 1
        assert rows[0].task_type == "alert_summary"
        assert rows[0].success is True
        assert rows[0].output_payload == {"summary": DEFAULT_SUMMARY}

    def test_llm_none_returns_none(self, db):
        assert generate_alert_summary(db, ALERT_CONTEXT, llm_annotator=None) is None

    def test_llm_degraded_returns_none(self, db):
        llm = _FakeAlertLLM(degraded=True)
        assert generate_alert_summary(db, ALERT_CONTEXT, llm_annotator=llm) is None

    def test_llm_error_returns_none(self, db):
        llm = _FakeAlertLLM(fail=True)
        assert generate_alert_summary(db, ALERT_CONTEXT, llm_annotator=llm) is None

    def test_llm_error_writes_failed_judgement(self, db):
        from app.models.llm import LLMJudgement

        llm = _FakeAlertLLM(fail=True)
        generate_alert_summary(db, ALERT_CONTEXT, llm_annotator=llm)
        db.flush()
        rows = db.query(LLMJudgement).all()
        assert len(rows) == 1
        assert rows[0].success is False
        assert rows[0].error is not None
