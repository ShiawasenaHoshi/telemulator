from __future__ import annotations

from typing import Any

UNAUTHORIZED = "Unauthorized"
NOT_FOUND = "Not Found"
CHAT_NOT_FOUND = "Bad Request: chat not found"
CANT_INITIATE = "Forbidden: bot can't initiate conversation with a user"
BLOCKED = "Forbidden: bot was blocked by the user"
MESSAGE_TO_EDIT = "Bad Request: message to edit not found"
CONFLICT_GETUPDATES = (
  "Conflict: terminated by other getUpdates request; "
  "make sure that only one bot instance is running"
)
QUERY_TOO_OLD = (
  "Bad Request: query is too old and response timeout expired or query ID is invalid"
)
NOT_MEMBER_GROUP = "Forbidden: bot is not a member of the group chat"
NOT_MEMBER_SUPER = "Forbidden: bot is not a member of the supergroup chat"
NOT_MEMBER_CHANNEL = "Forbidden: bot is not a member of the channel chat"
KICKED_GROUP = "Forbidden: bot was kicked from the group chat"
KICKED_SUPER = "Forbidden: bot was kicked from the supergroup chat"
KICKED_CHANNEL = "Forbidden: bot was kicked from the channel chat"
NOT_ENOUGH_RIGHTS_SEND = "Forbidden: not enough rights to send text messages to the chat"
USER_NOT_FOUND = "Bad Request: user not found"
METHOD_SUPER_CHANNEL = "Bad Request: method is available only for supergroups and channels"
CANT_REMOVE_OWNER = "Bad Request: can't remove chat owner"
CANT_PROMOTE_OWNER = "Bad Request: can't promote the chat owner"
NOT_ENOUGH_RESTRICT = "Forbidden: not enough rights to restrict/ban chat member"
NOT_ENOUGH_PROMOTE = "Bad Request: not enough rights"


def bot_error(
  code: int, description: str, *, retry_after: int | None = None
) -> tuple[int, dict[str, Any]]:
  body: dict[str, Any] = {"ok": False, "error_code": code, "description": description}
  if retry_after is not None:
    body["parameters"] = {"retry_after": retry_after}
  return code, body
