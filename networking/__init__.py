"""
server/networking/__init__.py

Public API surface of the networking package.

External code (main.py, tests) imports from here.
Internal modules import from each other directly.
"""

from networking.client import ConnectedClient, ConnectionState, RECONNECT_GRACE_SECONDS
from networking.protocol import (
    # Inbound
    InboundMessage,
    JoinRoomPayload,
    LeaveRoomPayload,
    DrawCardsPayload,
    PlayCardPayload,
    DiscardCardPayload,
    EndTurnPayload,
    StartGamePayload,
    SetModePayload,
    VydrzChoosePayload,
    VydrzNextRoundPayload,
    mk_vydrz_state,
    RequestSnapshotPayload,
    ChatPayload,
    parse_inbound,
    # Outbound builders
    mk_action_ack,
    mk_chat,
    mk_error,
    mk_game_event,
    mk_joined,
    mk_player_connected,
    mk_player_disconnected,
    mk_private_hand,
    mk_room_state,
    mk_state_snapshot,
)
from networking.room import Room, RoomPhase
from networking.server import app, RoomRegistry, registry

__all__ = [
    # Client
    "ConnectedClient",
    "ConnectionState",
    "RECONNECT_GRACE_SECONDS",
    # Protocol — inbound
    "InboundMessage",
    "JoinRoomPayload",
    "LeaveRoomPayload",
    "DrawCardsPayload",
    "PlayCardPayload",
    "DiscardCardPayload",
    "EndTurnPayload",
    "StartGamePayload",
    "SetModePayload",
    "VydrzChoosePayload",
    "VydrzNextRoundPayload",
    "mk_vydrz_state",
    "RequestSnapshotPayload",
    "ChatPayload",
    "parse_inbound",
    # Protocol — outbound
    "mk_action_ack",
    "mk_chat",
    "mk_error",
    "mk_game_event",
    "mk_joined",
    "mk_player_connected",
    "mk_player_disconnected",
    "mk_private_hand",
    "mk_room_state",
    "mk_state_snapshot",
    # Room
    "Room",
    "RoomPhase",
    # Server
    "app",
    "RoomRegistry",
    "registry",
]
