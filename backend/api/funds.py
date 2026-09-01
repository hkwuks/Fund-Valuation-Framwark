import asyncio
from fastapi import APIRouter, Body, Query
from typing import List, Dict, Any
import json
import os
from backend.market_data import market_data_service
from backend.fund_quant.core.trading import infer_trading_profile
from loguru import logger

# 基金数据文件路径
FUNDS_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'backend', 'funds.json')


router = APIRouter(prefix="/funds", tags=["基金管理"])


def ensure_data_dir():
    """确保数据目录存在"""
    os.makedirs(os.path.dirname(FUNDS_FILE_PATH), exist_ok=True)


async def load_funds_from_file() -> List[Dict[str, Any]]:
    """从文件加载基金数据"""
    ensure_data_dir()
    return await asyncio.to_thread(_load_funds_sync)


def _load_funds_sync() -> List[Dict[str, Any]]:
    if not os.path.exists(FUNDS_FILE_PATH):
        return []
    try:
        with open(FUNDS_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载基金文件失败: {e}")
        return []


async def save_funds_to_file(funds: List[Dict[str, Any]]):
    """保存基金数据到文件"""
    ensure_data_dir()
    await asyncio.to_thread(_save_funds_sync, funds)


def _save_funds_sync(funds: List[Dict[str, Any]]):
    try:
        with open(FUNDS_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(funds, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存基金文件失败: {e}")


@router.get(
    "",
    summary="获取基金列表",
    description="获取保存的基金列表"
)
async def get_funds():
    """
    获取基金列表
    """
    try:
        funds = await load_funds_from_file()
        return {
            "success": True,
            "message": "获取成功",
            "data": {
                "funds": funds
            }
        }
    except Exception as e:
        logger.error(f"获取基金列表失败: {e}")
        return {
            "success": False,
            "message": f"获取基金列表失败: {str(e)}",
            "data": {
                "funds": []
            }
        }


@router.post(
    "/add",
    summary="添加基金",
    description="添加新基金到基金列表"
)
async def add_fund(fund: Dict[str, Any] = Body(...)):
    """
    添加基金

    - **fund**: 基金数据，包含 fund_code、fund_name、fund_type、total_shares 等字段
    """
    try:
        logger.info(f"收到添加基金请求: {fund}")

        # 验证必填字段
        required_fields = ['fund_code', 'fund_name', 'fund_type', 'total_shares']
        for field in required_fields:
            if field not in fund:
                return {
                    "success": False,
                    "message": f"缺少必填字段: {field}"
                }

        # 加载现有基金
        funds = await load_funds_from_file()

        # 检查基金是否已存在
        for existing_fund in funds:
            if existing_fund.get('fund_code') == fund.get('fund_code'):
                return {
                    "success": False,
                    "message": f"基金已存在: {fund.get('fund_code')}"
                }

        # 交易属性优先使用可靠资料自动识别；无法确认时保留待人工确认状态。
        fund.setdefault("market_type", fund.get("market_type", "unknown"))
        if not fund.get("trade_mode") or fund.get("trading_profile_needs_confirmation", True):
            quote = None
            if "ETF" in f"{fund.get('fund_name', '')}{fund.get('fund_type', '')}".upper():
                try:
                    quote = await market_data_service.get_etf_realtime_data(fund["fund_code"])
                except Exception:
                    quote = None
            fund.update(infer_trading_profile(
                fund["fund_code"], fund.get("fund_name", ""), fund.get("fund_type", ""), quote,
                fund.get("purchase_channel"),
            ))

        # 添加新基金
        funds.append(fund)

        # 保存到文件
        await save_funds_to_file(funds)

        return {
            "success": True,
            "message": "基金添加成功"
        }
    except Exception as e:
        logger.error(f"添加基金失败: {e}")
        return {
            "success": False,
            "message": f"添加基金失败: {str(e)}"
        }


@router.delete(
    "/{fund_code}",
    summary="删除基金",
    description="从基金列表中删除指定基金"
)
async def delete_fund(fund_code: str):
    """
    删除基金

    - **fund_code**: 基金代码
    """
    try:
        # 加载现有基金
        funds = await load_funds_from_file()

        # 检查基金是否存在
        fund_exists = any(f.get('fund_code') == fund_code for f in funds)
        if not fund_exists:
            return {
                "success": False,
                "message": f"基金不存在: {fund_code}"
            }

        # 删除基金
        funds = [f for f in funds if f.get('fund_code') != fund_code]

        # 保存到文件
        await save_funds_to_file(funds)

        return {
            "success": True,
            "message": "基金删除成功"
        }
    except Exception as e:
        logger.error(f"删除基金失败: {e}")
        return {
            "success": False,
            "message": f"删除基金失败: {str(e)}"
        }


@router.put(
    "/{fund_code}",
    summary="更新基金",
    description="更新现有基金的信息"
)
async def update_fund(fund_code: str, fund: Dict[str, Any] = Body(...)):
    """
    更新基金

    - **fund_code**: 基金代码
    - **fund**: 基金数据
    """
    try:
        # 加载现有基金
        funds = await load_funds_from_file()

        # 查找基金
        fund_index = None
        for i, f in enumerate(funds):
            if f.get('fund_code') == fund_code:
                fund_index = i
                break

        if fund_index is None:
            return {
                "success": False,
                "message": f"基金不存在: {fund_code}"
            }

        # 更新基金
        funds[fund_index].update(fund)

        # 保存到文件
        await save_funds_to_file(funds)

        return {
            "success": True,
            "message": "基金更新成功"
        }
    except Exception as e:
        logger.error(f"更新基金失败: {e}")
        return {
            "success": False,
            "message": f"更新基金失败: {str(e)}"
        }


@router.post(
    "/batch",
    summary="批量获取基金信息",
    description="根据基金代码列表批量获取基金详细信息"
)
async def get_fund_data_batch(fund_codes: List[str] = Body(...)):
    """
    批量获取基金信息

    - **fund_codes**: 基金代码列表
    """
    try:
        fund_datas = []

        for fund_code in fund_codes:
            try:
                fund_data = await market_data_service.get_fund_data(fund_code)
                if fund_data:
                    fund_datas.append(fund_data.model_dump())
            except Exception as e:
                logger.error(f"获取基金 {fund_code} 信息失败: {e}")

        return {
            "success": True,
            "message": "批量获取成功",
            "data": fund_datas
        }
    except Exception as e:
        logger.error(f"批量获取基金信息失败: {e}")
        return {
            "success": False,
            "message": f"批量获取基金信息失败: {str(e)}",
            "data": []
        }


@router.get(
    "/query/{fund_code}",
    summary="查询基金信息",
    description="根据基金代码查询基金详细信息（从外部数据源获取）"
)
async def query_fund_data(fund_code: str):
    """
    查询基金信息

    - **fund_code**: 基金代码
    """
    try:
        # 使用market_data_service从外部数据源获取基金信息
        fund_data = await market_data_service.get_fund_data(fund_code)

        if fund_data:
            return {
                "success": True,
                "message": "查询成功",
                "data": fund_data.model_dump()
            }
        else:
            return {
                "success": False,
                "message": f"未找到基金信息: {fund_code}"
            }
    except Exception as e:
        logger.error(f"查询基金信息失败: {e}")
        return {
            "success": False,
            "message": f"查询基金信息失败: {str(e)}"
        }


@router.get(
    "/profile/{fund_code}",
    summary="自动识别基金交易属性",
    description="使用基金资料与ETF行情接口识别场内/场外及交易时序，无法确认时标记待人工确认",
)
async def get_trading_profile(fund_code: str, purchase_channel: str | None = Query(None)):
    if purchase_channel not in {None, "tiantian", "ant", "broker", "bank", "other"}:
        return {"success": False, "message": "不支持的购买渠道"}
    fund_data = await market_data_service.get_fund_data(fund_code)
    if not fund_data:
        return {"success": False, "message": f"未找到基金信息: {fund_code}"}
    quote = None
    if fund_data.market_type.value == "on_exchange":
        try:
            quote = await market_data_service.get_etf_realtime_data(fund_code)
        except Exception:
            quote = None
    profile = infer_trading_profile(
        fund_code, fund_data.fund_name, fund_data.fund_type, quote, purchase_channel,
    )
    return {"success": True, "data": {
        **fund_data.model_dump(), **profile,
    }}


@router.get(
    "/{fund_code}",
    summary="获取单个基金信息",
    description="根据基金代码获取基金详细信息"
)
async def get_fund(fund_code: str):
    """
    获取单个基金信息

    - **fund_code**: 基金代码
    """
    try:
        # 加载现有基金
        funds = await load_funds_from_file()

        # 查找基金
        for fund in funds:
            if fund.get('fund_code') == fund_code:
                return {
                    "success": True,
                    "message": "获取成功",
                    "data": fund
                }

        fund_data = await market_data_service.get_fund_data(fund_code)

        if fund_data:
            return {
                "success": True,
                "message": "获取成功",
                "data": fund_data.model_dump()
            }

        # 基金不存在
        return {
            "success": False,
            "message": f"基金不存在: {fund_code}"
        }
    except Exception as e:
        logger.error(f"获取基金信息失败: {e}")
        return {
            "success": False,
            "message": f"获取基金信息失败: {str(e)}"
        }


@router.post(
    "/clear",
    summary="清空基金列表",
    description="清空所有基金数据"
)
async def clear_funds():
    """
    清空基金列表
    """
    try:
        # 保存空列表
        await save_funds_to_file([])

        return {
            "success": True,
            "message": "基金列表清空成功"
        }
    except Exception as e:
        logger.error(f"清空基金列表失败: {e}")
        return {
            "success": False,
            "message": f"清空基金列表失败: {str(e)}"
        }


@router.get(
    "/{fund_code}/nav-history",
    summary="获取基金净值历史",
    description="获取指定基金的净值历史数据（用于净值走势图）"
)
async def get_fund_nav_history(fund_code: str, limit: int = 250):
    """
    获取基金净值历史

    - **fund_code**: 基金代码
    - **limit**: 返回最近多少条记录（默认 250）
    """
    try:
        from ..fund_quant.data.storage import get_nav_history
        nav_data = await asyncio.to_thread(get_nav_history, fund_code)
        if not nav_data:
            # 尝试采集
            try:
                from ..fund_quant.data.collector import fund_data_collector
                from ..fund_quant.data.storage import save_nav_points
                points = await fund_data_collector.fetch_nav_history(
                    fund_code=fund_code,
                    start_date=(datetime.now() - timedelta(days=365 * 5)).strftime("%Y%m%d"),
                )
                if points:
                    await asyncio.to_thread(save_nav_points, points)
                    nav_data = await asyncio.to_thread(get_nav_history, fund_code)
            except Exception as e:
                logger.warning(f"自动采集净值失败 {fund_code}: {e}")

        if not nav_data:
            return {"success": False, "message": f"基金 {fund_code} 无净值数据", "data": []}

        recent = nav_data[-limit:] if len(nav_data) > limit else nav_data
        return {"success": True, "data": recent}
    except Exception as e:
        logger.error(f"获取净值历史失败: {fund_code}, {e}")
        return {"success": False, "message": f"获取净值历史失败: {str(e)}", "data": []}
