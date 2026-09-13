from tradingagents.agency_runtime import AgencyRuntime
from tradingagents.telemetry.event_bus import EventBus


def test_runtime_status_event_is_replayable():
    bus = EventBus()
    runtime = AgencyRuntime(event_bus=bus)
    runtime.register_agent("tech-1", "crypto", "technical")
    runtime.set_agent_status("tech-1", "ANALYZING")
    runtime.set_agent_status("tech-1", "PAPER_TRADING")
    assert bus.latest_sequence == 2
    assert len(bus.replay(1)) == 1


def test_meeting_reaches_quorum_and_rejects_duplicate_vote():
    runtime = AgencyRuntime()
    meeting = runtime.start_meeting("m1", "StrategyCommittee", "crypto", ("a", "b", "c"), 2)
    meeting.record_vote("a", "BUY", 0.8, ["e1"])
    meeting.record_vote("b", "BUY", 0.7, ["e2"])
    assert meeting.status == "QUORUM_REACHED"
    try:
        meeting.record_vote("a", "SELL", 0.2, ["e3"])
    except ValueError:
        pass
    else:
        raise AssertionError("voto duplicado deveria ser rejeitado")
