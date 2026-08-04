"""加载 50 精品监控对象种子（幂等 upsert）。

使用：
    python -m app.seeds.load_watchlist

按 name + country_code 匹配既有实体；命中则更新 is_seed/category/priority/name_zh 等，
否则插入新行。已有 NER 自动登记的同名实体会被升级为种子。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.person import PersonOrg

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SEED_FILE = Path(__file__).parent / "watchlist_50.yaml"


def load_yaml() -> list[dict]:
    with SEED_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    items: list[dict] = []
    for group, entities in data.items():
        for e in entities:
            e["_group"] = group
            items.append(e)
    return items


def upsert_entity(db, payload: dict) -> tuple[bool, str]:
    """按 (name, country_code) upsert。返回 (created, name)。"""
    name = payload["name"]
    cc = payload["country_code"]
    stmt = select(PersonOrg).where(PersonOrg.name == name, PersonOrg.country_code == cc)
    existing = db.scalar(stmt)
    if existing is None:
        entity = PersonOrg(
            entity_type=payload["entity_type"],
            name=name,
            name_zh=payload.get("name_zh"),
            name_aliases=payload.get("name_aliases") or [],
            country_code=cc,
            role_title=payload.get("role_title"),
            monitored=True,
            is_seed=True,
            category=payload.get("category"),
            priority=payload.get("priority", 0),
        )
        db.add(entity)
        return True, name
    existing.entity_type = payload["entity_type"]
    existing.name_zh = payload.get("name_zh")
    existing.name_aliases = payload.get("name_aliases") or []
    existing.role_title = payload.get("role_title")
    existing.monitored = True
    existing.is_seed = True
    existing.category = payload.get("category")
    existing.priority = payload.get("priority", 0)
    db.add(existing)
    return False, name


def main() -> int:
    items = load_yaml()
    logger.info("seed_file_loaded", extra={"count": len(items)})
    db = get_session_factory()()
    try:
        created = 0
        updated = 0
        for payload in items:
            is_new, name = upsert_entity(db, payload)
            if is_new:
                created += 1
            else:
                updated += 1
        db.commit()
        logger.info("seed_done", extra={"created": created, "updated": updated, "total": len(items)})
        print(f"OK: created={created} updated={updated} total={len(items)}")
        return 0
    except Exception as exc:
        db.rollback()
        logger.exception("seed_failed", extra={"error": str(exc)})
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
