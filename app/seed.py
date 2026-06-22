import re
from datetime import datetime

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import (
    DecorationConfig,
    InviteRelation,
    PlatformAnnouncement,
    PointRule,
    PreferenceEvent,
    SupportConversation,
    SupportMessage,
    TravelOrder,
    TravelRoute,
)


DEFAULT_DECORATION = {
    "brand": {
        "name": "学徒行",
        "slogan": "愿你提笔上岸，收笔远行",
        "logoText": "行",
        "primary": "#ff7a35",
        "secondary": "#12a594",
        "background": "#f6f7f4",
        "dark": "#172c2a",
    },
    "pages": [
        {
            "id": "home",
            "name": "小程序首页",
            "path": "/pages/index/index",
            "status": "published",
            "blocks": [
                {
                    "id": "b1",
                    "type": "banner",
                    "name": "首页轮播",
                    "visible": True,
                    "title": "备考上岸，全包定制长线旅行",
                    "subtitle": "用一次远行，奖励认真生活的自己",
                    "image": "https://images.unsplash.com/photo-1500534314209-a25ddb2bd429?w=1200",
                    "badge": "上岸限定",
                    "background": "#0f6f66",
                },
                {
                    "id": "b2",
                    "type": "activity",
                    "name": "积分活动横幅",
                    "visible": True,
                    "title": "邀好友赚积分，免费泰山经典游",
                    "subtitle": "当前 68 积分 / 100 积分",
                    "button": "立即邀请",
                    "progress": 68,
                    "background": "#fff5e9",
                },
                {
                    "id": "b3",
                    "type": "grid",
                    "name": "核心功能宫格",
                    "visible": True,
                    "title": "学 · 游一站式服务",
                    "items": ["备考刷题", "资料商城", "定制旅行", "邀请有礼", "我的积分", "上岸权益"],
                    "columns": 3,
                },
            ],
        }
    ],
    "routes": [],
    "points": {
        "inviteScore": 1,
        "exchangeScore": 100,
        "validDays": 365,
        "yearlyLimit": 1,
        "monthlyStock": 50,
        "enabled": True,
    },
}


def normalize_points_text(value):
    if isinstance(value, dict):
        return {key: normalize_points_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_points_text(item) for item in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"¥\s*([\d,]+)", r"\1 积分", value)
    value = re.sub(r"([\d,]+)\s*元", r"\1 积分", value)
    value = value.replace("方案总报价", "方案所需积分")
    value = value.replace("起售价", "所需积分")
    value = value.replace("人均预算", "人均积分预算")
    value = value.replace("交通补贴", "积分补贴").replace("补差出行", "补积分差额出行")
    return value


