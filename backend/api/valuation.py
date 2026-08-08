from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from backend.fund_valuation import fund_valuation_service
from backend.market_data import market_data_service
from backend.api.schemas import (
    ValuationResponse,
    ValuationBatchResponse,
    ValuationBatchRequest,
    ValuationDetailResponse,
)
from backend.models import ValuationType
from loguru import logger
import asyncio
import json
from datetime import datetime


router = APIRouter(prefix="/valuation", tags=["基金估值"])


@router.get(
    "/info/types",
    summary="获取估值类型说明",
    description="获取系统支持的估值类型及其说明",
)
async def get_valuation_types():
    """获取估值类型说明"""
    return {
        "success": True,
        "message": "获取成功",
        "data": {
            "real_time_price": {
                "name": "实时价格",
                "description": "场内ETF/LOF的实时交易价格，置信度100%",
                "applicable": "场内ETF、LOF基金",
            },
            "index_based": {
                "name": "指数估值",
                "description": "基于跟踪指数涨跌幅估算净值变化，置信度85%",
                "applicable": "指数基金、指数增强基金",
            },
            "holdings_based": {
                "name": "持仓估值",
                "description": "基于基金持仓股票的涨跌幅加权计算，置信度60-80%",
                "applicable": "主动股票型基金、混合型基金",
            },
            "benchmark_only": {
                "name": "基准估值",
                "description": "仅基于业绩比较基准估算，置信度较低",
                "applicable": "无法获取跟踪指数或持仓的基金",
            },
            "hybrid_bond": {
                "name": "混合估值（债券 + 股票）",
                "description": "债券指数涨跌幅×债券仓位 + 股票持仓涨跌幅×股票仓位",
                "applicable": "偏债混合基金、二级债基",
            },
            "hybrid_qdii": {
                "name": "混合估值（持仓 + 指数）",
                "description": "已知持仓部分使用实际涨跌幅，剩余部分使用市场指数涨跌幅参考",
                "applicable": "主动管理型 QDII 基金",
            },
            "not_supported": {
                "name": "不支持估值",
                "description": "该基金类型暂不支持估值",
                "applicable": "货币基金、债券基金等",
            },
        },
    }


@router.get(
    "/{fund_code}",
    response_model=ValuationResponse,
    summary="获取基金估值",
    description="获取单个基金的实时估值，支持ETF、指数基金、主动基金等",
)
async def get_fund_valuation(
    fund_code: str,
    prefer_holdings: bool = Query(True, description="是否优先使用持仓估值"),
):
    """
    获取基金估值

    - **fund_code**: 基金代码（如 110022, 510300）
    - **prefer_holdings**: 是否优先使用持仓估值（默认True）
    """
    try:
        result = await fund_valuation_service.get_valuation_with_method(
            fund_code, prefer_holdings=prefer_holdings
        )

        if not result:
            # 检查是否是因为基金未被收录
            fund_data = await market_data_service.get_fund_data(fund_code)
            if not fund_data:
                return ValuationResponse(
                    success=False,
                    message=f"基金 {fund_code} 在天天基金网没有收录，可能是香港互认基金、私募基金或其他特殊基金",
                    data=None,
                )
            return ValuationResponse(
                success=False, message=f"无法计算基金 {fund_code} 的估值", data=None
            )

        return ValuationResponse(success=True, message="估值计算成功", data=result)
    except Exception as e:
        logger.error(f"获取基金估值失败: {fund_code}, {e}")
        return ValuationResponse(
            success=False, message=f"获取基金估值失败: {str(e)}", data=None
        )


@router.post(
    "/batch",
    response_model=ValuationBatchResponse,
    summary="批量获取基金估值",
    description="批量获取多个基金的实时估值",
)
async def get_fund_valuation_batch(request: ValuationBatchRequest):
    """
    批量获取基金估值

    - **fund_codes**: 基金代码列表
    - **prefer_holdings**: 是否优先使用持仓估值
    """
    results = []
    success_count = 0
    failed_count = 0
    tasks = []

    async with asyncio.TaskGroup() as tg:
        for code in request.fund_codes:
            task = tg.create_task(
                fund_valuation_service.calculate_fund_valuation(
                    code, prefer_holdings=request.prefer_holdings
                )
            )
            tasks.append(task)

    for task in tasks:
        result = task.result()
        if result:
            results.append(result)
            success_count += 1
        else:
            failed_count += 1

    return ValuationBatchResponse(
        success=True,
        message=f"成功计算 {success_count}/{len(request.fund_codes)} 个基金估值",
        data=results,
        total=len(results),
        success_count=success_count,
        failed_count=failed_count,
    )


