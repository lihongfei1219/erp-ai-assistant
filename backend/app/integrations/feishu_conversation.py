"""Server-owned Feishu conversation limits, persisted with each delivered decision."""

MAX_CONVERSATION_ROUNDS = 5


def round_count(payload: dict | None) -> int:
    if not payload:
        return 0
    # A pre-upgrade delivered decision is the first known round; no history rewrite.
    value = payload.get("conversation_round", 1)
    return min(value, MAX_CONVERSATION_ROUNDS) if type(value) is int and value > 0 else 1


def conversation_closed(payload: dict | None) -> bool:
    return round_count(payload) >= MAX_CONVERSATION_ROUNDS


def next_round(previous: dict | None) -> dict:
    return {"conversation_round": 1 if conversation_closed(previous) else round_count(previous) + 1}


def round_notice(payload: dict) -> str:
    count = round_count(payload)
    if conversation_closed(payload):
        return (
            f"本次对话第 {count}/{MAX_CONVERSATION_ROUNDS} 轮，已结束。"
            "下一条完整问题将开启新会话，不沿用本次条件或旧卡片编号。"
        )
    return (
        f"本次对话第 {count}/{MAX_CONVERSATION_ROUNDS} 轮。"
        "30分钟内可继续 @我 追问；发送“重新开始”可提前开启新会话。"
    )
