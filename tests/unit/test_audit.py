"""审计 CSV 导出单元测试（T1.10）。"""
from types import SimpleNamespace

from app.api.routes.audit import build_audit_csv


def _entry(**kw):
    defaults = {
        "at": None, "username": "admin", "action": "auth.login", "resource": None,
        "result": "success", "ip": "1.2.3.4", "user_agent": "ua", "detail": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestBuildAuditCsv:
    def test_header_with_bom(self):
        out = build_audit_csv([])
        assert out.startswith("﻿at,username,action,resource,result,ip,user_agent,detail")

    def test_row_serialization(self):
        out = build_audit_csv([_entry(detail={"k": "值"})])
        lines = out.strip().splitlines()
        assert len(lines) == 2
        assert "auth.login" in lines[1]
        assert "1.2.3.4" in lines[1]
        assert "值" in lines[1]  # detail JSON 序列化保留中文

    def test_none_fields_render_empty(self):
        out = build_audit_csv([_entry(username=None, ip=None)])
        lines = out.strip().splitlines()
        assert len(lines) == 2
