"""
图表生成工具

支持 15+ 种 ECharts 图表类型，自动验证数据格式，生成前端可直接渲染的 chart fenced block。
"""

from typing import Any, Dict, List, Literal, Optional, Union

# LLM 有时把 List[str] 参数序列化成 JSON 字符串传入，Union 让 Pydantic 放行，函数体内再解析

from langchain.tools import tool
from pydantic import BaseModel, Field


ChartType = Literal[
    "bar",           # 柱状图
    "bar_h",         # 横向柱状图
    "stacked_bar",   # 堆叠柱状图
    "line",          # 折线图
    "area",          # 面积图
    "pie",           # 饼图
    "donut",         # 圆环图
    "scatter",       # 散点图
    "radar",         # 雷达图
    "funnel",        # 漏斗图
    "gauge",         # 仪表盘
    "heatmap",       # 热力图
    "treemap",       # 矩形树图
    "sankey",        # 桑基图
    "boxplot",       # 箱线图
    "candlestick",   # K线图
    "waterfall",     # 瀑布图
]


def _normalize_number(val: Any) -> Union[int, float]:
    """强制转换为数字"""
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        # 移除单位符号，如 "100个" -> 100
        import re
        cleaned = re.sub(r'[^\d.+-]', '', val)
        try:
            return int(cleaned) if '.' not in cleaned else float(cleaned)
        except ValueError:
            return 0
    return 0


def _build_bar_option(data: List[Dict], title: Optional[str], x_field: str, y_field: Union[str, List[str]], stacked: bool = False, horizontal: bool = False) -> Dict:
    """构建柱状图 option"""
    if not data:
        raise ValueError("data 不能为空")

    # 单系列
    if isinstance(y_field, str):
        categories = [str(row.get(x_field, '')) for row in data]
        values = [_normalize_number(row.get(y_field, 0)) for row in data]

        axis_config = {
            "xAxis": {"type": "value" if horizontal else "category", "data": categories if not horizontal else None},
            "yAxis": {"type": "category" if horizontal else "value", "data": categories if horizontal else None},
        }

        return {
            "title": {"text": title, "left": "center"} if title else None,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 60, "right": 30, "top": 50, "bottom": 40},
            **axis_config,
            "series": [{
                "type": "bar",
                "data": values,
            }]
        }

    # 多系列
    categories = [str(row.get(x_field, '')) for row in data]
    series = []
    for field in y_field:
        values = [_normalize_number(row.get(field, 0)) for row in data]
        series.append({
            "name": field,
            "type": "bar",
            "data": values,
            "stack": "total" if stacked else None,
        })

    axis_config = {
        "xAxis": {"type": "value" if horizontal else "category", "data": categories if not horizontal else None},
        "yAxis": {"type": "category" if horizontal else "value", "data": categories if horizontal else None},
    }

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0},
        "grid": {"left": 60, "right": 30, "top": 50, "bottom": 60},
        **axis_config,
        "series": series,
    }


def _build_line_option(data: List[Dict], title: Optional[str], x_field: str, y_field: Union[str, List[str]], area: bool = False) -> Dict:
    """构建折线图/面积图 option"""
    if not data:
        raise ValueError("data 不能为空")

    categories = [str(row.get(x_field, '')) for row in data]

    # 单系列
    if isinstance(y_field, str):
        values = [_normalize_number(row.get(y_field, 0)) for row in data]
        return {
            "title": {"text": title, "left": "center"} if title else None,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 60, "right": 30, "top": 50, "bottom": 40},
            "xAxis": {"type": "category", "data": categories, "boundaryGap": False if area else True},
            "yAxis": {"type": "value"},
            "series": [{
                "type": "line",
                "data": values,
                "smooth": True,
                "areaStyle": {} if area else None,
            }]
        }

    # 多系列
    series = []
    for field in y_field:
        values = [_normalize_number(row.get(field, 0)) for row in data]
        series.append({
            "name": field,
            "type": "line",
            "data": values,
            "smooth": True,
            "areaStyle": {} if area else None,
        })

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0},
        "grid": {"left": 60, "right": 30, "top": 50, "bottom": 60},
        "xAxis": {"type": "category", "data": categories, "boundaryGap": False if area else True},
        "yAxis": {"type": "value"},
        "series": series,
    }


