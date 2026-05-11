"""acw_sc__v2 求解器单元测试"""

import pytest
from app.services import acw_sc_v2


def test_extract_arg1():
    """应能从旧版 WAF 挑战 HTML 中提取 arg1。"""
    html = '<script>var arg1=\'274E1264B626E35EEB058EECE01149F3496502CE\';...</script>'
    assert acw_sc_v2.extract_arg1(html) == '274E1264B626E35EEB058EECE01149F3496502CE'

    html = '<script>var arg1="274E1264B626E35EEB058EECE01149F3496502CE";...</script>'
    assert acw_sc_v2.extract_arg1(html) == '274E1264B626E35EEB058EECE01149F3496502CE'

    # 无 arg1 时返回 None
    assert acw_sc_v2.extract_arg1('<html>no arg1 here</html>') is None


def test_unsbox_length():
    """unsbox 应返回与输入等长的字符串。"""
    arg1 = "274E1264B626E35EEB058EECE01149F3496502CE"
    result = acw_sc_v2.unsbox(arg1)
    assert len(result) == len(arg1)
    assert result != arg1  # 重排后肯定不同


def test_hex_xor_basic():
    """hex xor 基本功能。"""
    result = acw_sc_v2.hex_xor("aaaa", "0000")
    assert result == "aaaa"

    result = acw_sc_v2.hex_xor("ff00", "00ff")
    assert result == "ffff"


def test_known_case():
    """
    使用已知的 arg1 与预期输出验证完整求解流程（旧版 WAF）。
    数据来源：https://www.cnblogs.com/wangshx666/p/18204426
    """
    arg1 = "274E1264B626E35EEB058EECE01149F3496502CE"
    expected = "664c59426b8a81e8e837ca070833106ec6c19310"
    result = acw_sc_v2.solve(f"<script>var arg1='{arg1}';</script>")
    assert result == expected, f"Expected {expected}, got {result}"


def test_invalid_html_returns_none():
    """无效 HTML 应返回 None。"""
    assert acw_sc_v2.solve("<html>no challenge</html>") is None
    assert acw_sc_v2.solve("") is None


def test_is_new_waf_detection():
    """应能检测新版 WAF（renderData 模式）。"""
    new_waf_html = (
        '<textarea id="renderData">...'
        '<meta name="aliyun_waf_aa" content="...">'
    )
    assert acw_sc_v2.is_new_waf(new_waf_html) is True

    old_waf_html = '<script>var arg1="xxx";</script>'
    assert acw_sc_v2.is_new_waf(old_waf_html) is False

    normal_html = '<html><body>normal page</body></html>'
    assert acw_sc_v2.is_new_waf(normal_html) is False


def test_new_waf_raises_error():
    """新版 WAF 应抛出明确错误提示用户配置 Cookie。"""
    new_waf_html = (
        '<textarea id="renderData">...'
        '<meta name="aliyun_waf_aa" content="...">'
    )
    with pytest.raises(RuntimeError) as excinfo:
        acw_sc_v2.solve(new_waf_html)
    assert "XUEQIU_COOKIE" in str(excinfo.value)
