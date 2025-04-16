"""
utils.py
========

Defines the helper functions.

"""
from treys import Evaluator, Card
from typing import List, Tuple
from itertools import combinations
from .models import Game, Player



# -----------------------------------------------------------------------
def create_deck () -> list:
    """
    """
    suits = ["s", "c", "h", "d"]  # ["♠", "♣", "♥", "♦"]
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
    return [f"{rank}{suit}" for suit in suits for rank in ranks]


# -----------------------------------------------------------------------
def can_user_do_action(game: Game, player: Player, action: str) -> bool:
    """
    
    """
    if player.is_all_in or player.has_folded:
        return False

    highest_bet = max(game.players.values_list("current_bet", flat=True))
    difference = highest_bet - player.current_bet

    if action == "check" and difference > 0 :
        return False
    
    if action == "call" and difference <= 0 :
        return False

    return True


# -----------------------------------------------------------------------
def get_next_phase(current_phase: str) -> str:
    """
    Return the next Texas hold 'em phase
    """
    next_phase = "showdown"
    if current_phase == "preflop":
        next_phase = "flop"
    elif current_phase == "flop":
        next_phase = "turn"
    elif current_phase == "turn":
        next_phase = "river"
    return next_phase


# -----------------------------------------------------------------------
def find_best_five_cards(seven_card_strings: List[str]) -> Tuple[int, str, Tuple[int, int, int, int, int]]:

    """
    Determines the best 5-card hand from a 7-card combination.

    Evaluates all 5-card combinations from the given 7 cards (hole + community),
    and returns the best score, hand rank, and the best 5-card hand as Treys integers.

    Args:
        seven_card_strings (List[str]): A list of 7 card strings, e.g., ['Ah', 'Kd', 'Qs', 'Jh', 'Tc', '9c', '8d'].

    Returns:
        Tuple[int, str, Tuple[int, int, int, int, int]]:
            - best_score (int): Treys score for the best hand (lower is better).
            - best_rank (str): Human-readable classification of the hand (e.g., "Straight", "Flush").
            - best_five_ints (Tuple[int, ...]): Tuple of Treys card integers representing the best hand.
    """
    evaluator = Evaluator()

    # 1. Convert to Treys "card int" objects
    all_seven_cards = [Card.new(c) for c in seven_card_strings]

    best_score = 7642 # 7642 distinctly ranked hands in poker.
    best_five_cards = None

    # 2. Enumerate all 5-card combos
    for combo in combinations(all_seven_cards, 5):
        score = evaluator.evaluate([], list(combo))
        if score <= best_score:
            best_score = score
            best_five_cards = combo

    # 3. Determine rank class
    rank_class = evaluator.get_rank_class(best_score)
    best_rank = evaluator.class_to_string(rank_class)

    # best_five_cards are already "card ints"
    return best_score, best_rank, best_five_cards


# -----------------------------------------------------------------------
def convert_treys_str_int_pretty(str_cards: List[str]) -> str:
    """  
    Converts a list of card strings to Treys pretty string format.

    This function converts string representations of cards (e.g., "Ah", "Kd")
    into Treys card integers, then returns a human-readable string using Treys'
    pretty string format.

    Args:
        str_cards (List[str]): List of card strings to convert.

    Returns:
        str: Treys-formatted pretty string of cards. 
    """
    cards_ints = [Card.new(c) for c in str_cards]
    cards_str = Card.ints_to_pretty_str(cards_ints)
    return cards_str.replace(",","")