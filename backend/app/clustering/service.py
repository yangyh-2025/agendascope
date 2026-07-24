"""聚类产出 service 接口（供 LLM 服务接线：议题命名器/分类器/摘要器，T2.13–T2.15）。

LLM 服务（app.llm）按此接口取待命名议题的簇档案（代表标题 + c-TF-IDF top 词），
命名/分类/摘要结果经 record_llm_naming 回填；人工锁定字段（human_locked_fields）
机器不再自动推翻（详细设计自我纠错口径）。
"""
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clustering.config import get_cluster_settings
from app.clustering.repository import representative_titles, topic_size
from app.core.errors import CODE_NOT_FOUND, BizError
from app.core.logging import get_logger
from app.models.topic import Topic

logger = get_logger("clustering.service")

# 待 LLM 回填的命名方式（聚类侧兜底命名）
PENDING_NAMING_METHODS = ("ctfidf_fallback", "keyword_fallback")


@dataclass(frozen=True)
class ClusterDossier:
    """簇档案：LLM 命名/分类/摘要的输入载荷（对应详细设计 TopicDossier 读侧）。"""

    topic_id: UUID
    name: str
    naming_method: str
    cluster_method: str
    lifecycle_state: str
    size: int
    keywords: list[str] = field(default_factory=list)          # c-TF-IDF top 词（≤20）
    countries: list[str] = field(default_factory=list)
    representative_titles: list[str] = field(default_factory=list)  # 簇内代表标题（权重降序）
    first_seen_at: str = ""
    last_seen_at: str = ""


class ClusterService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_cluster_settings()

    def _to_dossier(self, topic: Topic) -> ClusterDossier:
        return ClusterDossier(
            topic_id=topic.id,
            name=topic.name,
            naming_method=topic.naming_method,
            cluster_method=topic.cluster_method,
            lifecycle_state=topic.lifecycle_state,
            size=topic_size(self.db, topic.id),
            keywords=list(topic.keywords or []),
            countries=list(topic.country_scope or []),
            representative_titles=representative_titles(
                self.db, topic.id, self.settings.representative_titles_n
            ),
            first_seen_at=topic.first_seen_at.isoformat() if topic.first_seen_at else "",
            last_seen_at=topic.last_seen_at.isoformat() if topic.last_seen_at else "",
        )

    def get_cluster_dossier(self, topic_id: UUID) -> ClusterDossier:
        topic = self.db.get(Topic, topic_id)
        if topic is None:
            raise BizError(CODE_NOT_FOUND, "议题不存在")
        return self._to_dossier(topic)

    def list_pending_naming(self, limit: int = 50) -> list[ClusterDossier]:
        """待 LLM 命名/分类的议题（兜底命名留痕的活跃议题，先到先得按最近活跃排序）。"""
        stmt = (
            select(Topic)
            .where(
                Topic.naming_method.in_(PENDING_NAMING_METHODS),
                Topic.merged_into.is_(None),
                Topic.lifecycle_state != "archived",
            )
            .order_by(Topic.last_seen_at.desc())
            .limit(limit)
        )
        return [self._to_dossier(t) for t in self.db.scalars(stmt).all()]

    def record_llm_naming(
        self,
        topic_id: UUID,
        *,
        name: str,
        topic_category: str | None = None,
        summary_zh: str | None = None,
    ) -> Topic:
        """LLM 命名/分类/摘要回填：naming_method 置 llm 留痕；人工锁定字段不覆盖。"""
        topic = self.db.get(Topic, topic_id)
        if topic is None:
            raise BizError(CODE_NOT_FOUND, "议题不存在")
        locked = set(topic.human_locked_fields or [])
        if "name" not in locked:
            topic.name = name[:300]
            topic.name_auto = name[:300]
            topic.naming_method = "llm"
        if topic_category is not None and "topic_category" not in locked:
            topic.topic_category = topic_category
        if summary_zh is not None and "summary_zh" not in locked:
            topic.summary_zh = summary_zh
        self.db.flush()
        logger.info(
            "llm_naming_recorded", topic_id=str(topic.id), naming_method=topic.naming_method,
            category=topic.topic_category, locked=sorted(locked),
        )
        return topic
