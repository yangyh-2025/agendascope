"""prompt 注册表与版本管理单元测试（T2.13/T2.14/T2.17）。"""
import pytest

from app.llm.prompts import (
    DEFAULT_CATEGORIES,
    TASK_CATEGORY,
    TASK_NAMING,
    TASK_SUMMARY,
    get_prompt,
    parse_categories,
)


def test_each_task_has_versioned_prompt():
    for task in (TASK_NAMING, TASK_CATEGORY, TASK_SUMMARY):
        template = get_prompt(task)
        assert template.version, "每个任务必须有版本号"
        assert template.system


def test_naming_prompt_contains_fewshot_good_bad():
    system = get_prompt(TASK_NAMING).system
    assert "好：" in system and "坏：" in system, "命名 prompt 必须含好/坏命名对照"
    assert "JSON Schema" in system


def test_category_prompt_contains_boundary_examples():
    system = get_prompt(TASK_CATEGORY, categories=DEFAULT_CATEGORIES).system
    assert "分类边界示例" in system, "分类系统提示必须固化边界示例防漂移"
    for category in DEFAULT_CATEGORIES:
        assert category in system
    assert "{categories}" not in system, "分类体系占位符必须被渲染"


def test_category_extension_via_categories_arg():
    extended = DEFAULT_CATEGORIES + ["体育赛事"]
    system = get_prompt(TASK_CATEGORY, categories=extended).system
    assert "体育赛事" in system


def test_user_prompt_renders_titles_and_keywords():
    template = get_prompt(TASK_NAMING)
    user = template.build_user({"titles": ["标题一", "标题二"], "top_words": ["停火"]})
    assert "1. 标题一" in user
    assert "2. 标题二" in user
    assert "停火" in user


def test_unknown_task_or_version_raises():
    with pytest.raises(KeyError):
        get_prompt("not_a_task")
    with pytest.raises(KeyError):
        get_prompt(TASK_NAMING, version="topic-naming-v999")


def test_parse_categories_valid_and_invalid():
    assert parse_categories("") is None
    assert parse_categories('["政治安全", "自定义类"]') == ["政治安全", "自定义类"]
    with pytest.raises(ValueError):
        parse_categories('{"a": 1}')
    with pytest.raises(ValueError):
        parse_categories('["", 1]')
