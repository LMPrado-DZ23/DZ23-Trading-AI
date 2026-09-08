import threading
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph

from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.graph.analyst_execution import build_analyst_execution_plan
from tradingagents.graph.setup import create_isolated_analyst_node


def _isolated(spec, analyst):
    return create_isolated_analyst_node(
        spec,
        analyst,
        lambda state: {},
        lambda state: spec.clear_node,
    )


def test_parallel_branches_run_concurrently_and_merge_only_reports():
    market, news = build_analyst_execution_plan(["market", "news"]).specs
    barrier = threading.Barrier(2)
    seen_messages = {}

    def analyst(spec, report):
        def run(state):
            seen_messages[spec.key] = [message.content for message in state["messages"]]
            barrier.wait(timeout=3)
            return {
                "messages": [AIMessage(content=f"private-{spec.key}")],
                spec.report_key: report,
            }

        return run

    workflow = StateGraph(AgentState)
    workflow.add_node("market", _isolated(market, analyst(market, "MARKET")))
    workflow.add_node("news", _isolated(news, analyst(news, "NEWS")))
    workflow.add_node(
        "join",
        lambda state: {
            "investment_plan": f"{state['market_report']}|{state['news_report']}"
        },
    )
    workflow.add_edge(START, "market")
    workflow.add_edge(START, "news")
    workflow.add_edge(["market", "news"], "join")

    result = workflow.compile().invoke({"messages": [HumanMessage(content="seed")]})

    assert result["investment_plan"] == "MARKET|NEWS"
    assert seen_messages == {"market": ["seed"], "news": ["seed"]}
    assert [message.content for message in result["messages"]] == ["seed"]


def test_fan_in_never_runs_when_a_branch_fails():
    market, news = build_analyst_execution_plan(["market", "news"]).specs
    joined = MagicMock(return_value={"investment_plan": "bad"})

    workflow = StateGraph(AgentState)
    workflow.add_node(
        "market",
        _isolated(
            market,
            lambda state: {
                "messages": [AIMessage(content="ok")],
                "market_report": "OK",
            },
        ),
    )
    workflow.add_node(
        "news",
        _isolated(news, lambda state: (_ for _ in ()).throw(RuntimeError("failed"))),
    )
    workflow.add_node("join", joined)
    workflow.add_edge(START, "market")
    workflow.add_edge(START, "news")
    workflow.add_edge(["market", "news"], "join")

    with pytest.raises(RuntimeError, match="failed"):
        workflow.compile().invoke({"messages": [HumanMessage(content="seed")]})
    joined.assert_not_called()


def test_empty_report_fails_closed():
    market = build_analyst_execution_plan(["market"]).specs[0]
    node = _isolated(
        market,
        lambda state: {"messages": [AIMessage(content="no report")]},
    )

    with pytest.raises(RuntimeError, match="completed without market_report"):
        node({"messages": [HumanMessage(content="seed")]}, {})


def test_checkpoint_resume_retries_only_the_failed_parallel_branch():
    market, news = build_analyst_execution_plan(["market", "news"]).specs
    counts = {"market": 0, "news": 0}

    def stable(state):
        counts["market"] += 1
        return {"messages": [AIMessage(content="m")], "market_report": "M"}

    def transient(state):
        counts["news"] += 1
        if counts["news"] == 1:
            raise RuntimeError("transient")
        return {"messages": [AIMessage(content="n")], "news_report": "N"}

    workflow = StateGraph(AgentState)
    workflow.add_node("market", _isolated(market, stable))
    workflow.add_node("news", _isolated(news, transient))
    workflow.add_node(
        "join", lambda state: {"investment_plan": state["market_report"] + state["news_report"]}
    )
    workflow.add_edge(START, "market")
    workflow.add_edge(START, "news")
    workflow.add_edge(["market", "news"], "join")
    graph = workflow.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "parallel-resume"}}

    with pytest.raises(RuntimeError, match="transient"):
        graph.invoke({"messages": [HumanMessage(content="seed")]}, config=config)
    result = graph.invoke(None, config=config)

    assert result["investment_plan"] == "MN"
    assert counts == {"market": 1, "news": 2}
