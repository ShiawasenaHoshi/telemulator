from __future__ import annotations

from telemulator.journal import CALLS_LIMIT, Journal

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


def test_record_keeps_status_and_response() -> None:
  journal = Journal()
  rec = journal.record(
    "sendMessage",
    TOKEN,
    "ok",
    {"chat_id": "1", "text": "hi"},
    status=200,
    response={"ok": True, "result": {"message_id": 1}},
  )
  assert rec.status == 200
  assert rec.response["result"]["message_id"] == 1
  assert journal.calls()[-1].status == 200


def test_calls_are_capped() -> None:
  journal = Journal()
  for _ in range(CALLS_LIMIT + 50):
    journal.record("getUpdates", TOKEN, "ok")
  assert len(journal.calls()) == CALLS_LIMIT


def test_holes_survive_a_flood_of_routine_calls() -> None:
  """Журнал дыр — смысл эмулятора; polling не должен его вытеснять."""
  journal = Journal()
  journal.record("sendPoll", TOKEN, "unimplemented")
  for _ in range(CALLS_LIMIT + 50):
    journal.record("getUpdates", TOKEN, "ok")
  assert [rec.method for rec in journal.unimplemented()] == ["sendPoll"]
