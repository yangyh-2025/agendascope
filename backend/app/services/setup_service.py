"""安装向导状态服务（T5.6）：setup_state KV 持久化 + 监控范围生效 + 初始化进度推导。

监控范围生效口径：未勾选国家的 sources 置 disabled（调度器只调度 active/degraded，
天然停止采集）；重新勾选的国家将 disabled 源恢复为 active；GDELT 兜底伪源（country=ZZ）
不参与禁用。种子源导入完成后初始化编排需再次调用 apply_monitor_scope 使范围生效。
"""
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.source import Source
from app.models.system_state import SetupState
from app.models.topic import Topic

KEY_APP_CONFIG = "app_config"
KEY_MONITOR_SCOPE = "monitor_scope"
KEY_COMPLETED_STEPS = "completed_steps"
KEY_INITIALIZED = "initialized"

# GDELT 兜底通道伪源的国家占位码：跨国聚合通道，不随监控范围禁用
_GDELT_PSEUDO_COUNTRY = "ZZ"

_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


def get_state(db: Session, key: str) -> dict | None:
    row = db.get(SetupState, key)
    return row.value if row is not None else None


def set_state(db: Session, key: str, value: dict) -> None:
    row = db.get(SetupState, key)
    if row is None:
        db.add(SetupState(key=key, value=value))
    else:
        row.value = value
    db.flush()


def is_initialized(db: Session) -> bool:
    return bool((get_state(db, KEY_INITIALIZED) or {}).get("initialized"))


def mark_initialized(db: Session) -> None:
    set_state(db, KEY_INITIALIZED, {"initialized": True})


def completed_steps(db: Session) -> list[int]:
    return list((get_state(db, KEY_COMPLETED_STEPS) or {}).get("completed", []))


def mark_step_completed(db: Session, step: int) -> None:
    steps = completed_steps(db)
    if step not in steps:
        steps.append(step)
        steps.sort()
        set_state(db, KEY_COMPLETED_STEPS, {"completed": steps})


def normalize_countries(countries: list[str]) -> list[str]:
    """国家码归一化：大写、去重、保持输入顺序；非法码抛 ValueError。"""
    normalized: list[str] = []
    for code in countries:
        c = (code or "").strip().upper()
        if not _COUNTRY_RE.match(c):
            raise ValueError(f"非法国家码: {code!r}（需为 2 位字母 ISO 码）")
        if c not in normalized:
            normalized.append(c)
    return normalized


def apply_monitor_scope(db: Session, countries: list[str]) -> dict:
    """监控范围作用于源启用状态：未勾选国家 disabled，重新勾选恢复 active。"""
    selected = set(countries)
    sources = db.scalars(select(Source).where(Source.country_code != _GDELT_PSEUDO_COUNTRY)).all()
    disabled = 0
    enabled = 0
    for source in sources:
        if source.country_code.upper() in selected:
            if source.status == "disabled":
                source.status = "active"
                enabled += 1
        elif source.status != "disabled":
            source.status = "disabled"
            disabled += 1
    db.flush()
    return {"disabled": disabled, "enabled": enabled, "selected_countries": len(selected)}


def save_app_config(db: Session, app_name: str) -> None:
    set_state(db, KEY_APP_CONFIG, {"app_name": app_name})


def save_monitor_scope(db: Session, countries: list[str]) -> dict:
    result = apply_monitor_scope(db, countries)
    set_state(db, KEY_MONITOR_SCOPE, {"countries": countries})
    return result


def init_progress(db: Session) -> dict:
    """初始化三阶段进度（种子源导入→历史数据回补→首次聚类），由真实计数推导。"""
    source_count = db.scalar(select(func.count()).select_from(Source)) or 0
    article_count = db.scalar(select(func.count()).select_from(Article)) or 0
    topic_count = db.scalar(select(func.count()).select_from(Topic)) or 0
    stages = [
        {"key": "seed_sources", "label": "种子源导入", "done": source_count > 0, "count": source_count},
        {"key": "history_backfill", "label": "历史数据回补", "done": article_count > 0, "count": article_count},
        {"key": "first_clustering", "label": "首次聚类", "done": topic_count > 0, "count": topic_count},
    ]
    done_ratio = sum(1 for s in stages if s["done"]) / len(stages)
    return {"stages": stages, "overall_percent": round(done_ratio * 100)}


def wizard_status(db: Session) -> dict:
    """GET /setup/status 响应体：初始化标记 + 当前步骤 + 初始化进度。"""
    initialized = is_initialized(db)
    steps = completed_steps(db)
    current_step = 5 if initialized else (max(steps) + 1 if steps else 1)
    app_config = get_state(db, KEY_APP_CONFIG) or {}
    scope = get_state(db, KEY_MONITOR_SCOPE) or {}
    return {
        "initialized": initialized,
        "current_step": min(current_step, 5),
        "completed_steps": steps,
        "app_name": app_config.get("app_name"),
        "countries": scope.get("countries"),
        "progress": init_progress(db),
    }
