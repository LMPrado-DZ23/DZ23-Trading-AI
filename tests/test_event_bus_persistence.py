from tradingagents.telemetry import AuditEvent, AuditLog, EventBus


def test_event_bus_replays_persisted_events_after_restart(tmp_path):
    log = AuditLog(tmp_path / "events.jsonl")
    first = EventBus(log)
    event = AuditEvent.create(event_id="persist-1", event_type="signal.created", sequence=0, aggregate_type="signal", aggregate_id="s1", run_id="r1", actor="test", payload={"value": 1})
    envelope = first.publish(event)
    assert envelope.sequence == 1
    second = EventBus(log)
    replay = second.replay()
    assert len(replay) == 1
    assert replay[0].event.event_id == "persist-1"
    assert replay[0].sequence == 1
    second.publish(event)
    assert len(log.read()) == 1
