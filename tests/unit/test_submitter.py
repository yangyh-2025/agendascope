"""Submitter 防重②（提交失败内存缓存下轮重发）单元测试：以 stub 替代真实 HTTP。"""
from app.collector.submitter import Submitter
from app.collector.types import CollectedData


def _data(url="https://example.com/n/1"):
    return CollectedData(
        source_id="s1", url=url, title="标题标题标题标题标题", content="正文内容不少于十个字符的正文",
        informant="T", adapter_type="rss",
    )


class _FakePoster(Submitter):
    def __init__(self, fail_first: int):
        super().__init__(api_base="http://stub", token="t")
        self.fail_first = fail_first
        self.posted: list[dict] = []

    def _post(self, payload: dict) -> bool:
        self.posted.append(payload)
        if self.fail_first > 0:
            self.fail_first -= 1
            return False
        return True


def test_failed_submit_buffered_and_resent_next_round():
    sub = _FakePoster(fail_first=1)
    assert sub.submit(_data()) is False          # 第一次失败入缓存
    assert sub.pending_count == 1
    assert sub.resend_pending() == 1             # 下轮重发成功
    assert sub.pending_count == 0
    assert len(sub.posted) == 2                  # 原提交 + 重发


def test_success_submit_not_buffered():
    sub = _FakePoster(fail_first=0)
    assert sub.submit(_data()) is True
    assert sub.pending_count == 0
