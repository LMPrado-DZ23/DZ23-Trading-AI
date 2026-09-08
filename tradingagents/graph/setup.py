# TradingAgents/graph/setup.py

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.utils.agent_states import AgentState

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic

# Every target a shared conditional router can return. Each edge driven by the
# router maps all of them, so a fall-through return (e.g. under prompt/i18n/
# refactor drift in the speaker labels) can never hit a missing path_map entry
# and crash LangGraph mid-run (#1088).
DEBATE_PATH_MAP = {
    "Bull Researcher": "Bull Researcher",
    "Bear Researcher": "Bear Researcher",
    "Research Manager": "Research Manager",
}
RISK_ANALYSIS_PATH_MAP = {
    "Aggressive Analyst": "Aggressive Analyst",
    "Conservative Analyst": "Conservative Analyst",
    "Neutral Analyst": "Neutral Analyst",
    "Portfolio Manager": "Portfolio Manager",
}


def create_isolated_analyst_node(spec, analyst_node, tool_node, router):
    """Run one analyst/tool loop in private state and return only its report.

    Parallel branches must not share their temporary message/tool transcript.
    The outer graph therefore sees a single atomic update to the analyst's
    unique report key, which makes the fan-in deterministic and checkpointable.
    """
    branch = StateGraph(AgentState)
    branch.add_node("analyst", analyst_node)
    branch.add_node("tools", tool_node)
    branch.add_edge(START, "analyst")

    def route(state):
        return "tools" if router(state) == spec.tool_node else END

    branch.add_conditional_edges("analyst", route, {"tools": "tools", END: END})
    branch.add_edge("tools", "analyst")
    compiled = branch.compile()

    def isolated(state, config):
        branch_state = dict(state)
        branch_state["messages"] = list(state["messages"])
        branch_config = dict(config or {})
        branch_config["metadata"] = {
            **dict(branch_config.get("metadata") or {}),
            "tradingagents_analyst": spec.key,
        }
        result = compiled.invoke(branch_state, config=branch_config)
        report = result.get(spec.report_key, "")
        if not report:
            raise RuntimeError(f"{spec.agent_node} completed without {spec.report_key}")
        return {spec.report_key: report}

    return isolated


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        runtime_config: dict | None = None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.runtime_config = runtime_config or {}

    def setup_graph(
        self, selected_analysts=("market", "social", "news", "fundamentals")
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        plan = build_analyst_execution_plan(selected_analysts)

        analyst_factories = {
            "market": lambda: create_market_analyst(self.quick_thinking_llm),
            "social": lambda: create_sentiment_analyst(
                self.quick_thinking_llm, self.runtime_config
            ),
            "news": lambda: create_news_analyst(self.quick_thinking_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_thinking_llm),
        }

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create workflow
        workflow = StateGraph(AgentState)

        # Each analyst owns a private message/tool subgraph. Only its report is
        # merged into the outer state, preventing parallel transcript bleed.
        for spec in plan.specs:
            analyst_node = analyst_factories[spec.key]()
            workflow.add_node(
                spec.agent_node,
                create_isolated_analyst_node(
                    spec,
                    analyst_node,
                    self.tool_nodes[spec.key],
                    getattr(self.conditional_logic, f"should_continue_{spec.key}"),
                ),
            )

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # Define edges
        analyst_nodes = [spec.agent_node for spec in plan.specs]
        for analyst_node in analyst_nodes:
            workflow.add_edge(START, analyst_node)
        if len(analyst_nodes) == 1:
            workflow.add_edge(analyst_nodes[0], "Bull Researcher")
        else:
            workflow.add_edge(analyst_nodes, "Bull Researcher")

        # Both research-debate edges share the complete DEBATE_PATH_MAP (#1088).
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,
                DEBATE_PATH_MAP,
            )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        # All three risk edges share the complete RISK_ANALYSIS_PATH_MAP (#1088).
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,
                RISK_ANALYSIS_PATH_MAP,
            )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
