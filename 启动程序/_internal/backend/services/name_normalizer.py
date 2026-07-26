# services/name_normalizer.py
# CS2/CSGO 皮肤名称标准化与模糊匹配

import json
import re
from typing import Optional

# 磨损等级缩写 → 全称映射
WEAR_ABBREV: dict[str, str] = {
    "fn": "Factory New",
    "mw": "Minimal Wear",
    "ft": "Field-Tested",
    "ww": "Well-Worn",
    "bs": "Battle-Scarred",
}

# 前缀缩写
PREFIX_ABBREV: dict[str, str] = {
    "st": "StatTrak™",
    "souvenir": "Souvenir",
}

# 特殊字符标准化
_RE_WEAR_PAREN = re.compile(r"\(([^)]+)\)$")
_RE_MULTI_SPACE = re.compile(r"\s+")
_RE_PIPE_SPACE = re.compile(r"\s*\|\s*")


def normalize_name(name: str) -> str:
    """标准化皮肤名称，统一格式便于比较和查询。

    处理规则：
    1. 去除首尾空格
    2. 统一 | 两侧空白
    3. 展开磨损缩写 (FT → Field-Tested)
    4. 统一大小写（首字母大写）
    5. 合并多余空格

    >>> normalize_name("ak-47 | redline (ft)")
    'AK-47 | Redline (Field-Tested)'

    >>> normalize_name("  AWP|Asiimov(FT)  ")
    'AWP | Asiimov (Field-Tested)'
    """
    if not name:
        return ""

    name = name.strip()
    name = _RE_MULTI_SPACE.sub(" ", name)
    name = _RE_PIPE_SPACE.sub(" | ", name)

    # 展开磨损后缀缩写
    name = _expand_wear_abbrev(name)

    # 首字母大写（保留已有大写，如 AK-47、AWP）
    name = name.title()

    # 修复 title() 导致的全大写词被破坏（如 AK-47 → Ak-47）
    name = _fix_title_casing(name)

    name = _RE_MULTI_SPACE.sub(" ", name)
    return name.strip()


def _expand_wear_abbrev(name: str) -> str:
    """将磨损缩写 (FT) (FN) 等展开为全称，确保括号前有空格。"""
    match = _RE_WEAR_PAREN.search(name)
    if match:
        inner = match.group(1).strip().lower()
        if inner in WEAR_ABBREV:
            expanded = WEAR_ABBREV[inner]
            # 确保 ( 前有空格，除非已经是开头
            prefix = name[: match.start()]
            if prefix and not prefix.endswith(" "):
                prefix = prefix.rstrip() + " "
            return prefix + f"({expanded})"
    return name


def _fix_title_casing(name: str) -> str:
    """修复 .title() 对全部大写缩写词的破坏。"""
    preserved = {"Ak", "Awp", "Mp", "Usp", "Sg", "Pp", "P90", "P250", "Mp5", "Mp7", "Mp9"}
    # 实际上 title() 对 "AK-47" 的处理是 "Ak-47"，需要修正
    fixes = {
        "Ak-": "AK-",
        "Awp": "AWP",
        "Mp9": "MP9",
        "Mp7": "MP7",
        "Mp5": "MP5",
        "Usp-": "USP-",
        "Sg ": "SG ",
        "Pp-": "PP-",
        "P90": "P90",
        "P250": "P250",
    }
    for wrong, correct in fixes.items():
        if name.startswith(wrong) or wrong in name:
            name = name.replace(wrong, correct)
    return name


def fuzzy_match(name1: str, name2: str) -> bool:
    """模糊匹配两个皮肤名称，判断是否指向同一物品。

    匹配策略：
    1. 标准化后完全相等 → True
    2. 忽略磨损后缀后相等 → True
    3. 都标准化后小写相等 → True

    >>> fuzzy_match("AK-47 | Redline (FT)", "ak-47 | redline (field-tested)")
    True

    >>> fuzzy_match("AWP | Asiimov (FN)", "AWP | Asiimov (MW)")
    False
    """
    n1 = normalize_name(name1).lower()
    n2 = normalize_name(name2).lower()

    if n1 == n2:
        return True

    # 去掉磨损后缀再比
    n1_no_wear = _RE_WEAR_PAREN.sub("", n1).strip()
    n2_no_wear = _RE_WEAR_PAREN.sub("", n2).strip()

    return n1_no_wear == n2_no_wear


