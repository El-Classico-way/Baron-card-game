import pygame
import random
from enum import Enum
from typing import List, Optional
from abc import ABC, abstractmethod

# Initialize Pygame
pygame.init()


class Suit(Enum):
    HEARTS = "hearts"
    DIAMONDS = "diamonds"
    CLUBS = "clubs"
    SPADES = "spades"


class Rank(Enum):
    ACE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13


class Card:
    """Represents a single playing card."""

    def __init__(self, suit: Suit, rank: Rank):
        self.suit = suit
        self.rank = rank
        self.face_up = False
        self.rect = pygame.Rect(0, 0, 80, 120)

    def __str__(self) -> str:
        return f"{self.rank.name} of {self.suit.value.upper()}"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Card):
            return False
        return self.suit == other.suit and self.rank == other.rank

    def flip(self) -> None:
        """Toggle the card between face up and face down."""
        self.face_up = not self.face_up

    def draw(self, surface: pygame.Surface, x: int, y: int) -> None:
        """Draw the card at the specified position."""
        self.rect.x = x
        self.rect.y = y

        if self.face_up:
            self._draw_face(surface)
        else:
            self._draw_back(surface)

    def _draw_face(self, surface: pygame.Surface) -> None:
        """Draw the face of the card."""
        # Card background
        pygame.draw.rect(surface, (255, 255, 255), self.rect)
        pygame.draw.rect(surface, (0, 0, 0), self.rect, 2)

        # Card text
        font = pygame.font.Font(None, 24)
        rank_text = str(self.rank.value) if self.rank.value <= 10 else self.rank.name[0]
        suit_symbol = self._get_suit_symbol()

        text_surface = font.render(f"{rank_text}{suit_symbol}", True, self._get_suit_color())
        text_rect = text_surface.get_rect(center=self.rect.center)
        surface.blit(text_surface, text_rect)

    def _draw_back(self, surface: pygame.Surface) -> None:
        """Draw the back of the card."""
        pygame.draw.rect(surface, (0, 0, 150), self.rect)
        pygame.draw.rect(surface, (0, 0, 0), self.rect, 2)

        # Simple pattern
        for i in range(3):
            for j in range(5):
                x = self.rect.x + 15 + j * 10
                y = self.rect.y + 20 + i * 25
                pygame.draw.circle(surface, (255, 255, 255), (x, y), 3)

    def _get_suit_symbol(self) -> str:
        symbols = {
            Suit.HEARTS: "♥",
            Suit.DIAMONDS: "♦",
            Suit.CLUBS: "♣",
            Suit.SPADES: "♠"
        }
        return symbols[self.suit]

    def _get_suit_color(self) -> tuple:
        return (255, 0, 0) if self.suit in [Suit.HEARTS, Suit.DIAMONDS] else (0, 0, 0)


class Deck:
    """Represents a deck of playing cards."""

    def __init__(self):
        self.cards: List[Card] = []
        self._create_deck()
        self.shuffle()

    def _create_deck(self) -> None:
        """Create a standard 52-card deck."""
        for suit in Suit:
            for rank in Rank:
                self.cards.append(Card(suit, rank))

    def shuffle(self) -> None:
        """Shuffle the deck."""
        random.shuffle(self.cards)

    def deal_card(self) -> Optional[Card]:
        """Deal a card from the top of the deck."""
        return self.cards.pop() if self.cards else None

    def is_empty(self) -> bool:
        """Check if the deck is empty."""
        return len(self.cards) == 0

    def size(self) -> int:
        """Return the number of cards in the deck."""
        return len(self.cards)


class Hand:
    """Represents a player's hand of cards."""

    def __init__(self):
        self.cards: List[Card] = []

    def add_card(self, card: Card) -> None:
        """Add a card to the hand."""
        self.cards.append(card)

    def remove_card(self, card: Card) -> bool:
        """Remove a card from the hand."""
        if card in self.cards:
            self.cards.remove(card)
            return True
        return False

    def clear(self) -> None:
        """Remove all cards from the hand."""
        self.cards.clear()

    def size(self) -> int:
        """Return the number of cards in the hand."""
        return len(self.cards)

    def draw(self, surface: pygame.Surface, start_x: int, start_y: int, spacing: int = 90) -> None:
        """Draw all cards in the hand."""
        for i, card in enumerate(self.cards):
            card.draw(surface, start_x + i * spacing, start_y)


class Player(ABC):
    """Abstract base class for players."""

    def __init__(self, name: str):
        self.name = name
        self.hand = Hand()
        self.score = 0

    @abstractmethod
    def make_move(self, game_state) -> None:
        """Make a move in the game."""
        pass

    def reset(self) -> None:
        """Reset the player for a new game."""
        self.hand.clear()
        self.score = 0


class HumanPlayer(Player):
    """Human player implementation."""

    def __init__(self, name: str = "Player"):
        super().__init__(name)
        self.selected_card = None

    def make_move(self, game_state) -> None:
        """Handle human player input."""
        # This will be handled by the game's event system
        pass

    def select_card(self, mouse_pos: tuple) -> Optional[Card]:
        """Select a card based on mouse a position."""
        for card in self.hand.cards:
            if card.rect.collidepoint(mouse_pos):
                self.selected_card = card
                return card
        return None


