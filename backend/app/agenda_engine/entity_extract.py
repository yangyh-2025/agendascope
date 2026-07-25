"""轻量 NER 抽取（T3.5 过渡方案，persons_orgs 实体库在 T3.7 才接入）。

策略：
- 中文走 jieba.posseg 词性标注：ns=地名 → LOCATION，nr=人名 → PEOPLE，
  nt=机构团体 → ORG，nz=其他专名 → OTHER
- 英文规则：连续 ≥2 个大写开头的 token 视为实体候选（合并为一个多词实体），
  单词大写候选在句首时丢弃（避免句首普通词误判）；专有名词类别无法可靠区分时
  标 OTHER，地名/机构后缀（如 Beijing/Ministry/University）启发式归类
- 黑名单统计只看实体文本本身做频次，类别仅用于上报/调试，不参与打分
"""
from __future__ import annotations

import re

import jieba.posseg as pseg

# 词性 → 实体类别
# jieba 词性全集：nr=人名，nrfg=人名词素，nrt=人名转用（如"拜登"）
#               ns=地名，nsf=音译地名；nt=机构团体；nz=其他专名
_KIND_BY_FLAG = {
    "ns": "LOCATION",
    "nsf": "LOCATION",
    "nr": "PEOPLE",
    "nrfg": "PEOPLE",
    "nrt": "PEOPLE",
    "nt": "ORG",
    "nz": "OTHER",
}

# 英文大写候选启发式分类
_EN_LOCATION_HINTS = frozenset({
    "Beijing", "Shanghai", "Tokyo", "London", "Paris", "Moscow", "Washington",
    "Berlin", "Seoul", "Pyongyang", "Kyiv", "Ukraine", "Russia", "China",
    "Japan", "America", "Europe", "Asia", "Africa", "Taiwan", "Hong", "Kong",
})
_EN_ORG_SUFFIXES = frozenset({
    "Ministry", "Department", "University", "Institute", "Agency", "Committee",
    "Commission", "Corporation", "Corp", "Inc", "Ltd", "Company", "Bank",
    "Council", "Assembly", "Congress", "Senate", "Parliament", "Party",
})

# 拉丁词（含所有格 's 与连字符）
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")
# CJK 串判定
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")

# 黑名单噪音：单字符 / 纯数字 / 极常见非实体大写词（句首虚词/月份/星期）
_EN_NON_ENTITY = frozenset({
    "The", "A", "An", "And", "Or", "But", "If", "Then", "Than", "So",
    "In", "On", "At", "By", "For", "With", "From", "As", "To", "Of",
    "Is", "Are", "Was", "Were", "Be", "Been", "It", "Its", "This", "That",
    "These", "Those", "He", "She", "They", "We", "You", "I",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
})


def _is_latin_capitalized(token: str) -> bool:
    return bool(token) and token[0].isupper() and token.isascii()


def _classify_latin_entity(tokens: list[str]) -> str:
    """对连续大写拉丁 token 序列做启发式归类。"""
    if any(t in _EN_ORG_SUFFIXES for t in tokens):
        return "ORG"
    if any(t in _EN_LOCATION_HINTS for t in tokens):
        return "LOCATION"
    # 多词大写序列在新闻英文中多为人名（Firstname Lastname），单词无法区分按 OTHER
    return "PEOPLE" if len(tokens) >= 2 else "OTHER"


def _extract_latin_entities(text: str) -> list[tuple[str, str, int]]:
    """扫描拉丁 token 流，返回 (entity_text, kind, start_char_index) 列表。"""
    entities: list[tuple[str, str, int]] = []
    buffer: list[str] = []
    buffer_start: int = -1

    def flush() -> None:
        nonlocal buffer, buffer_start
        if not buffer:
            return
        # 单词大写候选且落在句首：大概率是普通词被句首大写化，丢弃
        if len(buffer) == 1 and buffer[0] in _EN_NON_ENTITY:
            buffer, buffer_start = [], -1
            return
        text_repr = " ".join(buffer)
        kind = _classify_latin_entity(buffer)
        entities.append((text_repr, kind, buffer_start))
        buffer, buffer_start = [], -1

    for match in _LATIN_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _is_latin_capitalized(token):
            if not buffer:
                buffer_start = match.start()
            buffer.append(token)
        else:
            flush()
    flush()
    return entities


def _extract_cjk_entities(text: str) -> list[tuple[str, str]]:
    """jieba.posseg 抽取中文实体：ns/nr/nt/nz → LOCATION/PEOPLE/ORG/OTHER。"""
    entities: list[tuple[str, str]] = []
    for word, flag in pseg.cut(text):
        word = word.strip()
        if len(word) < 2:
            continue  # 单字符专名噪音大（nr 经常把"他/她"切碎）
        if flag in _KIND_BY_FLAG:
            entities.append((word, _KIND_BY_FLAG[flag]))
    return entities


def extract_entities(text: str) -> list[tuple[str, str]]:
    """从混合中英文文本抽取实体候选，返回 (entity_text, entity_kind) 列表。

    entity_kind ∈ {"LOCATION", "PEOPLE", "ORG", "OTHER"}。同一实体可能重复返回
    （黑名单统计侧按文章集合去重），调用方如需去重自行 set()。
    """
    if not text:
        return []
    entities: list[tuple[str, str]] = []

    # 中文实体：jieba.posseg 直接在混合文本上工作（英文会被切成 ENG 单字母，不进 ns/nr/nt/nz）
    if _CJK_RE.search(text):
        entities.extend(_extract_cjk_entities(text))

    # 英文实体：独立规则通道，覆盖纯英文文本与中英混合
    for entity_text, kind, _start in _extract_latin_entities(text):
        entities.append((entity_text, kind))

    return entities


def is_valid_entity(entity: str) -> bool:
    """黑名单候选过滤：剔除纯数字、单字符、纯标点（复用 clustering.tokenize 的口径）。"""
    if not entity:
        return False
    if len(entity) < 2:
        return False
    if entity.isdigit():
        return False
    # 全标点 / 全空白：只要存在任意字母或数字即视为有效候选
    return any(ch.isalnum() for ch in entity)
