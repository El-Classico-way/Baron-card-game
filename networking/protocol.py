"""
server/networking/protocol.py

Typed WebSocket message protocol.

TWO SIDES OF THE PROTOCOL
──────────────────────────
  INBOUND  (client → server):  Raw JSON is parsed into Pydantic models here.
                                 Validation happens at this boundary — the game
                                 engine only ever receives already-validated data.

  OUTBOUND (server → client):  Plain dicts built by builder functions, then
                                 serialised to JSON by the transport layer.
                                 Builders are thin — they just enforce shape.

WHY PYDANTIC ONLY IN NETWORKING:
  The game engine is intentionally pure Python with no external deps.
  Pydantic belongs here at the untrusted-input boundary, not inside game/.

PACKET ENVELOPE:
  Every message in both directions has the shape:
      { "type": "<string>", "payload": { ... } }

  "type" is the routing key.  The server dispatches on it.
  "payload" is type-specific structured data.

INBOUND MESSAGE TYPES (client → server):
  join_room       Request to join or create a room.
  leave_room      Graceful disconnect from a room.
  draw_cards      Draw-phase action.
  play_card       Play a card, optionally targeting another player.
  discard_card    Discard-phase action.
  end_turn        End current player's turn.
  request_snapshot  Ask for a full state re-sync (reconnect helper).
  chat            Out-of-band chat message (no game effect).

OUTBOUND MESSAGE TYPES (server → client):
  Defined as string constants and built by the mk_* builder functions.
  The frontend dispatches on the "type" field.
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Inbound message envelope
# ---------------------------------------------------------------------------

class InboundMessage(BaseModel):
    """
    Every message from a client must match this envelope.

    The 'payload' field is a raw dict at this level — each action model
    below validates its own payload independently via `from_payload()`.
    Keeping them separate means the dispatcher can route on `type` first,
    then validate the payload with the right model.
    """
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Inbound payload models  (one per message type)
# ---------------------------------------------------------------------------

class JoinRoomPayload(BaseModel):
    """
    Client requests to join a named room.

    If room_id is omitted, the server creates a new room and returns its id.
    display_name is the human-readable name shown to other players.
    reconnect_token, if provided, resumes a disconnected session rather than
    creating a new player slot.
    """
    room_id: str | None = None          # None = create new room
    display_name: str = Field(..., min_length=1, max_length=32)
    reconnect_token: str | None = None  # opaque token issued on first join

    model_config = {"extra": "ignore"}


class LeaveRoomPayload(BaseModel):
    """Client is intentionally leaving. Server cleans up their slot."""
    model_config = {"extra": "ignore"}


class DrawCardsPayload(BaseModel):
    """Draw phase: player draws their standard allotment from the deck."""
    # count is optional — server enforces the correct number for the game mode.
    # Clients may omit it; including it allows future modes with variable draws.
    count: int = Field(default=2, ge=1, le=5)

    model_config = {"extra": "ignore"}


class PlayCardPayload(BaseModel):
    """
    Play phase: player plays a card from their hand.

    card_uid is the instance uid (Card.uid), not the definition id.
    target_id is the player_id of the target, if any.
    """
    card_uid: str = Field(..., min_length=1, max_length=64)
    target_id: str | None = None

    model_config = {"extra": "ignore"}


class DiscardCardPayload(BaseModel):
    """Discard phase: player discards a specific card to meet hand limit."""
    card_uid: str = Field(..., min_length=1, max_length=64)

    model_config = {"extra": "ignore"}


class EndTurnPayload(BaseModel):
    """Player signals they are done with their turn."""
    model_config = {"extra": "ignore"}


class RequestSnapshotPayload(BaseModel):
    """
    Client requests a full state snapshot.
    Sent automatically on reconnect; clients may also request it manually.
    """
    model_config = {"extra": "ignore"}


class ChatPayload(BaseModel):
    """
    Out-of-band chat.  No game effect — forwarded as-is to all room members.
    Content is sanitised (length-capped) but not filtered here.
    """
    message: str = Field(..., min_length=1, max_length=256)

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Inbound message type registry
# ---------------------------------------------------------------------------

# Maps the "type" string to its payload model.
# Used by the dispatcher to validate and route inbound messages.
INBOUND_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "join_room":        JoinRoomPayload,
    "leave_room":       LeaveRoomPayload,
    "draw_cards":       DrawCardsPayload,
    "play_card":        PlayCardPayload,
    "discard_card":     DiscardCardPayload,
    "end_turn":         EndTurnPayload,
    "request_snapshot": RequestSnapshotPayload,
    "chat":             ChatPayload,
}


def parse_inbound(raw: dict[str, Any]) -> tuple[str, BaseModel]:
    """
    Parse and validate a raw client message dict.

    Returns (message_type, validated_payload_model).

    Raises:
        ValueError   if 'type' is missing or unknown.
        pydantic.ValidationError  if payload fields are invalid.

    Usage::

        msg_type, payload = parse_inbound(json.loads(raw_text))
        if msg_type == "play_card":
            assert isinstance(payload, PlayCardPayload)
    """
    envelope = InboundMessage.model_validate(raw)
    msg_type = envelope.type

    payload_cls = INBOUND_PAYLOAD_MODELS.get(msg_type)
    if payload_cls is None:
        raise ValueError(f"Unknown message type: {msg_type!r}")

    validated = payload_cls.model_validate(envelope.payload)
    return msg_type, validated


# ---------------------------------------------------------------------------
# Outbound packet builders  (server → client)
# ---------------------------------------------------------------------------
#
# Each builder returns a plain dict.  The transport layer (client.py) calls
# json.dumps() on it.  Keeping these as functions (not classes) keeps them
# lightweight — they are called very frequently.
#
# Naming convention: mk_<topic>  e.g. mk_error, mk_game_event
#
# ALL outbound packets have shape:
#   { "type": str, "payload": dict }
#
# ---------------------------------------------------------------------------

def mk_error(message: str, code: str = "generic_error") -> dict:
    """
    Send an error to a single client.

    Example::

        { "type": "error",
          "payload": { "message": "It is not your turn.", "code": "not_your_turn" } }
    """
    return {
        "type": "error",
        "payload": {"message": message, "code": code},
    }


def mk_joined(
    room_id: str,
    player_id: str,
    reconnect_token: str,
    display_name: str,
) -> dict:
    """
    Sent to the joining client after a successful join_room.

    Contains the reconnect_token the client must persist to resume
    their session if the connection drops.

    Example::

        { "type": "joined",
          "payload": {
            "room_id": "abc123",
            "player_id": "pid-xyz",
            "reconnect_token": "<opaque>",
            "display_name": "Alice"
          } }
    """
    return {
        "type": "joined",
        "payload": {
            "room_id": room_id,
            "player_id": player_id,
            "reconnect_token": reconnect_token,
            "display_name": display_name,
        },
    }


def mk_room_state(
    room_id: str,
    host_player_id: str,
    players: list[dict],   # [{player_id, display_name, ready}]
    game_phase: str,
    max_players: int,
) -> dict:
    """
    Lobby-phase room state broadcast when a player joins or leaves.

    Example::

        { "type": "room_state",
          "payload": {
            "room_id": "abc123",
            "host_player_id": "pid-xyz",
            "players": [{"player_id": "...", "display_name": "Alice", "ready": true}],
            "game_phase": "lobby",
            "max_players": 7
          } }
    """
    return {
        "type": "room_state",
        "payload": {
            "room_id": room_id,
            "host_player_id": host_player_id,
            "players": players,
            "game_phase": game_phase,
            "max_players": max_players,
        },
    }


def mk_game_event(event_type: str, event_data: dict) -> dict:
    """
    Wrap a serialised GameEvent for transport.

    The frontend dispatches on event_data["type"] for rendering decisions.
    event_data is GameEvent.to_dict() — already a flat dict.

    Example::

        { "type": "game_event",
          "payload": {
            "event_type": "damage_dealt",
            "data": {
              "type": "damage_dealt",
              "source_id": "p1",
              "target_id": "p2",
              "amount": 1,
              "cause": "Bang!",
              "timestamp": 1234567890.0
            }
          } }
    """
    return {
        "type": "game_event",
        "payload": {
            "event_type": event_type,
            "data": event_data,
        },
    }


def mk_state_snapshot(
    public_state: dict,
    private_player: dict,  # player.to_private_dict() for this client only
) -> dict:
    """
    Full state sync packet.  Sent on reconnect and initial game start.

    The public_state is shared with every client; private_player is this
    client's own hand and role — it replaces the public entry for them.

    Clients should replace the player entry in public_state["players"]
    whose player_id matches private_player["player_id"].

    Example::

        { "type": "state_snapshot",
          "payload": {
            "state": { ... },            # GameState.to_public_dict()
            "your_player": { ... }       # player.to_private_dict()
          } }
    """
    return {
        "type": "state_snapshot",
        "payload": {
            "state": public_state,
            "your_player": private_player,
        },
    }


def mk_private_hand(player_dict: dict) -> dict:
    """
    Send a player's updated private hand after it changes (draw, steal, etc.).
    Lighter than a full snapshot — only the hand and metadata change.

    Example::

        { "type": "private_hand",
          "payload": {
            "player_id": "p1",
            "hand": [ { "uid": "bang-abc", "id": "bang", ... } ],
            "hand_count": 5
          } }
    """
    return {
        "type": "private_hand",
        "payload": {
            "player_id": player_dict["player_id"],
            "hand": player_dict.get("hand", []),
            "hand_count": len(player_dict.get("hand", [])),
            "role": player_dict.get("role"),
        },
    }


def mk_chat(sender_id: str, display_name: str, message: str, timestamp: float) -> dict:
    """
    Broadcast a chat message to all room members.

    Example::

        { "type": "chat",
          "payload": {
            "sender_id": "p1",
            "display_name": "Alice",
            "message": "good game!",
            "timestamp": 1234567890.0
          } }
    """
    return {
        "type": "chat",
        "payload": {
            "sender_id": sender_id,
            "display_name": display_name,
            "message": message,
            "timestamp": timestamp,
        },
    }


def mk_player_connected(player_id: str, display_name: str) -> dict:
    """Broadcast when a player connects or reconnects."""
    return {
        "type": "player_connected",
        "payload": {"player_id": player_id, "display_name": display_name},
    }


def mk_player_disconnected(player_id: str, display_name: str) -> dict:
    """Broadcast when a player disconnects (but before their slot is released)."""
    return {
        "type": "player_disconnected",
        "payload": {"player_id": player_id, "display_name": display_name},
    }


def mk_action_ack(action_type: str) -> dict:
    """
    Acknowledge a successful player action back to the acting client.
    Lets the client unblock its UI optimistically.

    Example::

        { "type": "action_ack", "payload": { "action": "play_card" } }
    """
    return {
        "type": "action_ack",
        "payload": {"action": action_type},
    }