class AIPlayer(Player):
    """AI player implementation."""

    def __init__(self, name: str = "AI"):
        super().__init__(name)

    def make_move(self, game_state):
        """Make an AI move (basic random strategy)."""
        if self.hand.cards:
            # Simple AI: play a random card
            card = random.choice(self.hand.cards)
            return card
        return None


class GameState:
    """Manages the current state of the game."""

    def __init__(self):
        self.current_player_index = 0
        self.game_phase = "setup"  # setup, playing, finished
        self.winner = None
        self.played_cards = []

    def next_player(self, num_players: int) -> None:
        """Move to the next player."""
        self.current_player_index = (self.current_player_index + 1) % num_players

    def is_game_over(self) -> bool:
        """Check if the game is over."""
        return self.game_phase == "finished"


class CardGame:
    """Main game class that orchestrates the card game."""

    def __init__(self, width: int = 1000, height: int = 700):
        self.width = width
        self.height = height
        self.screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption("Card Game")
        self.clock = pygame.time.Clock()

        # Game components
        self.deck = Deck()
        self.players: List[Player] = []
        self.game_state = GameState()

        # Colors
        self.bg_color = (0, 128, 0)  # Green felt
        self.white = (255, 255, 255)
        self.black = (0, 0, 0)

        # Initialize game
        self._setup_game()

    def _setup_game(self) -> None:
        """Set up the initial game state."""
        # Create players
        self.players.append(HumanPlayer("You"))
        self.players.append(AIPlayer("AI Player"))

        # Deal initial cards
        self._deal_initial_cards()

        self.game_state.game_phase = "playing"

    def _deal_initial_cards(self, cards_per_player: int = 7) -> None:
        """Deal initial cards to all players."""
        for _ in range(cards_per_player):
            for player in self.players:
                card = self.deck.deal_card()
                if card:
                    card.face_up = True  # For this example, show all cards
                    player.hand.add_card(card)

    def handle_events(self) -> bool:
        """Handle pygame events."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:  # Left-click
                    self._handle_mouse_click(event.pos)

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_r:  # R key to restart
                    self._restart_game()

        return True

    def _handle_mouse_click(self, mouse_pos: tuple) -> None:
        """Handle mouse clicks."""
        current_player = self.players[self.game_state.current_player_index]

        if isinstance(current_player, HumanPlayer):
            selected_card = current_player.select_card(mouse_pos)
            if selected_card:
                self._play_card(current_player, selected_card)

    def _play_card(self, player: Player, card: Card) -> None:
        """Play a card from a player's hand."""
        if player.hand.remove_card(card):
            self.game_state.played_cards.append((player, card))
            self._check_win_condition()
            self.game_state.next_player(len(self.players))

    def _check_win_condition(self) -> None:
        """Check if someone has won the game."""
        for player in self.players:
            if player.hand.size() == 0:
                self.game_state.winner = player
                self.game_state.game_phase = "finished"
                break

    def _restart_game(self) -> None:
        """Restart the game."""
        self.deck = Deck()
        for player in self.players:
            player.reset()
        self.game_state = GameState()
        self._setup_game()

    def update(self) -> None:
        """Update game logic."""
        if self.game_state.game_phase == "playing":
            current_player = self.players[self.game_state.current_player_index]

            # Handle AI turn
            if isinstance(current_player, AIPlayer):
                card = current_player.make_move(self.game_state)
                if card:
                    self._play_card(current_player, card)

    def draw(self) -> None:
        """Draw the game."""
        self.screen.fill(self.bg_color)

        # Draw players' hands
        for i, player in enumerate(self.players):
            y_pos = 50 if i == 1 else self.height - 170  # AI at top, human at bottom
            player.hand.draw(self.screen, 50, y_pos)

            # Draw player name and card count
            font = pygame.font.Font(None, 36)
            text = f"{player.name}: {player.hand.size()} cards"
            text_surface = font.render(text, True, self.white)
            self.screen.blit(text_surface, (50, y_pos - 30))

        # Draw played cards area
        font = pygame.font.Font(None, 24)
        played_text = font.render("Played Cards:", True, self.white)
        self.screen.blit(played_text, (400, 250))

        for i, (player, card) in enumerate(self.game_state.played_cards[-5:]):  # Show last 5
            card.draw(self.screen, 400 + i * 90, 280)

        # Draw game status
        if self.game_state.winner:
            font = pygame.font.Font(None, 48)
            win_text = font.render(f"{self.game_state.winner.name} Wins!", True, self.white)
            text_rect = win_text.get_rect(center=(self.width // 2, self.height // 2))
            self.screen.blit(win_text, text_rect)

            restart_text = font.render("Press R to restart", True, self.white)
            restart_rect = restart_text.get_rect(center=(self.width // 2, self.height // 2 + 60))
            self.screen.blit(restart_text, restart_rect)

        # Draw current player indicator
        if not self.game_state.is_game_over():
            current_player = self.players[self.game_state.current_player_index]
            turn_text = font.render(f"{current_player.name}'s Turn", True, self.white)
            self.screen.blit(turn_text, (self.width - 200, 20))

        pygame.display.flip()

    def run(self) -> None:
        """Main game loop."""
        running = True
        while running:
            running = self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(60)

        pygame.quit()


# Example usage
if __name__ == "__main__":
    game = CardGame()
    game.run()
