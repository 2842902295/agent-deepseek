"""
查重池管理 API
"""

from typing import List, Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from app.models.standard import StandardBaseInfo, StandardPoolCheck
from app.schemas.base import Fail, Success, SuccessExtra

router = APIRouter(prefix="/deduplication-pool", tags=["查重池管理"])


class CreatePoolRequest(BaseModel):
    """创建查重池请求"""
    pool_name: str = Field(..., description="查重池名称", min_length=1, max_length=200)
    description: Optional[str] = Field(None, description="查重池描述")
    standard_nos: List[str] = Field(..., description="标准编号列表", min_length=1)


class UpdatePoolRequest(BaseModel):
    """更新查重池请求"""
    pool_name: Optional[str] = Field(None, description="查重池名称", min_length=1, max_length=200)
    description: Optional[str] = Field(None, description="查重池描述")
    standard_nos: Optional[List[str]] = Field(None, description="标准编号列表", min_length=1)
    is_active: Optional[bool] = Field(None, description="是否启用")


@router.post("/create", summary="创建查重池")
async def create_pool(request: CreatePoolRequest):
    """
    创建查重池

    Args:
        pool_name: 查重池名称
        description: 查重池描述
        standard_nos: 标准编号列表

    Returns:
        创建的查重池信息
    """
    try:
        # 检查名称是否重复
        existing_pool = await StandardPoolCheck.filter(pool_name=request.pool_name).first()
        if existing_pool:
            return Fail(code="4000", msg=f"查重池名称 '{request.pool_name}' 已存在")

        # 验证标准编号是否存在
        valid_standards = await StandardBaseInfo.filter(standard_no__in=request.standard_nos).all()
        valid_standard_nos = [std.standard_no for std in valid_standards]

        # 创建查重池
        pool = await StandardPoolCheck.create(
            pool_name=request.pool_name,
            description=request.description,
            standard_nos=valid_standard_nos,
            standard_count=len(valid_standard_nos),
            is_active=True
        )

        logger.info(f"创建查重池成功: {pool.pool_name}, ID: {pool.id}, 包含 {pool.standard_count} 个标准")

        return Success(
            data={
                "id": pool.id,
                "pool_name": pool.pool_name,
                "description": pool.description,
                "standard_count": pool.standard_count,
                "create_time": pool.create_time.isoformat() if pool.create_time else None,
                "invalid_standard_nos": list(set(request.standard_nos) - set(valid_standard_nos))
            },
            msg="创建查重池成功"
        )

    except Exception as e:
        logger.error(f"创建查重池失败: {e}")
        return Fail(code="5000", msg=f"创建查重池失败: {str(e)}")


@router.get("/list", summary="获取查重池列表")
async def get_pool_list(
    current: int = 1,
        size: int = 10,
    pool_name: Optional[str] = None,
    is_active: Optional[bool] = None
):
    """
    获取查重池列表（分页）

    Args:
        current: 页码
        size: 每页数量
        pool_name: 查重池名称（模糊搜索）
        is_active: 是否启用

    Returns:
        查重池列表
    """
    try:
        # 构建查询
        query = StandardPoolCheck.all()

        # 应用筛选条件
        if pool_name:
            query = query.filter(pool_name__icontains=pool_name)

        if is_active is not None:
            query = query.filter(is_active=is_active)

        # 查询总数
        total = await query.count()

        # 分页查询
        offset = (current - 1) * size
        pools = await query.offset(offset).limit(size).order_by("-create_time")

        # 组装返回数据
        pool_list = []
        for pool in pools:
            pool_list.append({
                "id": pool.id,
                "pool_name": pool.pool_name,
                "description": pool.description,
                "standard_count": pool.standard_count,
                "is_default": pool.is_default,
                "is_active": pool.is_active,
                "create_time": pool.create_time.isoformat() if pool.create_time else None,
                "update_time": pool.update_time.isoformat() if pool.update_time else None
            })

        return SuccessExtra(
            data={"records": pool_list},
            total=total,
            current=current,
            size=size,
            msg="获取查重池列表成功"
        )

    except Exception as e:
        logger.error(f"获取查重池列表失败: {e}")
        return Fail(code="5000", msg=f"获取查重池列表失败: {str(e)}")


