"""全球监控目标国单一事实源（T4.5 覆盖口径：G20 全部 + 全球南方典型国家，每国主流媒体受众覆盖率 ≥70%）。

所有国家清单（map/topics/setup/GDELT/seed）从本模块派生，禁止散落硬编码。
code 为 ISO 3166-1 alpha-2 两位码（底层存储与 API 透传），name_zh 为前端展示中文名。
"""
from __future__ import annotations

from dataclasses import dataclass

# 地区分组（用于监控范围向导分组展示）
REGION_EAST_ASIA = "东亚"
REGION_SE_ASIA = "东南亚"
REGION_SOUTH_ASIA = "南亚"
REGION_MIDDLE_EAST = "中东"
REGION_EUROPE = "欧洲"
REGION_NAFTA = "北美"
REGION_LATAM = "拉美"
REGION_OCEANIA = "大洋洲"
REGION_AFRICA = "非洲"
REGION_CENTRAL_ASIA = "中亚"


@dataclass(frozen=True)
class Country:
    code: str            # ISO 3166-1 alpha-2
    name_zh: str         # 中文名（前端展示）
    region: str          # 地区分组
    is_g20: bool         # G20 成员（+欧盟部分）
    is_global_south: bool  # 全球南方（发展中国家/新兴经济体）


COUNTRIES: tuple[Country, ...] = (
    # ---- 东亚 ----
    Country("CN", "中国", REGION_EAST_ASIA, True, True),
    Country("JP", "日本", REGION_EAST_ASIA, True, False),
    Country("KR", "韩国", REGION_EAST_ASIA, True, False),
    # ---- 东南亚（全球南方核心）----
    Country("ID", "印度尼西亚", REGION_SE_ASIA, True, True),
    Country("MY", "马来西亚", REGION_SE_ASIA, False, True),
    Country("SG", "新加坡", REGION_SE_ASIA, False, True),
    Country("TH", "泰国", REGION_SE_ASIA, False, True),
    Country("VN", "越南", REGION_SE_ASIA, False, True),
    Country("PH", "菲律宾", REGION_SE_ASIA, False, True),
    Country("MM", "缅甸", REGION_SE_ASIA, False, True),
    Country("KH", "柬埔寨", REGION_SE_ASIA, False, True),
    # ---- 南亚（全球南方核心）----
    Country("IN", "印度", REGION_SOUTH_ASIA, True, True),
    Country("PK", "巴基斯坦", REGION_SOUTH_ASIA, False, True),
    Country("BD", "孟加拉国", REGION_SOUTH_ASIA, False, True),
    Country("LK", "斯里兰卡", REGION_SOUTH_ASIA, False, True),
    Country("NP", "尼泊尔", REGION_SOUTH_ASIA, False, True),
    # ---- 中东 ----
    Country("SA", "沙特阿拉伯", REGION_MIDDLE_EAST, True, True),
    Country("AE", "阿联酋", REGION_MIDDLE_EAST, False, True),
    Country("QA", "卡塔尔", REGION_MIDDLE_EAST, False, True),
    Country("IR", "伊朗", REGION_MIDDLE_EAST, False, True),
    Country("IL", "以色列", REGION_MIDDLE_EAST, False, False),
    Country("TR", "土耳其", REGION_MIDDLE_EAST, True, True),
    Country("KW", "科威特", REGION_MIDDLE_EAST, False, True),
    Country("JO", "约旦", REGION_MIDDLE_EAST, False, True),
    Country("LB", "黎巴嫩", REGION_MIDDLE_EAST, False, True),
    # ---- 欧洲 ----
    Country("GB", "英国", REGION_EUROPE, True, False),
    Country("DE", "德国", REGION_EUROPE, True, False),
    Country("FR", "法国", REGION_EUROPE, True, False),
    Country("IT", "意大利", REGION_EUROPE, True, False),
    Country("ES", "西班牙", REGION_EUROPE, True, False),
    Country("RU", "俄罗斯", REGION_EUROPE, True, True),
    Country("PL", "波兰", REGION_EUROPE, False, True),
    Country("SE", "瑞典", REGION_EUROPE, False, False),
    Country("NO", "挪威", REGION_EUROPE, False, False),
    Country("CH", "瑞士", REGION_EUROPE, False, False),
    Country("NL", "荷兰", REGION_EUROPE, False, False),
    Country("BE", "比利时", REGION_EUROPE, False, False),
    # ---- 北美 ----
    Country("US", "美国", REGION_NAFTA, True, False),
    Country("CA", "加拿大", REGION_NAFTA, True, False),
    # ---- 拉美（全球南方核心）----
    Country("BR", "巴西", REGION_LATAM, True, True),
    Country("MX", "墨西哥", REGION_LATAM, True, True),
    Country("AR", "阿根廷", REGION_LATAM, True, True),
    Country("CL", "智利", REGION_LATAM, False, True),
    Country("CO", "哥伦比亚", REGION_LATAM, False, True),
    Country("PE", "秘鲁", REGION_LATAM, False, True),
    # ---- 大洋洲 ----
    Country("AU", "澳大利亚", REGION_OCEANIA, True, False),
    Country("NZ", "新西兰", REGION_OCEANIA, False, False),
    # ---- 非洲（全球南方核心）----
    Country("ZA", "南非", REGION_AFRICA, True, True),
    Country("EG", "埃及", REGION_AFRICA, False, True),
    Country("NG", "尼日利亚", REGION_AFRICA, False, True),
    Country("KE", "肯尼亚", REGION_AFRICA, False, True),
    Country("ET", "埃塞俄比亚", REGION_AFRICA, False, True),
    Country("MA", "摩洛哥", REGION_AFRICA, False, True),
    Country("GH", "加纳", REGION_AFRICA, False, True),
    Country("TZ", "坦桑尼亚", REGION_AFRICA, False, True),
    Country("UG", "乌干达", REGION_AFRICA, False, True),
    # ---- 中亚 ----
    Country("KZ", "哈萨克斯坦", REGION_CENTRAL_ASIA, False, True),
)

# 快速索引
_COUNTRY_BY_CODE: dict[str, Country] = {c.code: c for c in COUNTRIES}


def country_by_code(code: str) -> Country | None:
    return _COUNTRY_BY_CODE.get(code.upper())


def country_name_zh(code: str) -> str:
    """取国家中文名；未收录时回退为原代码（不透传空名）。"""
    c = _COUNTRY_BY_CODE.get(code.upper())
    return c.name_zh if c else code


def all_codes() -> list[str]:
    return [c.code for c in COUNTRIES]


def all_name_zh_map() -> dict[str, str]:
    return {c.code: c.name_zh for c in COUNTRIES}


def g20_codes() -> set[str]:
    return {c.code for c in COUNTRIES if c.is_g20}


def global_south_codes() -> set[str]:
    return {c.code for c in COUNTRIES if c.is_global_south}
