"""
标准基础信息 API

提供标准基础信息的查询接口
"""

from typing import Optional, List

from fastapi import APIRouter, Query, Body
from loguru import logger

from app.models.standard.base_info import StandardBaseInfo
from app.models.standard.jgh_pdf import StandardJghPdf, StandardJghPdfChapter
from app.schemas.base import Success, SuccessExtra

router = APIRouter(prefix="/standard-base-info", tags=["标准基础信息"])


@router.get("/list", summary="获取标准基础信息列表")
async def get_standard_base_info_list(
    current: int = Query(1, description="当前页码"),
        size: int = Query(10, description="每页数量"),
    cname: Optional[str] = Query(None, description="标准名称（模糊搜索）"),
    standard_no: Optional[str] = Query(None, description="标准编号（模糊搜索）"),
):
    """
    获取标准基础信息列表（分页）

    支持按标准名称和标准编号进行模糊搜索
    """
    try:
        # 构建查询
        query = StandardBaseInfo.filter(deleted=False)

        # 标准名称模糊搜索
        if cname:
            query = query.filter(cname__icontains=cname)

        # 标准编号模糊搜索
        if standard_no:
            query = query.filter(standard_no__icontains=standard_no)

        # 获取总数
        total = await query.count()

        # 分页查询
        offset = (current - 1) * size
        records = await query.offset(offset).limit(size).order_by('-create_time')

        # 转换为字典
        records_data = []
        for record in records:
            records_data.append({
                'id': record.id,
                'standard_no': record.standard_no,
                'cname': record.cname,
                'ename': record.ename,
                'use_range': record.use_range,
                'intl_cat': record.intl_cat,
                'nat_cat': record.nat_cat,
                'std_domain': record.std_domain,
                'std_field': record.std_field,
                'std_year': record.std_year,
                'std_obj': record.std_obj,
                'issue_date': record.issue_date.isoformat() if record.issue_date else None,
                'act_date': record.act_date.isoformat() if record.act_date else None,
                'annul_date': record.annul_date.isoformat() if record.annul_date else None,
                'approval_unit': record.approval_unit,
                'put_unit': record.put_unit,
                'lead_unit': record.lead_unit,
                'draft_unit_main': record.draft_unit_main,
                'draft_unit': record.draft_unit,
                'draft_staff': record.draft_staff,
                'chief_unit': record.chief_unit,
                'mgr_dept': record.mgr_dept,
                'is_secret': record.is_secret,
                'std_nature': record.std_nature,
                'mandatory_clause': record.mandatory_clause,
                'patent_info': record.patent_info,
                'state': record.state,
                'security_level': record.security_level,
                'release_history': record.release_history,
                'release_std_no': record.release_std_no,
                'replace_description': record.replace_description,
                'replace_stds': record.replace_stds,
                'target_stds': record.target_stds,
                'adopt_situation': record.adopt_situation,
                'adopt_std_no': record.adopt_std_no,
                'adopt_text': record.adopt_text,
                'adopt_level': record.adopt_level,
                'adopt_type': record.adopt_type,
                'adopt_no': record.adopt_no,
                'adopt_name': record.adopt_name,
                'gjb_no': record.gjb_no,
                'std_type': record.std_type,
                'industry': record.industry,
                'remark': record.remark,
                'creator': record.creator,
                'updater': record.updater,
                'create_time': record.create_time.isoformat() if record.create_time else None,
                'update_time': record.update_time.isoformat() if record.update_time else None,
            })

        return SuccessExtra(
            data={"records": records_data},
            total=total,
            current=current,
            size=size
        )

    except Exception as e:
        logger.error(f"获取标准基础信息列表失败: {str(e)}")
        raise