def _build_pie_option(data: List[Dict], title: Optional[str], donut: bool = False) -> Dict:
    """构建饼图/圆环图 option"""
    if not data:
        raise ValueError("data 不能为空")

    # 推断 name/value 字段
    first = data[0]
    name_field = "name" if "name" in first else list(first.keys())[0]
    value_field = "value" if "value" in first else list(first.keys())[1] if len(first) > 1 else list(first.keys())[0]

    pie_data = [
        {"name": str(row.get(name_field, '')), "value": _normalize_number(row.get(value_field, 0))}
        for row in data
    ]

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"bottom": 0},
        "series": [{
            "type": "pie",
            "radius": ["40%", "70%"] if donut else "65%",
            "data": pie_data,
            "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowOffsetX": 0, "shadowColor": "rgba(0, 0, 0, 0.5)"}},
        }]
    }


def _build_scatter_option(data: List[Dict], title: Optional[str], x_field: str, y_field: str) -> Dict:
    """构建散点图 option"""
    if not data:
        raise ValueError("data 不能为空")

    scatter_data = [
        [_normalize_number(row.get(x_field, 0)), _normalize_number(row.get(y_field, 0))]
        for row in data
    ]

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "item"},
        "grid": {"left": 60, "right": 30, "top": 50, "bottom": 40},
        "xAxis": {"type": "value", "name": x_field},
        "yAxis": {"type": "value", "name": y_field},
        "series": [{
            "type": "scatter",
            "data": scatter_data,
            "symbolSize": 8,
        }]
    }


def _build_radar_option(data: Dict[str, Any], title: Optional[str]) -> Dict:
    """构建雷达图 option

    data 格式：
    {
      "indicators": [{"name": "能力A", "max": 100}, ...],
      "series": [{"name": "系列1", "values": [80, 90, 70, ...]}, ...]
    }
    """
    indicators = data.get("indicators", [])
    series_data = data.get("series", [])

    if not indicators:
        raise ValueError("雷达图必须提供 indicators")

    radar_series = [
        {"name": s.get("name", ""), "value": s.get("values", [])}
        for s in series_data
    ]

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {},
        "legend": {"bottom": 0},
        "radar": {"indicator": indicators},
        "series": [{
            "type": "radar",
            "data": radar_series,
        }]
    }


def _build_funnel_option(data: List[Dict], title: Optional[str]) -> Dict:
    """构建漏斗图 option"""
    if not data:
        raise ValueError("data 不能为空")

    first = data[0]
    name_field = "name" if "name" in first else list(first.keys())[0]
    value_field = "value" if "value" in first else list(first.keys())[1] if len(first) > 1 else list(first.keys())[0]

    funnel_data = [
        {"name": str(row.get(name_field, '')), "value": _normalize_number(row.get(value_field, 0))}
        for row in data
    ]

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
        "series": [{
            "type": "funnel",
            "data": funnel_data,
            "left": "10%",
            "width": "80%",
        }]
    }


def _build_gauge_option(data: Dict[str, Any], title: Optional[str]) -> Dict:
    """构建仪表盘 option

    data 格式：{"value": 75, "name": "完成度", "max": 100}
    """
    value = _normalize_number(data.get("value", 0))
    name = data.get("name", "")
    max_val = _normalize_number(data.get("max", 100))

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "series": [{
            "type": "gauge",
            "data": [{"value": value, "name": name}],
            "max": max_val,
            "detail": {"formatter": "{value}"},
        }]
    }


def _build_heatmap_option(data: List[Dict], title: Optional[str], x_field: str, y_field: str, value_field: str) -> Dict:
    """构建热力图 option"""
    if not data:
        raise ValueError("data 不能为空")

    # 提取所有 x/y 类别
    x_categories = sorted(set(str(row.get(x_field, '')) for row in data))
    y_categories = sorted(set(str(row.get(y_field, '')) for row in data))

    # 构建热力图数据 [x_index, y_index, value]
    heatmap_data = []
    for row in data:
        x_val = str(row.get(x_field, ''))
        y_val = str(row.get(y_field, ''))
        val = _normalize_number(row.get(value_field, 0))
        x_idx = x_categories.index(x_val)
        y_idx = y_categories.index(y_val)
        heatmap_data.append([x_idx, y_idx, val])

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"position": "top"},
        "grid": {"left": 100, "right": 30, "top": 50, "bottom": 40},
        "xAxis": {"type": "category", "data": x_categories, "splitArea": {"show": True}},
        "yAxis": {"type": "category", "data": y_categories, "splitArea": {"show": True}},
        "visualMap": {"min": 0, "max": max([d[2] for d in heatmap_data]) if heatmap_data else 100, "calculable": True, "orient": "horizontal", "left": "center", "bottom": 10},
        "series": [{
            "type": "heatmap",
            "data": heatmap_data,
            "label": {"show": True},
        }]
    }


