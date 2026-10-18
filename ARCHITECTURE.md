# ARCHITECTURE.md

# Project Overview

This project is a multiplayer online card game inspired by Bang.

Main goals:

* extensible game engine
* support multiple game modes
* browser/mobile friendly
* self-hostable by friends
* moddable cards and rules
* maintainable long-term architecture

Core philosophy:

* authoritative server
* isolated game logic
* event-driven architecture
* composition over inheritance
* data-driven content

---

# Core Technology Stack

## Backend

* Python 3.12+
* FastAPI
* asyncio
* websockets
* pydantic

## Frontend

* HTML
* CSS
* JavaScript

## Optional Tools

* pygame-ce (debug/local desktop client)
* PyInstaller
* Docker

---

# High-Level Architecture

client UI
↓ websocket
network layer
↓ commands
game engine
↓ events
network layer
↓ websocket
clients

---

# Core Design Rules

## Rule 1 — Server Authoritative

The server is the only source of truth.

Clients:

* send action requests
* render received state/events

Clients NEVER:

* calculate game outcomes
* validate rules
* decide RNG
* modify state directly

---

## Rule 2 — Game Logic Isolation

Game logic must NOT know:

* websockets
* HTTP
* frontend rendering
* browser UI

The engine must be testable entirely from Python.

GOOD:
game.play_card(...)

BAD:
websocket.send(...)
inside game logic.

---

## Rule 3 — Frontend Is Presentation Only

Frontend responsibilities:

* rendering
* animations
* user interaction
* websocket communication

Frontend must not implement game rules.

---

## Rule 4 — Composition Over Inheritance

Avoid deep class hierarchies.

BAD:
AttackCard -> BangCard -> FireBangCard

GOOD:
Card(
effects=[DealDamageEffect()]
)

Prefer:

* reusable effects
* tags
* components
* composition

---

## Rule 5 — Event-Driven Architecture

The engine emits events.

Examples:

* card_played
* player_damaged
* turn_started
* player_eliminated

Events are used for:

* frontend updates
* animations
* logs
* reactions
* spectators
* replay system

---

# Project Structure

project/
│
├── server/
│   ├── api/
│   ├── networking/
│   ├── game/
│   ├── cards/
│   ├── modes/
│   ├── assets/
│   └── main.py
│
├── client/
│   ├── html/
│   ├── css/
│   ├── js/
│   └── assets/
│
├── shared/
│   ├── protocols/
│   └── schemas/
│
├── tests/
│
└── tools/

---

# Server Modules

## /game

Pure game engine.

Contains:

* turn logic
* players
* cards
* deck
* phases
* events
* actions

No networking code allowed.

---

## /networking

Handles:

* websocket clients
* rooms
* serialization
* broadcasting
* reconnect logic

No game rules allowed.

---

## /cards

Contains:

* card definitions
* card loading
* effect mapping

Cards should be loaded from JSON.

---

## /modes

Each mode modifies:

* setup
* victory conditions
* decks
* roles
* special rules

Modes should reuse the same engine.

---

# Naming Conventions

## Python

Classes:
PascalCase

Functions:
snake_case

Constants:
UPPER_SNAKE_CASE

Private methods:
_prefix_method()

---

# Type Hint Policy

All new code should use:

* full type hints
* dataclasses or pydantic where appropriate

Avoid untyped code.

---

# Async Rules

Networking layer:

* async

Game engine:

* mostly synchronous unless needed

Avoid unnecessary async complexity in core logic.

---

# WebSocket Protocol

## General Packet Structure

{
"type": "event_type",
"payload": {}
}

---

# Client → Server Messages

Examples:

* join_room
* play_card
* end_turn
* chat_message

Clients send INTENT only.

Example:

{
"type": "play_card",
"payload": {
"card_id": "bang",
"target_id": "player_2"
}
}

---

# Server → Client Messages

Examples:

* game_state
* game_event
* error
* player_joined

---

# Event System

## Event Rules

Events should:

* be immutable
* contain minimal required data
* avoid direct object references

GOOD:
{
"player_id": "p1"
}

BAD:
{
"player": Player(...)
}

---

# State Management

## Single Source of Truth

Game state exists only on the server.

Frontend stores:

* render state
* temporary UI state

Frontend should be replaceable without changing server logic.

---

# Randomness Rules

All randomness must go through centralized RNG.

Example:
RandomManager

This allows:

* deterministic testing
* replay support
* debugging

Never use random directly inside arbitrary files.

---

# Card System

## Data-Driven Cards

Cards should primarily be data.

Card definitions belong in JSON.

Example:

{
"id": "bang",
"name": "Bang!",
"effects": [
{
"type": "damage",
"value": 1
}
]
}

Avoid hardcoding every card class.

---

# Asset Rules

## Asset Structure

assets/
cards/
avatars/
sounds/
backgrounds/

Card image names should match card IDs.

Example:
bang.webp

---

# Frontend Architecture

## Frontend Responsibilities

Frontend should:

* render game state
* animate events
* send player actions

Frontend should NOT:

* calculate damage
* validate legality
* own authoritative state

---

# UI State Rules

UI state:

* selected cards
* hover state
* animations

Game state:

* server only

---

# Testing Policy

Priority tests:

1. card effects
2. turn flow
3. victory conditions
4. networking serialization

Every major mechanic should eventually have tests.

---

# Performance Philosophy

Do NOT optimize early.

Priority order:

1. clean architecture
2. correctness
3. extensibility
4. optimization later

---

# Anti-Patterns To Avoid

## DO NOT:

* mix networking and game logic
* place rendering inside engine
* create giant God classes
* hardcode all cards
* use deep inheritance trees
* let clients determine outcomes

---

# Preferred Design Style

Prefer:

* small focused classes
* event systems
* reusable effects
* explicit state transitions
* readable code over clever code

---

# Initial Development Roadmap

## Phase 1

Core engine only.

No frontend.

Terminal testing.

---

## Phase 2

WebSocket networking.

Fake clients.

---

## Phase 3

Minimal browser frontend.

No animations.

---

## Phase 4

Visual polish.

Animations.

Mobile improvements.

---

## Phase 5

Modding support.

Custom cards and modes.

---

# Future Features

Potential future additions:

* bots/AI
* replay system
* spectators
* matchmaking
* ranked modes
* persistent accounts
* mod marketplace
* custom scripting

Architecture should remain flexible enough for these additions.

---

# Development Philosophy

Keep systems:

* isolated
* testable
* replaceable
* deterministic

Correct architecture is more important than early features.

---

# Multi-Mode Design Notes

The engine is intended to support multiple fundamentally different game modes.

Examples include:

* role-based turn card games inspired by Bang
* collection/set-building modes
* deterministic matchup modes with pre-selected card combinations
* modes with different deck structures and action systems

Because of this:

* game rules must be mode-driven
* deck generation must be configurable
* action validation must be mode-specific
* phases/turn systems must be extensible
* cards should use tags/components/effects rather than hardcoded assumptions

The project should be treated as a flexible multiplayer card game framework rather than a single fixed game.