async def seed_data() -> None:
    async with SessionLocal() as db:
        if not await db.get(PointRule, 1):
            db.add(PointRule(id=1))
        if not await db.scalar(select(DecorationConfig.id).limit(1)):
            db.add(DecorationConfig(status="published", version=1, content=DEFAULT_DECORATION))
        if not await db.scalar(select(TravelRoute.id).limit(1)):
            db.add_all(
                [
                    TravelRoute(
                        name="川西雪山轻徒步",
                        category="户外",
                        days="5天4夜",
                        price=3680,
                        stock=42,
                        agency="山海旅行",
                        image="https://images.unsplash.com/photo-1464278533981-50106e6176b1?w=700",
                    ),
                    TravelRoute(
                        name="泉州非遗漫游",
                        category="研学",
                        days="3天2夜",
                        price=1580,
                        stock=28,
                        agency="知行文旅",
                        image="https://images.unsplash.com/photo-1528127269322-539801943592?w=700",
                    ),
                    TravelRoute(
                        name="青岛海风毕业季",
                        category="团建",
                        days="3天2夜",
                        price=1880,
                        stock=0,
                        agency="青年假日",
                        status=False,
                        image="https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=700",
                    ),
                ]
            )
        if not await db.scalar(select(TravelOrder.id).limit(1)):
            db.add_all(
                [
                    TravelOrder(
                        order_no="TS20260621008",
                        order_type="积分兑换",
                        title="泰山经典游 2天1夜",
                        user_name="林晓雪",
                        phone="176****8032",
                        travel_date="2026-07-06",
                        agency="齐鲁文旅",
                        amount_text="100 积分",
                    ),
                    TravelOrder(
                        order_no="DZ20260621016",
                        order_type="人工定制",
                        title="川西雪山深度定制",
                        user_name="陈泽宇",
                        phone="139****2177",
                        travel_date="2026-08-12",
                        amount_text="7680 积分",
                    ),
                ]
            )
        if not await db.scalar(select(InviteRelation.id).limit(1)):
            db.add_all(
                [
                    InviteRelation(inviter_id="U20260482", invitee_phone="176****8032", device_id="iOS-A83F"),
                    InviteRelation(
                        inviter_id="U20260117",
                        invitee_phone="139****2177",
                        device_id="Android-2B19",
                        abnormal=True,
                    ),
                ]
            )
        if not await db.scalar(select(PreferenceEvent.id).limit(1)):
            db.add_all(
                [
                    PreferenceEvent(
                        user_id="U20260621",
                        user_name="小徒同学",
                        preference_type="route",
                        target_key="1",
                        target_name="川西雪山轻徒步",
                        action="target_add",
                        score=5,
                    ),
                    PreferenceEvent(
                        user_id="U20260621",
                        user_name="小徒同学",
                        preference_type="route",
                        target_key="1",
                        target_name="川西雪山轻徒步",
                        action="favorite_add",
                        score=3,
                    ),
                    PreferenceEvent(
                        user_id="U20260621",
                        user_name="小徒同学",
                        preference_type="study",
                        target_key="exam_questions",
                        target_name="备考刷题",
                        action="entry_click",
                        score=2,
                    ),
                    PreferenceEvent(
                        user_id="U20260482",
                        user_name="林晓雪",
                        preference_type="route",
                        target_key="2",
                        target_name="泉州非遗漫游",
                        action="favorite_add",
                        score=3,
                    ),
                    PreferenceEvent(
                        user_id="U20260482",
                        user_name="林晓雪",
                        preference_type="study",
                        target_key="materials",
                        target_name="资料商城",
                        action="entry_click",
                        score=2,
                    ),
                    PreferenceEvent(
                        user_id="U20260117",
                        user_name="陈泽宇",
                        preference_type="route",
                        target_key="3",
                        target_name="青岛海风毕业季",
                        action="target_add",
                        score=5,
                    ),
                ]
            )
        if not await db.scalar(select(PlatformAnnouncement.id).limit(1)):
            now = datetime.now()
            db.add_all(
                [
                    PlatformAnnouncement(
                        title="暑期积分活动升级通知",
                        summary="邀请新用户、路线收藏和上岸目标行为已纳入积分激励，活动页同步更新。",
                        content="1. 邀请活动保持每邀请 1 位新用户获得 1 积分。\n2. 首页、旅行页和路线详情页的说明已统一为积分体系。\n3. 如遇展示延迟，可下拉刷新首页与旅行页。",
                        tag="活动更新",
                        pinned=True,
                        status=True,
                        published_at=now,
                    ),
                    PlatformAnnouncement(
                        title="泰山经典游预约须知",
                        summary="兑换成功后请至少提前 7 天提交预约，客服将在 24 小时内完成审核。",
                        content="泰山经典游属于积分权益产品，仅限本人使用，不支持转让与折现。提交预约后可在“我的旅行”中查看审核进度。",
                        tag="出行提醒",
                        status=True,
                        published_at=now,
                    ),
                ]
            )
        if not await db.scalar(select(SupportConversation.id).limit(1)):
            demo_conversation = "demo-support-20260621"
            db.add(
                SupportConversation(
                    id=demo_conversation,
                    user_id="U20260482",
                    user_name="林晓雪",
                    last_message="泰山游兑换后怎么预约日期？",
                    unread_admin=1,
                )
            )
            db.add_all(
                [
                    SupportMessage(
                        conversation_id=demo_conversation,
                        sender_role="user",
                        sender_name="林晓雪",
                        content="你好呀，我想咨询积分兑换的泰山游。",
                    ),
                    SupportMessage(
                        conversation_id=demo_conversation,
                        sender_role="admin",
                        sender_name="学徒行客服",
                        content="你好呀，可以的，请问想了解哪一部分？",
                    ),
                    SupportMessage(
                        conversation_id=demo_conversation,
                        sender_role="user",
                        sender_name="林晓雪",
                        content="泰山游兑换后怎么预约日期？",
                    ),
                ]
            )
        for decoration in list(await db.scalars(select(DecorationConfig))):
            decoration.content = normalize_points_text(decoration.content)
        for order in list(await db.scalars(select(TravelOrder))):
            order.amount_text = normalize_points_text(order.amount_text)
        await db.commit()
