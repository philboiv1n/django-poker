from collections import defaultdict
from asgiref.sync import sync_to_async
from treys import Card
from ..models import Game, Player
from ..utils import get_next_phase, find_best_five_cards, convert_treys_str_int_pretty


class PhasesMixin:
    """Betting phase management: is_phase_over, end_phase, goto_next_phase, handle_showdown."""

    async def is_phase_over(self, game: Game) -> bool:
        """
        Determines if the current betting phase should end.

        The phase ends under the following conditions:
        - All non-folded players have called the highest bet or gone all-in.
        - All active players have checked if no bets were placed.
        - In preflop phase, ensures the big blind has acted if the small blind just called.
        - The phase does not end if fewer than two players remain.

        Args:
            game (Game): The current game instance.

        Returns:
            bool: True if the phase should end, False otherwise.
        """

        print("* CHECK IF PHASE IS OVER")

        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True,
        )()

        # if no player or 1 player left (winner), stop
        if len(active_players) <= 1:
            return False

        # Only players who can still act (not all-in) determine if the phase is over
        eligible_players = [p for p in active_players if not p.is_all_in]
        if not eligible_players:
            return True  # Everyone is all-in; move to next phase

        highest_bet = max(p.current_bet for p in eligible_players)

        # If all eligible players have checked with no bet
        all_players_checked = all(p.has_checked for p in eligible_players)
        all_players_matched_bet = all(p.current_bet == highest_bet for p in eligible_players)

        # If it's preflop and the big blind hasn't acted yet
        if game.current_phase == "preflop":
            big_blind_player = next((p for p in eligible_players if p.is_big_blind), None)
            if big_blind_player is not None and not big_blind_player.has_acted_this_round:
                return False

        # Normal scenario #1: there's a bet, everyone matched
        if highest_bet > 0 and all_players_matched_bet:
            phase_over = True
        # Normal scenario #2: no bet, all checked
        elif highest_bet == 0 and all_players_checked:
            phase_over = True
        else:
            phase_over = False

        print("* Checking if phase is over...")
        for p in active_players:
            print(f"Player {p.position}: bet={p.current_bet}, acted={p.has_acted_this_round}, folded={p.has_folded}, all_in={p.is_all_in}")
        print(f"Highest bet: {highest_bet}")
        print(f"All players checked: {all_players_checked}")
        print(f"All players matched bet: {all_players_matched_bet}")
        return phase_over

    async def end_phase(self, game: Game, winner=None) -> None:
        """
        Ends the current betting phase and prepares for the next phase or starts a new hand.

        If a winner is specified (only one player left), awards the pot to that player
        and begins a new hand. Otherwise, advances to the next phase or starts a new
        hand if the showdown is reached. Resets player statuses accordingly.

        Args:
            game (Game): The current game instance.
            winner (Player, optional): The player who wins by default due to all others folding.

        Returns:
            None
        """

        print("* END PHASE")

        # Reset each player's current bet & checked status for the next phase/hand
        players = await sync_to_async(
            lambda: list(game.players.all()), thread_sensitive=True
        )()
        for player in players:
            player.current_bet = 0
            player.has_checked = False
            player.has_acted_this_round = False
            player.can_reraise_this_round = True
        await sync_to_async(
            lambda: Player.objects.bulk_update(
                players, ["current_bet", "has_checked", "has_acted_this_round", "can_reraise_this_round"]
            )
        )()

        # Reset raise delta for the new betting round
        game.last_raise_delta = 0
        await sync_to_async(lambda: game.save(update_fields=["last_raise_delta"]))()

        # If there's a forced winner (1 player left after folds),
        if winner:
            # Get the current pot amount
            pot = await sync_to_async(lambda: game.get_pot(), thread_sensitive=True)()
            winner.chips += pot
            await sync_to_async(lambda: winner.save(update_fields=["chips"]))()

            username = await sync_to_async(lambda: winner.user.username)()
            await self.broadcast_messages(
                f"🏆 {username} is the last player and wins the pot of {pot} chips!"
            )

            # Start a fresh hand right away (since this hand ended by fold)
            await self.start_hand(game)
            return

        await self.goto_next_phase(game)

        # if we reach the last round, start a new hand
        if game.current_phase == "showdown":
            await self.start_hand(game)
            return

        # else move current player after the dealer
        else:
            await self.next_player(game, game.dealer_position)

    async def goto_next_phase(self, game: Game) -> None:
        """
        Find and move to the next phase. At each stage, the appropriate
        community cards are dealt and the game state is updated accordingly.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        print("* GOTO NEXT PHASE")
        next_phase = get_next_phase(game.current_phase)
        game.current_phase = next_phase
        await sync_to_async(lambda: game.save(update_fields=["current_phase"]))()

        print("** NEXT PHASE :", next_phase)
        if next_phase not in {"flop", "turn", "river", "showdown"}:
            return  # Safety check

        if next_phase == "showdown":
            await self.handle_showdown(game)
            return

        # Burn one card
        Game.burn_card(game)

        # Determine how many cards to deal
        cards_to_deal = 3 if next_phase == "flop" else 1
        dealt_cards = game.deck[:cards_to_deal]
        game.community_cards.extend(dealt_cards)
        game.deck = game.deck[cards_to_deal:]

        # Save
        await sync_to_async(lambda: game.save(update_fields=["deck", "community_cards"]))()

        # Broadcast
        cards_pretty = await sync_to_async(convert_treys_str_int_pretty)(game.community_cards)
        phase_label = f"📡 {next_phase.capitalize()} : {cards_pretty}"
        await self.broadcast_messages(phase_label)

    async def handle_showdown(self, game: Game) -> None:
        """
        Determines winners and distributes the pot among eligible players.

        Handles the showdown by evaluating each remaining player's best 5-card hand,
        constructing side pots based on total bets, and awarding chips to the winners
        of each side pot. Uses Treys hand evaluator for scoring. Players with equal
        best hands split the pot equally.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        print("* MOVE TO SHOWDOWN")

        active_players = await sync_to_async(
            lambda: list(game.players.select_related("user").filter(has_folded=False)),
            thread_sensitive=True,
        )()

        if not active_players:
            return  # Safety check

        # Sort players by total bet (all players, including folded)
        all_players = await sync_to_async(
            lambda: list(game.players.select_related("user").all()), thread_sensitive=True
        )()
        all_players.sort(key=lambda p: p.total_bet)

        # Build side pots including folded players' contributions
        side_pots = []
        previous_bet = 0
        for i, player in enumerate(all_players):
            current_bet = player.total_bet
            if current_bet > previous_bet:
                diff = current_bet - previous_bet
                pot_size = diff * (len(all_players) - i)
                eligible_players = [p.id for p in all_players[i:] if not p.has_folded]
                side_pots.append({"amount": pot_size, "eligible_ids": eligible_players})
                previous_bet = current_bet

        print(side_pots)

        # Evaluate each player's best 5-card hand
        player_hands = []
        for player in active_players:
            combined_cards = game.community_cards + player.hole_cards
            score, rank, best_5_ints = await sync_to_async(find_best_five_cards)(combined_cards)
            player_hands.append((score, rank, best_5_ints, player))

        # Sort from best to worst (lowest treys score = best hand)
        player_hands.sort(key=lambda x: x[0])

        winnings = defaultdict(lambda: {
            "chips_won": 0,
            "best_score": None,
            "best_rank": "",
            "best_five": None,
        })

        # Distribute side pots
        for pot in side_pots:
            pot_amount = pot["amount"]
            eligible_ids = pot["eligible_ids"]

            # Filter out the players who are eligible for this pot
            in_contest = [(s, r, b5, p) for (s, r, b5, p) in player_hands if p.id in eligible_ids]
            if not in_contest:
                continue

            best_score = in_contest[0][0]
            winners = [(s, r, b5, p) for (s, r, b5, p) in in_contest if s == best_score]
            share = pot_amount // len(winners)
            remainder = pot_amount % len(winners)

            for (win_score, win_rank, win_5, win_player) in winners:
                winnings[win_player]["chips_won"] += share
                winnings[win_player]["best_score"] = win_score
                winnings[win_player]["best_rank"] = win_rank
                winnings[win_player]["best_five"] = win_5
                win_player.chips += share

            # Award the odd chip(s) to the winner(s) sitting closest left of the dealer
            if remainder > 0:
                dealer_pos = game.dealer_position or 0
                winners_sorted = sorted(
                    winners,
                    key=lambda x: (x[3].position - dealer_pos - 1) % (max(p.position for p in all_players) + 1)
                )
                odd_chip_player = winners_sorted[0][3]
                odd_chip_player.chips += remainder
                winnings[odd_chip_player]["chips_won"] += remainder

        # Persist all chip changes in a single bulk_update
        await sync_to_async(
            lambda: Player.objects.bulk_update(list(winnings.keys()), ["chips"])
        )()

        # Now broadcast once per winning player
        for win_player, info in winnings.items():
            username = win_player.user.username
            best_five_str = Card.ints_to_pretty_str(info["best_five"]).replace(",", "")
            rank_desc = info["best_rank"]
            total_chips = info["chips_won"]

            await self.broadcast_messages(
                f"🏆 {username} wins {total_chips} with {best_five_str} ({rank_desc})"
            )