@router.post(
    "/batch/stream",
    summary="批量获取基金估值（流式返回）",
    description="使用 SSE 流式返回，并发执行估值，每个基金估值完成后立即返回结果",
)
async def get_fund_valuation_batch_stream(request: ValuationBatchRequest):
    """
    批量获取基金估值（流式返回，并发执行）

    使用 Server-Sent Events (SSE) 实时返回每个基金的估值结果。
    使用 asyncio.TaskGroup 并发执行估值计算，通过 Queue 实时传递结果。

    - **fund_codes**: 基金代码列表
    - **prefer_holdings**: 是否优先使用持仓估值

    SSE 事件格式：
    - event: valuation - 单个基金估值结果
    - event: progress - 进度更新
    - event: complete - 全部完成
    - event: error - 错误信息
    """
    from asyncio import Queue

    async def calculate_single_with_queue(code: str, queue: Queue):
        """计算单个基金估值并将结果放入队列"""
        try:
            result = await fund_valuation_service.get_valuation_with_method(
                code, prefer_holdings=request.prefer_holdings
            )
            await queue.put(("result", code, result, None))
        except Exception as e:
            logger.warning(f"获取基金 {code} 估值失败: {e}")
            await queue.put(("error", code, None, str(e)))

    async def generate_valuation_stream():
        total = len(request.fund_codes)
        queue = Queue()

        # 发送开始事件
        yield f"event: progress\ndata: {json.dumps({'type': 'start', 'total': total}, ensure_ascii=False)}\n\n"

        # 使用 TaskGroup 并发执行所有估值任务
        async with asyncio.TaskGroup() as tg:
            for code in request.fund_codes:
                tg.create_task(calculate_single_with_queue(code, queue))

            # 实时读取队列结果
            completed = 0
            success_count = 0
            failed_count = 0

            while completed < total:
                msg_type, code, result, error = await queue.get()

                if msg_type == "result":
                    completed += 1
                    if result:
                        success_count += 1
                        # 将 ValuationResult 转换为可序列化的字典
                        result_dict = {
                            "fund_code": result.fund_code,
                            "fund_name": result.fund_name,
                            "valuation_type": result.valuation_type.value,
                            "valuation_method": result.valuation_method,
                            "estimated_nav": result.estimated_nav,
                            "estimated_change_percent": result.estimated_change_percent,
                            "previous_nav": result.previous_nav,
                            "latest_nav": result.latest_nav,
                            "nav_date": result.nav_date,
                            "total_value": result.total_value,
                            "holdings_value": result.holdings_value,
                            "benchmark_info": result.benchmark_info,
                            "confidence": result.confidence,
                            "confidence_note": result.confidence_note,
                            "index_code": result.index_code,
                            "index_name": result.index_name,
                            "pe_value": result.pe_value,
                            "pe_percentile": result.pe_percentile,
                            "pb_value": result.pb_value,
                            "pb_percentile": result.pb_percentile,
                            "valuation_signal": result.valuation_signal,
                            "signal_action": result.signal_action,
                            "signal_source": result.signal_source,
                            "timestamp": result.timestamp.isoformat()
                            if result.timestamp
                            else None,
                        }
                        yield f"event: valuation\ndata: {json.dumps({'success': True, 'data': result_dict}, ensure_ascii=False)}\n\n"
                    else:
                        failed_count += 1
                        yield f"event: valuation\ndata: {json.dumps({'success': False, 'fund_code': code, 'message': '无法获取估值'}, ensure_ascii=False)}\n\n"

                elif msg_type == "error":
                    completed += 1
                    failed_count += 1
                    yield f"event: error\ndata: {json.dumps({'fund_code': code, 'message': error}, ensure_ascii=False)}\n\n"

                # 发送进度更新
                progress = {
                    "type": "progress",
                    "current": completed,
                    "total": total,
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "percent": round(completed / total * 100, 1),
                }
                yield f"event: progress\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"

        # 发送完成事件
        complete_data = {
            "type": "complete",
            "total": total,
            "success_count": success_count,
            "failed_count": failed_count,
            "timestamp": datetime.now().isoformat(),
        }
        yield f"event: complete\ndata: {json.dumps(complete_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_valuation_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