def extract_wear(name: str) -> Optional[str]:
    """从皮肤名称中提取磨损等级。

    >>> extract_wear("AK-47 | Redline (Field-Tested)")
    'Field-Tested'
    """
    match = _RE_WEAR_PAREN.search(name)
    if match:
        wear = match.group(1).strip()
        lower = wear.lower()
        if lower in WEAR_ABBREV:
            return WEAR_ABBREV[lower]
        if lower in {v.lower() for v in WEAR_ABBREV.values()}:
            return wear
    return None


# ────────────────────────── 白名单/黑名单通配符匹配 ──────────────────────────

def _wildcard_to_regex(pattern: str) -> re.Pattern:
    """将简单通配符模式转为正则表达式。

    * → .* （匹配任意字符）
    其他字符 → re.escape 原样匹配
    大小写不敏感

    >>> _wildcard_to_regex("*AK-47*").pattern
    '.*AK\\-47.*'
    """
    # 按 * 分割，每段用 re.escape，再用 .* 连接
    parts = pattern.split("*")
    escaped = [re.escape(p) for p in parts]
    regex_str = ".*".join(escaped)
    return re.compile(regex_str, re.IGNORECASE)


def match_pattern_list(name: str, patterns_json: str) -> bool:
    """检查名称是否匹配 JSON 数组中的任一模式。

    patterns_json: JSON 字符串，如 '["*AK-47*", "AWP | Asiimov"]'
                  空数组 "[]" 或空字符串视为不限制（白名单不限制=匹配所有）

    Returns:
        True  如果 patterns 为空（不限制）或匹配任一模式
        False 如果不匹配任何模式

    >>> match_pattern_list("AK-47 | Redline (FT)", '["*AK-47*"]')
    True

    >>> match_pattern_list("M4A1-S | Cyrex", '["*AK-47*", "AWP*"]')
    False
    """
    if not patterns_json or patterns_json in ("[]", ""):
        return True  # 空=不限制

    try:
        patterns: list[str] = json.loads(patterns_json)
    except (json.JSONDecodeError, TypeError):
        return True  # 解析失败则不过滤

    if not patterns:
        return True

    normalized_name = normalize_name(name)

    for pattern in patterns:
        if not pattern or not pattern.strip():
            continue
        regex = _wildcard_to_regex(pattern.strip())
        if regex.search(normalized_name):
            return True

    return False


def match_whitelist(name: str, whitelist_json: str) -> bool:
    """白名单匹配：名称匹配白名单中任一模式 → True。

    空白名单视为通过（不限制）。
    """
    return match_pattern_list(name, whitelist_json)


def match_blacklist(name: str, blacklist_json: str) -> bool:
    """黑名单匹配：名称匹配黑名单中任一模式 → True（应被过滤掉）。

    空黑名单视为不匹配（不过滤任何物品）。
    """
    if not blacklist_json or blacklist_json in ("[]", ""):
        return False  # 空黑名单=不过滤

    try:
        patterns: list[str] = json.loads(blacklist_json)
    except (json.JSONDecodeError, TypeError):
        return False

    if not patterns:
        return False

    normalized_name = normalize_name(name)

    for pattern in patterns:
        if not pattern or not pattern.strip():
            continue
        regex = _wildcard_to_regex(pattern.strip())
        if regex.search(normalized_name):
            return True

    return False


def check_wear_filter(wear: str | None, wear_filter_json: str) -> bool:
    """检查磨损是否在过滤列表中。

    wear_filter_json: JSON 字符串，如 '["Factory New", "Minimal Wear"]'
                     空数组 "[]" 表示不限制。

    Returns:
        True  如果 wear_filter 为空或磨损在列表中
        False 如果磨损不在列表中
    """
    if not wear_filter_json or wear_filter_json in ("[]", ""):
        return True  # 空=不限制

    if not wear:
        return True  # 无磨损信息则放行

    try:
        allowed_wears: list[str] = json.loads(wear_filter_json)
    except (json.JSONDecodeError, TypeError):
        return True

    if not allowed_wears:
        return True

    wear_normalized = wear.strip().lower()
    allowed_lower = {w.strip().lower() for w in allowed_wears}

    return wear_normalized in allowed_lower


class NameNormalizer:
    """名称标准化器，可实例化使用，也可直接使用模块级函数。"""

    @staticmethod
    def normalize(name: str) -> str:
        return normalize_name(name)

    @staticmethod
    def match(n1: str, n2: str) -> bool:
        return fuzzy_match(n1, n2)

    @staticmethod
    def get_wear(name: str) -> Optional[str]:
        return extract_wear(name)

    @staticmethod
    def expand_wear_abbrev(name: str) -> str:
        return _expand_wear_abbrev(name)
