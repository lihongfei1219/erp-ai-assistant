"""Check local config; --probe explicitly sends only synthetic questions to the model."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.ai.model_client import ModelClient, load_model_settings
from app.ai.question_bounds import question_bounds, validate_grounding
from app.ai.sales_intent import ModelDecision, parse_model_decision

CASES = [
    ('summary', '统计一下2026年9月9号到15号的整体销售表现',
     ('sales_summary', '2026-09-09', '2026-09-16', 'amount', 10)),
    ('ranking', '2026年9月9日到15日，按销售额列出最热销的五个商品',
     ('product_ranking', '2026-09-09', '2026-09-16', 'amount', 5)),
    ('trend', '把2026年9月9号到15号每天的销售金额列一下',
     ('sales_trend', '2026-09-09', '2026-09-16', 'amount', 10)),
    ('relative', '过去七天整体生意怎么样',
     ('sales_summary', '2026-09-13', '2026-09-20', 'amount', 10)),
    ('missing-date', '哪几个商品卖得好', None),
    ('filter', '2026年9月9日到15日，上海客户的销售额是多少', None),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    try:
        settings = load_model_settings()
        print('Model configured:', settings.enabled)
        if not args.probe:
            return 0 if settings.enabled else 1
        client = ModelClient(settings)
        for name, question, expected in CASES:
            content = client.interpret(question, date(2026, 9, 20))
            decision = ModelDecision.model_validate(json.loads(content))
            if expected is None:
                correct = decision.action == 'clarify'
            else:
                intent = parse_model_decision(content)
                bounds = question_bounds(question, date(2026, 9, 20))
                validate_grounding(question, bounds, intent)
                actual = (intent.tool, str(intent.start_date), str(intent.end_date_exclusive),
                          intent.metric, intent.top_n)
                correct = actual == expected
            print(name, 'PASS' if correct else 'FAIL')
            if not correct:
                return 1
        return 0
    except ValueError:
        print('Probe failed; no raw response, configuration or exception was printed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