def _build_treemap_option(data: List[Dict], title: Optional[str]) -> Dict:
    """构建矩形树图 option

    data 格式（树形结构）：
    [{"name": "A", "value": 100, "children": [...]}, ...]
    """
    if not data:
        raise ValueError("data 不能为空")

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"formatter": "{b}: {c}"},
        "series": [{
            "type": "treemap",
            "data": data,
            "nodeGap": 4,
            "levels": [
                {"itemStyle": {"gapWidth": 3, "borderWidth": 2, "borderColor": "#fff"}},
                {"itemStyle": {"gapWidth": 0.5, "borderWidth": 1, "borderColor": "#ffffff60"}},
            ],
        }]
    }


def _build_sankey_option(data: Dict[str, Any], title: Optional[str]) -> Dict:
    """构建桑基图 option

    data 格式：
    {
      "nodes": [{"name": "A"}, {"name": "B"}, ...],
      "links": [{"source": "A", "target": "B", "value": 10}, ...]
    }
    """
    nodes = data.get("nodes", [])
    links = data.get("links", [])

    if not nodes or not links:
        raise ValueError("桑基图必须提供 nodes 和 links")

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "item"},
        "series": [{
            "type": "sankey",
            "data": nodes,
            "links": links,
            "emphasis": {"focus": "adjacency"},
        }]
    }


def _build_boxplot_option(data: List[Dict], title: Optional[str]) -> Dict:
    """构建箱线图 option

    data 格式：
    [{"name": "组1", "values": [1, 2, 3, 4, 5, 6, 7, 8, 9]}, ...]
    """
    if not data:
        raise ValueError("data 不能为空")

    categories = [row.get("name", "") for row in data]
    boxplot_data = []

    for row in data:
        values = sorted([_normalize_number(v) for v in row.get("values", [])])
        if len(values) < 5:
            raise ValueError(f"箱线图每组至少需要 5 个数据点，当前组 '{row.get('name')}' 只有 {len(values)} 个")

        # 计算五数概括：min, Q1, median, Q3, max
        n = len(values)
        q1_idx = n // 4
        median_idx = n // 2
        q3_idx = 3 * n // 4

        boxplot_data.append([
            values[0],           # min
            values[q1_idx],      # Q1
            values[median_idx],  # median
            values[q3_idx],      # Q3
            values[-1],          # max
        ])

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "item"},
        "grid": {"left": 60, "right": 30, "top": 50, "bottom": 40},
        "xAxis": {"type": "category", "data": categories, "boundaryGap": True},
        "yAxis": {"type": "value"},
        "series": [{
            "type": "boxplot",
            "data": boxplot_data,
        }]
    }


def _build_candlestick_option(data: List[Dict], title: Optional[str]) -> Dict:
    """构建 K 线图 option

    data 格式：
    [{"date": "2024-01", "open": 10, "close": 12, "low": 9, "high": 13}, ...]
    """
    if not data:
        raise ValueError("data 不能为空")

    dates = [row.get("date", "") for row in data]
    candlestick_data = [
        [
            _normalize_number(row.get("open", 0)),
            _normalize_number(row.get("close", 0)),
            _normalize_number(row.get("low", 0)),
            _normalize_number(row.get("high", 0)),
        ]
        for row in data
    ]

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 60, "right": 30, "top": 50, "bottom": 40},
        "xAxis": {"type": "category", "data": dates, "boundaryGap": True},
        "yAxis": {"type": "value"},
        "series": [{
            "type": "candlestick",
            "data": candlestick_data,
        }]
    }


def _build_waterfall_option(data: List[Dict], title: Optional[str], x_field: str, y_field: str) -> Dict:
    """构建瀑布图 option（用 bar + stack 实现）"""
    if not data:
        raise ValueError("data 不能为空")

    categories = [str(row.get(x_field, '')) for row in data]
    values = [_normalize_number(row.get(y_field, 0)) for row in data]

    # 计算累积和与辅助系列（用于堆叠实现瀑布效果）
    assist = [0]  # 辅助系列，控制起始位置
    cumulative = 0

    for i, val in enumerate(values):
        if i > 0:
            assist.append(cumulative)
        cumulative += val

    return {
        "title": {"text": title, "left": "center"} if title else None,
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 60, "right": 30, "top": 50, "bottom": 40},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": [
            {
                "name": "辅助",
                "type": "bar",
                "stack": "total",
                "itemStyle": {"color": "rgba(0,0,0,0)"},
                "data": assist,
            },
            {
                "name": y_field,
                "type": "bar",
                "stack": "total",
                "data": values,
            }
        ]
    }