@router.get("/detail/{pool_id}", summary="获取查重池详情")
async def get_pool_detail(pool_id: int):
    """
    获取查重池详情

    Args:
        pool_id: 查重池ID

    Returns:
        查重池详细信息
    """
    try:
        pool = await StandardPoolCheck.filter(id=pool_id).first()

        if not pool:
            return Fail(code="4004", msg="查重池不存在")

        return Success(
            data={
                "id": pool.id,
                "pool_name": pool.pool_name,
                "description": pool.description,
                "standard_nos": pool.standard_nos,
                "standard_count": pool.standard_count,
                "is_default": pool.is_default,
                "is_active": pool.is_active,
                "create_time": pool.create_time.isoformat() if pool.create_time else None,
                "update_time": pool.update_time.isoformat() if pool.update_time else None
            },
            msg="获取查重池详情成功"
        )

    except Exception as e:
        logger.error(f"获取查重池详情失败: {e}")
        return Fail(code="5000", msg=f"获取查重池详情失败: {str(e)}")


@router.put("/update/{pool_id}", summary="更新查重池")
async def update_pool(pool_id: int, request: UpdatePoolRequest):
    """
    更新查重池

    Args:
        pool_id: 查重池ID
        request: 更新请求

    Returns:
        更新后的查重池信息
    """
    try:
        pool = await StandardPoolCheck.filter(id=pool_id).first()

        if not pool:
            return Fail(code="4004", msg="查重池不存在")

        # 不允许修改默认查重池
        if pool.is_default:
            return Fail(code="4000", msg="默认查重池不允许修改")

        # 检查名称是否重复（如果要修改名称）
        if request.pool_name and request.pool_name != pool.pool_name:
            existing_pool = await StandardPoolCheck.filter(pool_name=request.pool_name).first()
            if existing_pool:
                return Fail(code="4000", msg=f"查重池名称 '{request.pool_name}' 已存在")
            pool.pool_name = request.pool_name

        # 更新描述
        if request.description is not None:
            pool.description = request.description

        # 更新标准列表
        if request.standard_nos is not None:
            # 验证标准编号是否存在
            valid_standards = await StandardBaseInfo.filter(standard_no__in=request.standard_nos).all()
            valid_standard_nos = [std.standard_no for std in valid_standards]

            pool.standard_nos = valid_standard_nos
            pool.standard_count = len(valid_standard_nos)

        # 更新启用状态
        if request.is_active is not None:
            pool.is_active = request.is_active

        await pool.save()

        logger.info(f"更新查重池成功: {pool.pool_name}, ID: {pool.id}")

        return Success(
            data={
                "id": pool.id,
                "pool_name": pool.pool_name,
                "description": pool.description,
                "standard_count": pool.standard_count,
                "is_active": pool.is_active,
                "update_time": pool.update_time.isoformat() if pool.update_time else None
            },
            msg="更新查重池成功"
        )

    except Exception as e:
        logger.error(f"更新查重池失败: {e}")
        return Fail(code="5000", msg=f"更新查重池失败: {str(e)}")


@router.delete("/delete/{pool_id}", summary="删除查重池")
async def delete_pool(pool_id: int):
    """
    删除查重池

    Args:
        pool_id: 查重池ID

    Returns:
        删除结果
    """
    try:
        pool = await StandardPoolCheck.filter(id=pool_id).first()

        if not pool:
            return Fail(code="4004", msg="查重池不存在")

        # 不允许删除默认查重池
        if pool.is_default:
            return Fail(code="4000", msg="默认查重池不允许删除")

        pool_name = pool.pool_name
        await pool.delete()

        logger.info(f"删除查重池成功: {pool_name}, ID: {pool_id}")

        return Success(msg="删除查重池成功")

    except Exception as e:
        logger.error(f"删除查重池失败: {e}")
        return Fail(code="5000", msg=f"删除查重池失败: {str(e)}")


@router.get("/all-active", summary="获取所有启用的查重池（用于下拉选择）")
async def get_all_active_pools():
    """
    获取所有启用的查重池（用于下拉选择）

    Returns:
        所有启用的查重池列表（简化版）
    """
    try:
        pools = await StandardPoolCheck.filter(is_active=True).order_by("-is_default", "-create_time")

        pool_list = []
        for pool in pools:
            pool_list.append({
                "id": pool.id,
                "pool_name": pool.pool_name,
                "standard_count": pool.standard_count,
                "is_default": pool.is_default
            })

        return Success(
            data={"pools": pool_list},
            msg="获取成功"
        )

    except Exception as e:
        logger.error(f"获取启用查重池列表失败: {e}")
        return Fail(code="5000", msg=f"获取失败: {str(e)}")


__all__ = ["router"]
