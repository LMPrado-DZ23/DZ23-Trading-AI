from tradingagents.agents.utils.agent_utils import (
    gather_wrapped_reports,
    get_instrument_context_from_state,
    get_language_instruction,
    opponent_argument_or_opening,
)


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
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
        bear_history = investment_debate_state.get("bear_history", "")

        _raw_opp = investment_debate_state.get("current_response", "")
        current_response = opponent_argument_or_opening(
            _raw_opp, "bull analyst"
        )
        if _raw_opp.strip():
            current_response = wrap_report_for_prompt(
                "Bull Analyst", current_response, state.get("trade_date")
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

        prompt = f"""You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.
""" + get_language_instruction(state.get("output_language", "English"))

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history_raw + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
