"""parse_structured 与 schema_instruction 单元测试。"""
import pytest
from pydantic import BaseModel, ValidationError

from app.llm.errors import LLMParseError
from app.llm.schemas import NamingOutput, parse_structured, schema_instruction


class _Pair(BaseModel):
    name: str
    score: int


def test_parse_clean_json():
    result = parse_structured('{"name": "俄乌停火谈判", "score": 8}', _Pair)
    assert result.name == "俄乌停火谈判"
    assert result.score == 8


def test_parse_json_with_surrounding_text():
    raw = '好的，输出如下：\n{"name": "新疆棉争议", "score": 7}\n以上。'
    result = parse_structured(raw, _Pair)
    assert result.name == "新疆棉争议"


def test_parse_json_in_markdown_block():
    raw = '```json\n{"name": "美联储降息预期发酵", "score": 6}\n```'
    result = parse_structured(raw, _Pair)
    assert result.name == "美联储降息预期发酵"


def test_parse_no_json_raises():
    with pytest.raises(LLMParseError):
        parse_structured("完全不是 JSON 的输出", _Pair)


def test_parse_broken_json_raises():
    with pytest.raises(LLMParseError):
        parse_structured('{"name": "缺引号, "score": }', _Pair)


def test_parse_schema_violation_raises():
    with pytest.raises(LLMParseError):
        parse_structured('{"name": "x", "score": "不是数字"}', _Pair)


def test_naming_output_validators():
    assert NamingOutput(name="  俄乌停火谈判。 ").name == "俄乌停火谈判"
    with pytest.raises(ValidationError):
        NamingOutput(name="一")


def test_schema_instruction_embeds_json_schema():
    instruction = schema_instruction(NamingOutput)
    assert "JSON Schema" in instruction
    assert '"name"' in instruction
