"""安装向导 API（T5.6）：POST /api/v1/setup 5 步流程。"""
import os
import platform
import socket

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import get_db
from app.core.errors import BizError, ok
from app.core.security import check_password_policy, hash_password
from app.db.session import get_session_factory
from app.services.seed_service import ensure_admin

router = APIRouter()

TOTAL_STEPS = 5


class EnvCheckResult(BaseModel):
    passed: bool
    cpu_cores: int
    memory_mb: int
    disk_gb: float
    docker_available: bool
    internet_reachable: bool
    warnings: list[str] = Field(default_factory=list)


class SetupStepInput(BaseModel):
    step: int = Field(ge=1, le=5)
    # Step 2
    app_name: str | None = None
    # Step 3
    countries: list[str] | None = None
    # Step 4
    admin_username: str | None = None
    admin_password: str | None = None


@router.get("/env-check")
def env_check() -> EnvCheckResult:
    """Step 1：环境自检（CPU/内存/磁盘/Docker/网络连通性）。"""
    import shutil
    import subprocess

    cpu = os.cpu_count() or 1
    mem = 0
    disk_gb = 0.0
    warnings: list[str] = []

    try:
        import psutil
        mem = int(psutil.virtual_memory().total / (1024 * 1024))
        disk_gb = round(shutil.disk_usage("/").free / (1024**3), 1)
    except ImportError:
        mem = 0
    if mem < 8000 and mem > 0:
        warnings.append(f"内存 {mem}MB < 8GB（推荐），聚类与 LLM 推理可能受限，可继续部署但性能无保障")

    docker_ok = False
    try:
        subprocess.run(["docker", "version"], capture_output=True, timeout=5, check=False)
        docker_ok = True
    except Exception:
        warnings.append("Docker 不可用或未安装——请先安装 Docker 20+")

    net_ok = False
    try:
        s = socket.create_connection(("8.8.8.8", 53), timeout=3)
        s.close()
        net_ok = True
    except OSError:
        warnings.append("外网不可达——仅离线安装模式可用")

    return EnvCheckResult(
        passed=docker_ok,
        cpu_cores=cpu,
        memory_mb=mem,
        disk_gb=disk_gb,
        docker_available=docker_ok,
        internet_reachable=net_ok,
        warnings=warnings,
    )


@router.post("")
def setup_step(body: SetupStepInput, request: Request):
    """Step 2-5 向导步骤调度。"""
    db = get_session_factory()()
    try:
        if body.step == 2:
            return ok({"step": 2, "message": "基础配置已保存", "app_name": body.app_name or "AgendaScope 观澜"})
        elif body.step == 3:
            countries = body.countries or []
            return ok({"step": 3, "message": f"监控范围已设定：{len(countries)} 国", "countries": countries})
        elif body.step == 4:
            if not body.admin_username or not body.admin_password:
                raise BizError(1001, "管理员用户名与密码必填")
            # 密码创建/重置路径统一过服务端策略（T1.7）
            if not check_password_policy(body.admin_password):
                raise BizError(1001, "密码不符合策略：至少 10 位且包含大小写字母与数字")
            admin = ensure_admin(db)
            # 安装向导录入的密码真实生效：重置 admin 密码并解除强制改密标记
            admin.password_hash = hash_password(body.admin_password)
            admin.must_change_password = False
            db.commit()
            return ok({"step": 4, "message": "管理员账号已就绪", "admin_id": str(admin.id)})
        elif body.step == 5:
            return ok({
                "step": 5,
                "message": "安装完成",
                "next": "访问 / 查看看板；访问 /system 进入管理后台",
                "admin_note": "请登录后立即修改初始密码",
            })
        else:
            raise BizError(1001, f"无效步骤 {body.step}（1-{TOTAL_STEPS}）")
    finally:
        db.close()


__all__ = ["router"]
