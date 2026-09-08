from tradingagents.agents.utils.agent_utils import (
    gather_wrapped_reports,
    get_instrument_context_from_state,
    get_language_instruction,
    opponent_argument_or_opening,
)


def create_bull_researcher(llm):
    def bull_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        # Rodada-6: debate outputs are model text derived from untrusted
        # sources — classification must not drop between debators.
        from tradingagents.dataflows.evidence import wrap_report_for_prompt

        # raw para PERSISTIR no estado; wrapped SÓ para o prompt — o envelope
        # nunca entra no histórico gravado (achado r7: degradação cumulativa).
        history_raw = investment_debate_state.get("history", "")
        history = wrap_report_for_prompt(
            "Investment Debate", history_raw, state.get("trade_date")
        )
        bull_history = investment_debate_state.get("bull_history", "")

        _raw_opp = investment_debate_state.get("current_response", "")
        current_response = opponent_argument_or_opening(
            _raw_opp, "bear analyst"
        )
        if _raw_opp.strip():
            current_response = wrap_report_for_prompt(
                "Bear Analyst", current_response, state.get("trade_date")
            )
        # Derived-trust wrapping (#security-fase0): reports enter this prompt
        # only inside explicit UNTRUSTED/DERIVED envelopes, via the single
        # sanctioned assembler.
        wrapped = gather_wrapped_reports(state)
        market_research_report = wrapped["market_report"]
        sentiment_report = wrapped["sentiment_report"]
        news_report = wrapped["news_report"]
        fundamentals_report = wrapped["fundamentals_report"]
        instrument_context = get_instrument_context_from_state(state)
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"
        fundamentals_label = (
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        prompt = f"""You are a Bull Analyst advocating for investing in the {target_label}. Your task is to build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Leverage the provided research and data to address concerns and counter bearish arguments effectively.

Key points to focus on:
- Growth Potential: Highlight the company's market opportunities, revenue projections, and scalability.
- Competitive Advantages: Emphasize factors like unique products, strong branding, or dominant market positioning.
- Positive Indicators: Use financial health, industry trends, and recent positive news as evidence.
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bear analyst's points and debating effectively rather than just listing data.

Resources available:
{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}
Use this information to deliver a compelling bull argument, refute the bear's concerns, and engage in a dynamic debate that demonstrates the strengths of the bull position.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bull Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history_raw + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node
