"""seed vip service agreement article

Revision ID: a7b8c9d0e129
Revises: a7b8c9d0e128
Create Date: 2026-10-18 21:00:00.000000
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e129"
down_revision: Union[str, None] = "a7b8c9d0e128"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(sa.text("SELECT id FROM content_articles WHERE slug = :slug LIMIT 1"), {"slug": "vip-service-agreement"}).first()
    if exists:
        return
    now = datetime.now()
    bind.execute(sa.text("""
        INSERT INTO content_articles
        (title, slug, summary, content, category, cover, pinned, vip_only, status, sort_order, published_at, start_at, end_at, created_at, updated_at)
        VALUES
        (:title, :slug, :summary, :content, :category, '', 0, 0, 1, 20, :published_at, NULL, NULL, :created_at, :updated_at)
    """), {
        "title": "VIP会员服务协议",
        "slug": "vip-service-agreement",
        "summary": "说明 VIP 会员开通、续费、权益使用和退款规则。",
        "content": "学徒行 VIP 会员服务协议\n\n一、会员开通\n用户完成 VIP 订单支付后，会员身份和有效期按照所选方案自动生效。\n\n二、会员权益\n会员可享受平台公布的 VIP 专属内容和功能。具体权益以会员页面及平台实际展示为准。\n\n三、续费与有效期\n会员可以在有效期内续费，续费成功后会员有效期按平台规则顺延。会员到期后，VIP 专属内容和功能可能会被限制访问。\n\n四、退款说明\n已支付订单的退款按照平台订单和售后规则处理。退款完成后，对应 VIP 权益和购买奖励积分可能同步撤销。\n\n五、服务调整\n平台会根据业务运营情况调整会员权益，并通过公告或会员页面进行说明。",
        "category": "会员服务",
        "published_at": now,
        "created_at": now,
        "updated_at": now,
    })


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM content_articles WHERE slug = 'vip-service-agreement'"))
