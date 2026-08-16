from __future__ import annotations

import json
from pathlib import Path

from telemulator.catalog import catalog_version, classify_method, load_catalog


def test_implemented_and_real_but_missing_are_in_catalog() -> None:
  catalog = load_catalog()
  for name in (
    "getMe",
    "sendMessage",
    "sendPoll",
    "banChatMember",
    "answerInlineQuery",
    "getUpdates",
  ):
    assert name in catalog


def test_typo_is_not_in_catalog() -> None:
  assert "sendMesage" not in load_catalog()


def test_classify_distinguishes_hole_from_typo() -> None:
  implemented = frozenset({"getMe", "sendMessage"})
  assert classify_method("sendMessage", implemented) == "ok"
  assert classify_method("sendPoll", implemented) == "unimplemented"
  assert classify_method("sendMesage", implemented) == "unknown"


def test_catalog_records_where_it_came_from() -> None:
  assert catalog_version()
  assert len(load_catalog()) >= 180


def test_catalog_has_no_duplicates() -> None:
  methods = json.loads(Path("telemulator/catalog.json").read_text(encoding="utf-8"))["methods"]
  assert len(methods) == len(set(methods))


def test_catalog_keeps_heading_verified_methods() -> None:
  catalog = load_catalog()
  for name in (
    "getManagedBotToken",
    "replaceManagedBotToken",
    "getUserProfileAudios",
    "savePreparedKeyboardButton",
    "setChatMemberTag",
  ):
    assert name in catalog