@router.get("/detail/{standard_id}", summary="获取标准基础信息详情")
async def get_standard_base_info_detail(standard_id: str):
    """
    获取单个标准的详细信息
    """
    try:
        record = await StandardBaseInfo.filter(id=standard_id, deleted=False).first()

        if not record:
            return Success(data=None, msg="标准不存在")

        data = {
            'id': record.id,
            'standard_no': record.standard_no,
            'cname': record.cname,
            'ename': record.ename,
            'use_range': record.use_range,
            'intl_cat': record.intl_cat,
            'nat_cat': record.nat_cat,
            'std_domain': record.std_domain,
            'std_field': record.std_field,
            'std_year': record.std_year,
            'std_obj': record.std_obj,
            'issue_date': record.issue_date.isoformat() if record.issue_date else None,
            'act_date': record.act_date.isoformat() if record.act_date else None,
            'annul_date': record.annul_date.isoformat() if record.annul_date else None,
            'approval_unit': record.approval_unit,
            'put_unit': record.put_unit,
            'lead_unit': record.lead_unit,
            'draft_unit_main': record.draft_unit_main,
            'draft_unit': record.draft_unit,
            'draft_staff': record.draft_staff,
            'chief_unit': record.chief_unit,
            'mgr_dept': record.mgr_dept,
            'is_secret': record.is_secret,
            'std_nature': record.std_nature,
            'mandatory_clause': record.mandatory_clause,
            'patent_info': record.patent_info,
            'state': record.state,
            'security_level': record.security_level,
            'release_history': record.release_history,
            'release_std_no': record.release_std_no,
            'replace_description': record.replace_description,
            'replace_stds': record.replace_stds,
            'target_stds': record.target_stds,
            'adopt_situation': record.adopt_situation,
            'adopt_std_no': record.adopt_std_no,
            'adopt_text': record.adopt_text,
            'adopt_level': record.adopt_level,
            'adopt_type': record.adopt_type,
            'adopt_no': record.adopt_no,
            'adopt_name': record.adopt_name,
            'gjb_no': record.gjb_no,
            'std_type': record.std_type,
            'industry': record.industry,
            'remark': record.remark,
            'creator': record.creator,
            'updater': record.updater,
            'create_time': record.create_time.isoformat() if record.create_time else None,
            'update_time': record.update_time.isoformat() if record.update_time else None,
        }

        return Success(data=data)

    except Exception as e:
        logger.error(f"获取标准详情失败: {str(e)}")
        raise


@router.get("/stats", summary="获取标准基础信息统计")
async def get_standard_base_info_stats():
    """
    获取标准基础信息的统计数据
    """
    try:
        # 总数
        total = await StandardBaseInfo.filter(deleted=False).count()

        # 有标准编号的数量
        has_standard_no = await StandardBaseInfo.filter(
            deleted=False,
            standard_no__not_isnull=True
        ).exclude(standard_no='').count()

        # 有适用范围的数量
        has_use_range = await StandardBaseInfo.filter(
            deleted=False,
            use_range__not_isnull=True
        ).exclude(use_range='').count()

        data = {
            'total': total,
            'has_standard_no': has_standard_no,
            'has_use_range': has_use_range,
            'no_standard_no': total - has_standard_no,
            'no_use_range': total - has_use_range,
        }

        return Success(data=data)

    except Exception as e:
        logger.error(f"获取统计数据失败: {str(e)}")
        raise


