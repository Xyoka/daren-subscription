"""
acw_sc__v2 阿里云 WAF 挑战求解器（纯 Python 实现）

⚠️ 注意：2026年雪球网已升级至新版阿里云 WAF，使用了 166KB 混淆 JS +
localStorage 机制的挑战。旧版 acw_sc__v2 算法（unsbox + hexXor）不再适用。

当前方案：
1. 默认尝试通过自动求解处理（兼容旧版 WAF）
2. 如果新版 WAF 检测到（特征：renderData + aliyun_waf_aa 无 arg1），
   会抛出明确错误，指引用户配置浏览器 Cookie。

推荐方案：从浏览器获取雪球 Cookie 配置到 .env 文件中的 XUEQIU_COOKIE。
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# 固定的 hexXor 密钥（来自雪球 JS 混淆代码 _0x5e8b26，旧版算法用）
_HEX_XOR_KEY = "3000176000856006061501533003690027800375"

# unsbox 使用的固定排列数组（旧版算法用）
_UNSBOX_ORDER = [
    0xf, 0x23, 0x1d, 0x18, 0x21, 0x10, 0x1, 0x26,
    0xa, 0x9, 0x13, 0x1f, 0x28, 0x1b, 0x16, 0x17,
    0x19, 0xd, 0x6, 0xb, 0x27, 0x12, 0x14, 0x8,
    0xe, 0x15, 0x20, 0x1a, 0x2, 0x1e, 0x7, 0x4,
    0x11, 0x5, 0x3, 0x1c, 0x22, 0x25, 0xc, 0x24,
]

# 新版本 WAF 特征（无 arg1，改用 renderData + 外部 JS）
_NEW_WAF_MARKERS = ("renderData", "aliyun_waf_aa", "aliyun_waf_bb")


def is_new_waf(html: str) -> bool:
    """检测是否为新版阿里云 WAF（renderData 模式，不再使用 arg1）。"""
    return "renderData" in html and "aliyun_waf_aa" in html


def extract_arg1(html: str) -> str | None:
    """从旧版 WAF 挑战页面的 HTML 中提取 arg1 参数。"""
    match = re.search(r"arg1\s*=\s*'([^']+)'", html)
    if match:
        return match.group(1)
    match = re.search(r'arg1\s*=\s*"([^"]+)"', html)
    if match:
        return match.group(1)
    return None


def unsbox(arg1: str) -> str:
    """
    unsbox 重排操作（旧版算法）。
    将 arg1 中的字符按照 _UNSBOX_ORDER 排列映射重新排序。
    """
    result = [''] * len(arg1)
    for i, ch in enumerate(arg1):
        for j, order_value in enumerate(_UNSBOX_ORDER):
            if order_value == i + 1:
                if j < len(result):
                    result[j] = ch
                break
    return ''.join(result)


def hex_xor(s1: str, s2: str) -> str:
    """
    hexXor 异或操作（旧版算法）。
    将两个十六进制字符串成对异或。
    """
    result = []
    for i in range(0, min(len(s1), len(s2)), 2):
        v1 = int(s1[i:i + 2], 16)
        v2 = int(s2[i:i + 2], 16)
        xor_val = v1 ^ v2
        result.append(f"{xor_val:02x}")
    return ''.join(result)


def solve(html: str) -> str | None:
    """
    从 WAF 挑战页面的 HTML 中计算 acw_sc__v2 cookie 值。

    如果检测到新版 WAF（renderData 模式），会抛出 RuntimeError 提示用户配置 Cookie。
    如果检测到旧版 WAF（含 arg1），尝试用 acw_sc__v2 算法求解。

    返回 cookie 值（不含 "acw_sc__v2=" 前缀），如果无法解析则返回 None。
    """
    # 检测新版 WAF
    if is_new_waf(html):
        raise RuntimeError(
            "雪球网已升级至新版阿里云 WAF（renderData 模式），"
            "当前自动求解算法不再兼容。\n"
            "请通过以下方式解决：\n"
            "1. 用浏览器打开 https://xueqiu.com/ 并登录\n"
            "2. 打开浏览器 DevTools → Application → Cookies → xueqiu.com\n"
            "3. 复制所有 Cookie（如 xq_a_token、xqat、xq_id_token 等）\n"
            "4. 粘贴到 backend/.env 文件中的 XUEQIU_COOKIE=\n"
            "   示例：XUEQIU_COOKIE='xq_a_token=xxx; xqat=yyy; xq_id_token=zzz'"
        )

    arg1 = extract_arg1(html)
    if not arg1:
        return None
    unsboxed = unsbox(arg1)
    cookie_value = hex_xor(unsboxed, _HEX_XOR_KEY)
    return cookie_value
