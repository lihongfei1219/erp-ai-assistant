from datetime import date

import pytest

from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion


def plan(*kinds, start="2026-09-01", end="2026-09-08", **extra):
    return AnalysisPlan(
        steps=[dict(kind=kind, start_date=start, end_date_exclusive=end, **extra) for kind in kinds]
    )


@pytest.mark.parametrize(
    "question, result",
    [
        ("统计2026年9月1日至7日销售概览", plan("summary")),
        (
            "2026年9月1日至7日每日销售趋势，并列出商品销售额前5名",
            AnalysisPlan(
                steps=[
                    dict(kind="trend", start_date="2026-09-01", end_date_exclusive="2026-09-08"),
                    dict(
                        kind="product_ranking",
                        start_date="2026-09-01",
                        end_date_exclusive="2026-09-08",
                        top_n=5,
                    ),
                ]
            ),
        ),
        (
            "比较2026年9月8日至14日与2026年9月1日至7日的销售额，按商品拆解变化贡献",
            plan(
                "comparison",
                start="2026-09-08",
                end="2026-09-15",
                comparison_start_date="2026-09-01",
                comparison_end_date_exclusive="2026-09-08",
            ),
        ),
        ("上周每日销售趋势", plan("trend", start="2026-09-07", end="2026-09-14")),
    ],
)
def test_supported_questions_are_independently_grounded(question, result):
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(AnalysisQuestion(question=question), result, date(2026, 9, 20))


def test_followup_uses_previous_dates():
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(
        AnalysisQuestion(question="再看客户排行", previous_plan=plan("summary")),
        plan("buyer_ranking"),
        date(2026, 9, 20),
    )


@pytest.mark.parametrize(
    "wording",
    [
        "分析2026年9月1日至5号的销售数据，哪些药品卖的好",
        "分析2026年9月1日至5号的销售数据，哪些商品卖的好",
        "麻烦帮我看看2026年9月1日至5日哪些产品比较畅销，谢谢",
        "2026/9/1到2026/9/5，哪些商品卖得不错，给我列个榜单",
        "2026年9月1日至5号什么商品最受欢迎",
    ],
)
def test_colloquial_bestsellers_default_to_amount_top_ten(wording):
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(
        AnalysisQuestion(question=wording),
        plan("product_ranking", end="2026-09-06"),
        date(2026, 9, 20),
    )


def test_followup_can_change_ranking_metric_and_limit():
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(
        AnalysisQuestion(question="换成按订单数排，取前5名", previous_plan=plan("product_ranking")),
        plan("product_ranking", metric="orders", top_n=5),
        date(2026, 9, 20),
    )


def test_followup_keeps_explicit_metric_when_only_limit_changes():
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(
        AnalysisQuestion(
            question="取前5名", previous_plan=plan("product_ranking", metric="orders")
        ),
        plan("product_ranking", metric="orders", top_n=5),
        date(2026, 9, 20),
    )


@pytest.mark.parametrize("wording", ["再看商品排行", "换成订单量"])
def test_followup_preserves_same_ranking_object_settings(wording):
    from app.ai.analytics_bounds import validate_question_plan

    previous = plan("product_ranking", metric="orders", top_n=5)
    validate_question_plan(
        AnalysisQuestion(question=wording, previous_plan=previous),
        previous,
        date(2026, 9, 20),
    )


def test_changed_ranking_object_uses_fresh_defaults():
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(
        AnalysisQuestion(
            question="再看客户排行", previous_plan=plan("product_ranking", metric="orders", top_n=5)
        ),
        plan("buyer_ranking"),
        date(2026, 9, 20),
    )


@pytest.mark.parametrize("wording", ["排行", "排名", "排序", "排"])
def test_formal_and_colloquial_ranking_words_remain_valid(wording):
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(
        AnalysisQuestion(question=f"2026年9月1日至7日药品按订单数{wording}前5名"),
        plan("product_ranking", metric="orders", top_n=5),
        date(2026, 9, 20),
    )


@pytest.mark.parametrize("extra", [{"top_n": 5}, {"metric": "orders"}])
def test_bestseller_defaults_cannot_be_arbitrarily_changed(extra):
    from app.ai.analytics_bounds import validate_question_plan

    with pytest.raises(QueryUnavailable):
        validate_question_plan(
            AnalysisQuestion(question="2026年9月1日至7日哪些商品卖得好"),
            plan("product_ranking", **extra),
            date(2026, 9, 20),
        )


@pytest.mark.parametrize(
    "condition", ["只看药品", "不含器械", "感冒药", "销量最高", "利润最高", "成交金额"]
)
def test_natural_language_does_not_drop_filters_or_quantity(condition):
    from app.ai.analytics_bounds import validate_question_plan

    with pytest.raises(QueryUnavailable):
        validate_question_plan(
            AnalysisQuestion(question=f"2026年9月1日至7日{condition}的商品排行"),
            plan("product_ranking"),
            date(2026, 9, 20),
        )


def test_spaced_chinese_dates_match_the_ui_example():
    from app.ai.analytics_bounds import validate_question_plan

    validate_question_plan(
        AnalysisQuestion(
            question="分析 2026 年 9 月 1 日至 7 日的销售趋势，并列出销售额前 10 的商品"
        ),
        plan("trend", "product_ranking"),
        date(2026, 9, 20),
    )


@pytest.mark.parametrize(
    "question, result",
    [
        ("2026年9月1日至7日商品销售额前5名", plan("product_ranking", top_n=10)),
        ("2026年9月1日至7日客户订单数排行", plan("buyer_ranking", metric="amount")),
        ("2026年9月1日至7日商品和客户排行", plan("product_ranking")),
        ("2026年9月1日至7日只看已出库订单", plan("summary")),
        ("2026年9月1日至7日A公司销售额", plan("summary")),
        ("2026年9月1日至7日销售环比", plan("summary")),
    ],
)
def test_ignored_requirements_are_rejected(question, result):
    from app.ai.analytics_bounds import validate_question_plan

    with pytest.raises(QueryUnavailable):
        validate_question_plan(AnalysisQuestion(question=question), result, date(2026, 9, 20))