@router.post("/batch-delete", summary="批量删除标准基础信息及关联数据")
async def batch_delete_standard_base_info(
    ids: List[str] = Body(..., description="要删除的标准ID列表")
):
    """
    批量删除标准基础信息及其关联的数据

    删除逻辑：
    1. 根据 standard_base_info 的 ID 查询对应的 standard_no
    2. 根据 standard_no 删除 standard_jgh_pdf 表中的记录
    3. 根据 standard_jgh_pdf 的 main_task_id 删除 standard_jgh_pdf_chapter 表中的记录
    4. 删除 standard_base_info 表中的记录
    """
    try:
        if not ids:
            return Success(msg="请选择要删除的数据")

        deleted_count = 0

        for standard_id in ids:
            # 1. 查询标准基础信息
            base_info = await StandardBaseInfo.filter(id=standard_id, deleted=False).first()
            if not base_info:
                logger.warning(f"标准基础信息不存在或已删除: {standard_id}")
                continue

            standard_no = base_info.standard_no

            # 2. 查询并删除 standard_jgh_pdf 及其关联的 chapter
            if standard_no:
                pdf_records = await StandardJghPdf.filter(
                    standard_no=standard_no,
                    deleted=False
                ).all()

                for pdf_record in pdf_records:
                    # 3. 删除关联的章节数据
                    if pdf_record.main_task_id:
                        await StandardJghPdfChapter.filter(
                            main_task_id=pdf_record.main_task_id
                        ).delete()
                        logger.info(f"删除章节数据: main_task_id={pdf_record.main_task_id}")

                    # 软删除 PDF 记录
                    pdf_record.deleted = True
                    await pdf_record.save()
                    logger.info(f"删除PDF记录: id={pdf_record.id}, standard_no={standard_no}")

            # 4. 软删除标准基础信息
            base_info.deleted = True
            await base_info.save()
            deleted_count += 1
            logger.info(f"删除标准基础信息: id={standard_id}, standard_no={standard_no}")

        return Success(msg=f"成功删除 {deleted_count} 条数据及其关联数据")

    except Exception as e:
        logger.error(f"批量删除失败: {str(e)}")
        raise


@router.post("/batch-verify", summary="批量校验标准编号是否存在")
async def batch_verify_standard_nos(
    standard_nos: List[str] = Body(..., embed=True, description="标准编号列表"),
):
    """
    批量校验标准编号是否存在，返回每个编号对应的 id 和是否存在

    入参示例：{"standard_nos": ["GB/T 12232-2005", "GB 50016-2014"]}
    出参示例：{"data": {"GB/T 12232-2005": {"id": "xxx", "exists": true}, ...}}
    """
    try:
        if not standard_nos:
            return Success(data={})

        # 一次性查出所有匹配记录（只取 id + standard_no，不加载全字段）
        records = await StandardBaseInfo.filter(
            standard_no__in=standard_nos, deleted=False
        ).only("id", "standard_no").all()

        result: dict[str, dict] = {}
        for r in records:
            # 同一 standard_no 可能有多条，取第一条即可
            if r.standard_no not in result:
                result[r.standard_no] = {"id": str(r.id), "exists": True}

        # 没有查到的编号补 exists=false
        for std_no in standard_nos:
            if std_no not in result:
                result[std_no] = {"id": "", "exists": False}

        return Success(data=result)

    except Exception as e:
        logger.error(f"批量校验标准编号失败: {str(e)}")
        raise


@router.get("/jgh-pdf/{standard_no:path}", summary="根据标准编号获取JGH PDF信息")
async def get_jgh_pdf_by_standard_no(standard_no: str):
    """根据标准编号查询 standard_jgh_pdf 记录，返回 main_task_id 等"""
    try:
        record = await StandardJghPdf.filter(standard_no=standard_no).first()
        if not record:
            return Success(data=None, msg="未找到PDF记录")
        data = {
            "id": str(record.id),
            "main_task_id": str(record.main_task_id),
            "standard_no": record.standard_no,
            "cname": record.cname,
            "name": record.name,
        }
        return Success(data=data)
    except Exception as e:
        logger.error(f"查询JGH PDF失败: {str(e)}")
        raise


@router.get("/jgh-pdf-chapters/{main_task_id}", summary="获取标准章节列表")
async def get_jgh_pdf_chapters(main_task_id: str):
    """根据 main_task_id 查询 standard_jgh_pdf_chapter 全部章节"""
    try:
        chapters = await StandardJghPdfChapter.filter(main_task_id=int(main_task_id)).order_by("id").all()
        data = [
            {
                "id": str(ch.id),
                "main_task_id": str(ch.main_task_id),
                "title": ch.title,
                "title_no": ch.title_no,
                "page": ch.page,
                "word": ch.word,
            }
            for ch in chapters
        ]
        return Success(data=data)
    except Exception as e:
        logger.error(f"查询章节列表失败: {str(e)}")
        raise
