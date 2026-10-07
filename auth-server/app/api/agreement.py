"""用户协议 API"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/api", tags=["用户协议"])

AGREEMENT_TEXT = """
zhuzhu Copilot 用户服务协议

更新日期：2026年10月6日
生效日期：2026年10月6日

欢迎使用 zhuzhu Copilot 服务！请您在使用本服务前，仔细阅读并充分理解本协议的全部内容。

第一章 账号条款
1.1 用户注册时需提供真实、准确的用户名和密码，并妥善保管账号信息。
1.2 每个IP地址仅限注册一个账号，多开账号将被封禁。
1.3 用户账号禁止转让、出借、售卖。因账号保管不当造成的损失由用户自行承担。
1.4 用户账号被封禁后，账号内积分不予退还。

第二章 积分条款
2.1 新用户注册即赠送500积分。
2.2 积分可用于使用 zhuzhu Copilot 提供的各类AI模型服务。
2.3 积分消耗规则：每生成1000 tokens消耗30积分（0.03x系数）。
2.4 积分不可兑换现金，不可转让。
2.5 积分有效期：自获得之日起长期有效，账号注销后积分清零。

第三章 会员条款
3.1 Pro会员：7元/月，每月赠送1000积分。
3.2 Max会员：14元/月，每月赠送2000积分。
3.3 会员到期后自动降级为免费用户，未消耗完的积分保留。
3.4 会员续费自动叠加时长，从当前到期日起顺延。

第四章 支付条款
4.1 所有支付均为手动确认模式：用户付款后联系管理员人工核对到账。
4.2 支付完成后积分/会员将在管理员确认后到账。
4.3 虚拟商品一经售出，概不退款。如有特殊问题请联系客服。
4.4 严禁利用支付漏洞获取非法利益，一经发现将封禁账号。

第五章 免责条款
5.1 本服务按"现状"提供，不对服务的不间断性、准确性做担保。
5.2 用户使用本服务生成的内容仅供参考，不构成任何建议。
5.3 因不可抗力（服务器故障、网络中断等）造成的服务中断，本平台不承担责任。
5.4 用户使用本服务时产生的一切法律责任由用户自行承担。

第六章 其他
6.1 本协议最终解释权归 zhuzhu Copilot 所有。
6.2 平台有权根据运营情况修改本协议，修改后将在本页面公示。
6.3 继续使用本服务即视为同意修改后的协议。

如有疑问，请联系管理员。
""".strip()


@router.get("/agreement")
async def get_agreement():
    """获取用户协议"""
    return {"code": 0, "data": {"content": AGREEMENT_TEXT}}


@router.get("/announcement")
async def get_public_announcement(db: AsyncSession = Depends(get_db)):
    """公开公告接口（无需登录）：供客户端轮询展示系统公告。

    与 ``/api/auth/me`` 内嵌的 announcement 同源（system_config 表），
    但**不需要 Authorization**，因此未登录/登录态失效时也能拉到公告。
    enabled=False 或内容为空时返回空字符串（客户端隐藏公告位）。
    """
    content = ""
    try:
        from sqlalchemy import text as _sql
        r = await db.execute(
            _sql("SELECT config_value FROM system_config WHERE config_key = 'announcement'")
        )
        row = r.mappings().first()
        raw = (row["config_value"] if row else "") or ""
        r2 = await db.execute(
            _sql("SELECT config_value FROM system_config WHERE config_key = 'announcement_enabled'")
        )
        row2 = r2.mappings().first()
        enabled = (str(row2["config_value"]) != "0") if row2 else True
        content = raw if (raw and enabled) else ""
    except Exception:
        content = ""
    return {"code": 0, "data": {"content": content, "enabled": bool(content)}}