@tool
def create_chart(
    chart_type: ChartType,
    data: Any,
    title: Optional[str] = None,
    x_field: Optional[str] = "name",
    y_field: Optional[str] = "value",
    series_fields: Optional[Union[List[str], str]] = None,
    extra: Optional[Union[Dict[str, Any], str]] = None,
) -> str:
    """
    创建图表，返回可直接嵌入对话的 chart fenced block。

    Args:
        chart_type: bar / line / pie / scatter / radar / funnel / gauge / heatmap / treemap / sankey / boxplot / candlestick / waterfall / bar_h / stacked_bar / area / donut
        data: 图表数据——
            - bar/line/scatter/heatmap/waterfall: List[Dict]，每 Dict 一行数据
            - pie/donut/funnel: List[Dict]，必须有 name 和 value 字段
            - radar: {"indicators": [...], "series": [{"name", "values"}]}
            - gauge: {"value": 75, "name": "完成度", "max": 100}
            - treemap: List[Dict] 树形结构；sankey: {"nodes": [...], "links": [...]}
            - boxplot: [{"name": "组1", "values": [...]}]
            - candlestick: [{"date", "open", "close", "low", "high"}]
        title: 图表标题（可选）
        x_field / y_field: 轴/值字段名（bar/line/scatter/heatmap/waterfall 用）
        series_fields: 多系列字段名列表（多系列 bar/line，传了则忽略 y_field）
        extra: 额外的 ECharts option 覆盖
    """
    import json

    try:
        # 处理 data 参数：如果是字符串，先解析成对象
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                return f"data 参数解析失败：{str(e)}\n请确保传入有效的 JSON 字符串或 Python 对象。"

        # 处理 series_fields 参数：如果是字符串，先解析成列表
        if isinstance(series_fields, str):
            try:
                series_fields = json.loads(series_fields)
            except json.JSONDecodeError:
                # 如果解析失败，当作单个字段名处理（兼容性）
                series_fields = [series_fields]

        # 处理 extra 参数：如果是字符串，先解析成字典
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = None

        # 根据类型构建 ECharts option
        if chart_type == "bar":
            y = series_fields if series_fields else y_field
            option = _build_bar_option(data, title, x_field, y, stacked=False, horizontal=False)
        elif chart_type == "bar_h":
            y = series_fields if series_fields else y_field
            option = _build_bar_option(data, title, x_field, y, stacked=False, horizontal=True)
        elif chart_type == "stacked_bar":
            y = series_fields if series_fields else y_field
            option = _build_bar_option(data, title, x_field, y, stacked=True, horizontal=False)
        elif chart_type == "line":
            y = series_fields if series_fields else y_field
            option = _build_line_option(data, title, x_field, y, area=False)
        elif chart_type == "area":
            y = series_fields if series_fields else y_field
            option = _build_line_option(data, title, x_field, y, area=True)
        elif chart_type == "pie":
            option = _build_pie_option(data, title, donut=False)
        elif chart_type == "donut":
            option = _build_pie_option(data, title, donut=True)
        elif chart_type == "scatter":
            option = _build_scatter_option(data, title, x_field, y_field)
        elif chart_type == "radar":
            option = _build_radar_option(data, title)
        elif chart_type == "funnel":
            option = _build_funnel_option(data, title)
        elif chart_type == "gauge":
            option = _build_gauge_option(data, title)
        elif chart_type == "heatmap":
            value_field = "value"  # 热力图固定用 value 字段
            option = _build_heatmap_option(data, title, x_field, y_field, value_field)
        elif chart_type == "treemap":
            option = _build_treemap_option(data, title)
        elif chart_type == "sankey":
            option = _build_sankey_option(data, title)
        elif chart_type == "boxplot":
            option = _build_boxplot_option(data, title)
        elif chart_type == "candlestick":
            option = _build_candlestick_option(data, title)
        elif chart_type == "waterfall":
            option = _build_waterfall_option(data, title, x_field, y_field)
        else:
            return f"错误：不支持的图表类型 '{chart_type}'"

        # 合并 extra 配置
        if extra:
            option.update(extra)

        # 移除 None 值（减少 JSON 体积）
        def remove_none(obj):
            if isinstance(obj, dict):
                return {k: remove_none(v) for k, v in obj.items() if v is not None}
            elif isinstance(obj, list):
                return [remove_none(item) for item in obj]
            else:
                return obj

        option = remove_none(option)

        # 生成 chart fenced block
        chart_json = json.dumps(option, ensure_ascii=False, separators=(',', ':'))
        return f"```chart\n{chart_json}\n```"

    except Exception as e:
        return f"图表生成失败：{str(e)}\n\n请检查数据格式是否正确。"


__all__ = ["create_chart"]
