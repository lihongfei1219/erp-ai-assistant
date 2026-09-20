"""Shared colloquial defaults; never erase named entities or explicit filters."""

import re
import unicodedata

# Only equivalent expressions are rewritten. Unknown text still reaches grounding.
ALIASES = {
    "卖的比较好": "热销",
    "卖得比较好": "热销",
    "卖的最好": "热销",
    "卖得最好": "热销",
    "卖的好": "热销",
    "卖得好": "热销",
    "卖得不错": "热销",
    "卖的不错": "热销",
    "比较畅销": "热销",
    "最畅销": "热销",
    "畅销": "热销",
    "最受欢迎": "热销",
    "受欢迎": "热销",
    "卖得怎么样": "销售情况",
    "卖的怎么样": "销售情况",
    "销售业绩": "销售表现",
    "下单数": "订单数",
    "订单量": "订单数",
    "列个榜单": "排行",
    "排个名": "排行",
    "榜单": "排行",
    "按订单数排": "按订单数排行",
    "按金额排": "按金额排行",
    "客户买得多": "客户排行",
    "客户买的多": "客户排行",
    # Approved domain vocabulary: generic 药品 refers to the current product scope.
    # Explicit restrictions (仅/只看/排除/类别) are deliberately not removed.
    "药品": "商品",
}
_ALIASES = re.compile(
    "|".join(
        re.escape(word) + (r"(?!行|名|序)" if word.endswith("排") else "")
        for word in sorted(ALIASES, key=len, reverse=True)
    )
)
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")


def compact_question(question: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", question))


def normalize_question(question: str) -> str:
    question = compact_question(question)
    question = _NUMERIC_DATE.sub(lambda m: f"{m[1]}年{m[2]}月{m[3]}日", question)
    return _ALIASES.sub(lambda m: ALIASES[m[0]], question)


NATURAL_LANGUAGE_DEFAULTS = """
理解用户日常说法，不要求用户使用工具名或技术字段：
“卖得好/卖的好/畅销/热销/受欢迎”默认商品销售金额降序排行，metric=amount，top_n=10。
“哪些客户买得多”默认客户销售金额排行。明确说订单数/订单量/下单数时用orders；明确前N名时用N。
明确说销量/件数/数量不能用金额或订单数代替，仍应说明缺少统一数量单位。
用户确认“药品”是当前商品的日常称呼，泛称药品等同商品；结果由服务端标注当前商品范围、未做分类筛选。
但“只看药品/排除器械/某类药/感冒药”等明确分类或实体限制仍不支持，不能丢掉条件。
“分析销售数据，哪些商品卖得好”只需product_ranking；只有明确要求概览/汇总/总体时才额外生成summary。
9月1日至5号表示9月1日至5日（含5日）；2026/9/1到2026/9/5与中文日期等价。
追问“换成按订单数排，取前5名”可继承上次唯一排行的对象与日期；没有新指标和条数时保留上次排行设置。
没有历史时排行默认金额前10；明确切换到另一排行对象时重新用默认值。
不要为这些已有默认值的指标、条数或包含结束日再次要求确认，直接run。
解释只用用户能理解的含首尾日期，不向用户解释end_date_exclusive或多加一天的实现细节。
"""
