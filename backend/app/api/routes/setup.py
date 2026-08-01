"""安装向导 API（T5.6）：/setup 5 步流程。

- Step 2/3 真实落库（setup_state 表），Step 3 监控范围即时作用于源启用状态
- GET /setup/status：initialized 标记 + 当前步骤 + 初始化三阶段进度（前端进度条轮询）
- 写端点在 initialized=True 后一律拒绝（4005），安装完成后向导自动关闭
"""
import os
import socket

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import (
    CODE_PARAM_INVALID,
    CODE_SETUP_COMPLETED,
    CODE_STATE_INVALID,
    BizError,
    ok,
)
from app.core.security import check_password_policy, hash_password
from app.services import setup_service
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


@router.get("/status")
def setup_status(db: Session = Depends(get_db)):
    """向导状态：initialized 标记 + 当前步骤 + 初始化三阶段进度（进度条轮询）。"""
    return ok(setup_service.wizard_status(db))


def _reject_if_initialized(db: Session) -> None:
    """安装完成后向导写端点关闭（专用错误码 4005）。"""
    if setup_service.is_initialized(db):
        raise BizError(CODE_SETUP_COMPLETED, "系统已完成初始化，安装向导已关闭")


@router.post("")
def setup_step(body: SetupStepInput, db: Session = Depends(get_db)):
    """Step 2-5 向导步骤调度。"""
    _reject_if_initialized(db)

    if body.step == 2:
        app_name = (body.app_name or "").strip() or "AgendaScope 观澜"
        setup_service.save_app_config(db, app_name)
        setup_service.mark_step_completed(db, 2)
        db.commit()
        return ok({"step": 2, "message": "基础配置已保存", "app_name": app_name})

    if body.step == 3:
        if not body.countries:
            raise BizError(CODE_PARAM_INVALID, "监控范围不能为空：请勾选至少一个国家")
        try:
            countries = setup_service.normalize_countries(body.countries)
        except ValueError as exc:
            raise BizError(CODE_PARAM_INVALID, str(exc)) from None
        scope_result = setup_service.save_monitor_scope(db, countries)
        setup_service.mark_step_completed(db, 3)
        db.commit()
        return ok({
            "step": 3,
            "message": f"监控范围已设定：{len(countries)} 国",
            "countries": countries,
            "sources_disabled": scope_result["disabled"],
            "sources_enabled": scope_result["enabled"],
        })

    if body.step == 4:
        if not body.admin_username or not body.admin_password:
            raise BizError(CODE_PARAM_INVALID, "管理员用户名与密码必填")
        # 密码创建/重置路径统一过服务端策略（T1.7）
        if not check_password_policy(body.admin_password):
            raise BizError(CODE_PARAM_INVALID, "密码不符合策略：至少 10 位且包含大小写字母与数字")
        admin = ensure_admin(db)
        # 安装向导录入的密码真实生效：重置 admin 密码并解除强制改密标记
        admin.password_hash = hash_password(body.admin_password)
        admin.must_change_password = False
        username = body.admin_username.strip()
        if username and username != admin.username:
            from sqlalchemy import select

            from app.models.user import User

            conflict = db.scalar(select(User.id).where(User.username == username, User.id != admin.id))
            if conflict is not None:
                raise BizError(CODE_PARAM_INVALID, f"用户名 {username} 已被占用")
            admin.username = username
        setup_service.mark_step_completed(db, 4)
        db.commit()
        return ok({"step": 4, "message": "管理员账号已就绪", "admin_id": str(admin.id)})

    if body.step == 5:
        if 4 not in setup_service.completed_steps(db):
            raise BizError(CODE_STATE_INVALID, "请先完成 Step 4 管理员账号设置")
        setup_service.mark_initialized(db)
        setup_service.mark_step_completed(db, 5)
        db.commit()
        return ok({
            "step": 5,
            "message": "安装完成",
            "initialized": True,
            "next": "访问 / 查看看板；访问 /system 进入管理后台",
        })

    raise BizError(CODE_PARAM_INVALID, f"无效步骤 {body.step}（1-{TOTAL_STEPS}）")


__all__ = ["router"]