@router.get(
    "/{fund_code}/detail",
    response_model=ValuationDetailResponse,
    summary="获取基金估值详情",
    description="获取基金估值的详细信息，包括持仓贡献分析",
)
async def get_fund_valuation_detail(fund_code: str):
    """
    获取基金估值详情

    - **fund_code**: 基金代码
    """
    try:
        result = await fund_valuation_service.calculate_fund_valuation(fund_code)

        if not result:
            return ValuationDetailResponse(
                success=False,
                message=f"无法计算基金 {fund_code} 的估值",
                fund_code=fund_code,
                fund_name="",
                valuation_type=ValuationType.NOT_SUPPORTED,
            )

        holdings_contribution = []
        if result.holdings_value:
            for stock_code, info in result.holdings_value.items():
                holdings_contribution.append(
                    {
                        "stock_code": stock_code,
                        "stock_name": info.get("name", ""),
                        "weight": info.get("weight", 0),
                        "change_percent": info.get("change_percent", 0),
                        "contribution": info.get("contribution", 0),
                    }
                )

        return ValuationDetailResponse(
            success=True,
            message="获取估值详情成功",
            fund_code=result.fund_code,
            fund_name=result.fund_name,
            valuation_type=result.valuation_type,
            estimated_nav=result.estimated_nav,
            estimated_change_percent=result.estimated_change_percent,
            previous_nav=result.previous_nav,
            confidence=result.confidence,
            confidence_note=result.confidence_note,
            benchmark_info=result.benchmark_info,
            holdings_contribution=holdings_contribution,
            timestamp=result.timestamp,
        )
    except Exception as e:
        logger.error(f"获取基金估值详情失败: {fund_code}, {e}")
        return ValuationDetailResponse(
            success=False,
            message=f"获取基金估值详情失败: {str(e)}",
            fund_code=fund_code,
            fund_name="",
            valuation_type=ValuationType.NOT_SUPPORTED,
        )


@router.get(
    "/{fund_code}/accuracy",
    summary="验证估值准确性",
    description="对比估算净值与实际净值，计算估值误差",
)
async def verify_valuation_accuracy(fund_code: str):
    """
    验证估值准确性

    - **fund_code**: 基金代码
    """
    try:
        result = await fund_valuation_service.calculate_fund_valuation(fund_code)
        # 使用 get_fund_data 获取基金净值数据
        fund_data = await market_data_service.get_fund_data(fund_code)

        if not result:
            return {
                "success": False,
                "message": f"无法计算基金 {fund_code} 的估值",
                "data": None,
            }

        if not fund_data:
            return {
                "success": True,
                "message": "估值计算成功，但无法获取实际净值进行对比",
                "data": {
                    "fund_code": result.fund_code,
                    "fund_name": result.fund_name,
                    "estimated_nav": result.estimated_nav,
                    "estimated_change_percent": result.estimated_change_percent,
                    "actual_nav": None,
                    "actual_change_percent": None,
                    "nav_error": None,
                    "change_error": None,
                },
            }

        # 使用 fund_data 中的净值信息
        actual_nav = fund_data.nav
        actual_previous_nav = fund_data.previous_nav
        actual_change_percent = None

        if actual_nav and actual_previous_nav and actual_previous_nav > 0:
            actual_change_percent = (
                (actual_nav - actual_previous_nav) / actual_previous_nav * 100
            )

        nav_error = None
        change_error = None

        if result.estimated_nav and actual_nav:
            nav_error = result.estimated_nav - actual_nav

        if (
            result.estimated_change_percent is not None
            and actual_change_percent is not None
        ):
            change_error = result.estimated_change_percent - actual_change_percent

        return {
            "success": True,
            "message": "验证完成",
            "data": {
                "fund_code": result.fund_code,
                "fund_name": result.fund_name,
                "valuation_type": result.valuation_type.value,
                "estimated_nav": result.estimated_nav,
                "estimated_change_percent": result.estimated_change_percent,
                "actual_nav": actual_nav,
                "actual_change_percent": round(actual_change_percent, 2)
                if actual_change_percent
                else None,
                "nav_error": round(nav_error, 4) if nav_error else None,
                "change_error": round(change_error, 2) if change_error else None,
                "confidence": result.confidence,
                "timestamp": result.timestamp.isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"验证估值准确性失败: {fund_code}, {e}")
        return {
            "success": False,
            "message": f"验证估值准确性失败: {str(e)}",
            "data": None,
        }
