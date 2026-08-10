"""
单价表 ORM。

价格按 (provider, model, unit_type) + 生效区间 [effective_from, effective_to) 版本化。
- 改价 = 把旧行的 effective_to 设为当前时间 + INSERT 一行新价
- 历史 agent_usage_log 永远按写入时的快照（pricing_snapshot_json）对账，不受改价影响
- unit_type 自由扩展，常见值：
    token_in / token_out / token_cached
    video_sec_720 / video_sec_1080
    mcp_call / mcp_search_result
"""

from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class AgentPricing(BaseModel, TimestampMixin):
    """单位价格（带生效区间）。"""

    id = fields.BigIntField(pk=True, description="主键ID")
    provider = fields.CharField(max_length=32, description="dashscope / openai / anthropic / ...")
    model = fields.CharField(max_length=96, description="model id（如 claude-opus-4-8 / happyhorse-1.0-t2v）")
    unit_type = fields.CharField(
        max_length=32,
        description="token_in / token_out / token_cached / video_sec_720 / mcp_call / ...",
    )
    price_yuan = fields.DecimalField(max_digits=14, decimal_places=10, description="每单位价（元）")
    effective_from = fields.DatetimeField(description="开始生效时间（含）")
    effective_to = fields.DatetimeField(null=True, description="失效时间（不含）；NULL=当前生效")
    note = fields.CharField(max_length=255, null=True, description="备注，例如调价原因")

    class Meta:
        table = "agent_pricing"
        table_description = "单价表（版本化）"
        indexes = [
            ("provider", "model", "unit_type", "effective_to"),
        ]
