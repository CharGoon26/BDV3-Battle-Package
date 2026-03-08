import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bd_models.models import Ball, BallInstance, Player
from settings.models import settings

from .battle_lib import (
    BattleBall,
    BattleInstance,
    TurnAction,
    MOVES,
    create_battle_from_instances,
)

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.battle")

# Store active battles per guild
active_battles = {}
# Store cooldowns per user
battle_cooldowns = {}


class BattleMoveView(discord.ui.View):
    def __init__(self, battle: BattleInstance, player_name: str):
        super().__init__(timeout=60)
        self.battle = battle
        self.player_name = player_name
        self.selected_move = None

    @discord.ui.button(label="Quick Attack", emoji="⚔️", style=discord.ButtonStyle.primary)
    async def attack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "attack")

    @discord.ui.button(label="Heavy Strike", emoji="💪", style=discord.ButtonStyle.danger)
    async def heavy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "heavy")

    @discord.ui.button(label="Defend", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def defend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "defend")

    @discord.ui.button(label="Recover", emoji="💚", style=discord.ButtonStyle.success)
    async def heal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "heal")

    async def handle_move_selection(self, interaction: discord.Interaction, move_key: str):
        if interaction.user.name != self.player_name:
            await interaction.response.send_message("❌ This isn't your battle turn!", ephemeral=True)
            return
        self.selected_move = move_key
        move = MOVES[move_key]
        await interaction.response.send_message(
            f"✅ You selected: {move.emoji} **{move.name}**\nWaiting for opponent...",
            ephemeral=True,
        )
        self.stop()


class BattleTeamBuilder(discord.ui.View):
    # FIX: Accept the cog instance so done_callback can call _update_battle_setup_message
    def __init__(self, battle_data: dict, is_p1: bool, available_balls: list, user: discord.User, cog: "Battle"):
        super().__init__(timeout=300)
        self.battle_data = battle_data
        self.is_p1 = is_p1
        self.available_balls = available_balls
        self.user = user
        self.cog = cog
        self.battle = battle_data["battle"]
        self.current_team = self.battle.p1_balls if is_p1 else self.battle.p2_balls
        self.update_components()

    def update_components(self):
        self.clear_items()

        if len(self.current_team) < 3:
            team_ball_names = [ball.name for ball in self.current_team]
            available = [ball for ball in self.available_balls if ball.ball.country not in team_ball_names]
            if available:
                select = discord.ui.Select(
                    placeholder="➕ Select cards to add to your team",
                    min_values=0,
                    max_values=min(len(available), min(3 - len(self.current_team), 25)),
                    custom_id="add_balls",
                )
                for ball in available[:25]:
                    select.add_option(
                        label=ball.ball.country[:100],
                        value=str(ball.pk),
                        description=f"ATK: {ball.attack} | HP: {ball.health}",
                    )
                select.callback = self.add_balls_callback
                self.add_item(select)

        if len(self.current_team) > 0:
            remove_select = discord.ui.Select(
                placeholder="➖ Select cards to remove from your team",
                min_values=0,
                max_values=len(self.current_team),
                custom_id="remove_balls",
            )
            for i, ball in enumerate(self.current_team):
                remove_select.add_option(
                    label=ball.name[:100],
                    value=str(i),
                    description=f"ATK: {ball.attack} | HP: {ball.health}",
                )
            remove_select.callback = self.remove_balls_callback
            self.add_item(remove_select)

        done_btn = discord.ui.Button(label="Done", style=discord.ButtonStyle.success, emoji="✅")
        done_btn.callback = self.done_callback
        self.add_item(done_btn)

    def create_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="⚔️ Build Your Battle Team",
            description=f"**Team Size:** {len(self.current_team)}/3 cards\n\nUse the dropdowns below to add or remove cards.",
            color=discord.Color.blue(),
        )
        if self.current_team:
            team_text = "\n".join(
                [f"{i+1}. **{ball.name}** - ATK: {ball.attack} | HP: {ball.health}" for i, ball in enumerate(self.current_team)]
            )
        else:
            team_text = "*No cards selected yet*"
        embed.add_field(name="📋 Current Team", value=team_text, inline=False)
        if len(self.current_team) < 3:
            embed.set_footer(text=f"Add {3 - len(self.current_team)} more card(s) to complete your team")
        else:
            embed.set_footer(text="✅ Team complete! Click Done when ready.")
        return embed

    async def add_balls_callback(self, interaction: discord.Interaction):
        selected_ids = interaction.data["values"]
        for ball_id in selected_ids:
            if len(self.current_team) >= 3:
                break
            ball_instance = next((b for b in self.available_balls if str(b.pk) == ball_id), None)
            if not ball_instance:
                continue
            battle_ball = BattleBall(
                name=ball_instance.ball.country,
                owner=self.user.name,
                health=ball_instance.health,
                attack=ball_instance.attack,
                max_health=ball_instance.health,
                emoji="",
            )
            self.current_team.append(battle_ball)
        self.update_components()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    async def remove_balls_callback(self, interaction: discord.Interaction):
        selected_indices = [int(idx) for idx in interaction.data["values"]]
        for idx in sorted(selected_indices, reverse=True):
            if idx < len(self.current_team):
                self.current_team.pop(idx)
        self.update_components()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    async def done_callback(self, interaction: discord.Interaction):
        if len(self.current_team) < 3:
            await interaction.response.send_message(
                f"❌ You need 3 cards! You currently have {len(self.current_team)}.",
                ephemeral=True,
            )
            return
        for item in self.children:
            item.disabled = True
        embed = self.create_embed()
        embed.title = "✅ Team Complete!"
        embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=embed, view=self)

        # FIX: Update the public battle setup message after team is confirmed
        try:
            await self.cog._update_battle_setup_message(interaction, self.battle_data)
        except Exception as e:
            log.error(f"Failed to update battle setup message after /battle add: {e}")

        self.stop()


def create_battle_embed(battle: BattleInstance, title: str = "⚔️ Battle in Progress") -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"**{battle.p1_name}** vs **{battle.p2_name}**\nTurn: {battle.current_turn}",
        color=discord.Color.red(),
    )
    p1_ball = battle.get_active_ball(battle.p1_name)
    if p1_ball:
        hp_bar = create_hp_bar(p1_ball.health, p1_ball.max_health)
        embed.add_field(
            name=f"{battle.p1_name}'s {p1_ball.name}",
            value=f"{hp_bar}\n⚔️ ATK: {p1_ball.attack} | 💚 HP: {p1_ball.health}/{p1_ball.max_health}",
            inline=True,
        )
    embed.add_field(name="\u200b", value="**VS**", inline=True)
    p2_ball = battle.get_active_ball(battle.p2_name)
    if p2_ball:
        hp_bar = create_hp_bar(p2_ball.health, p2_ball.max_health)
        embed.add_field(
            name=f"{battle.p2_name}'s {p2_ball.name}",
            value=f"{hp_bar}\n⚔️ ATK: {p2_ball.attack} | 💚 HP: {p2_ball.health}/{p2_ball.max_health}",
            inline=True,
        )
    p1_alive = sum(1 for ball in battle.p1_balls if not ball.dead)
    p2_alive = sum(1 for ball in battle.p2_balls if not ball.dead)
    embed.add_field(name=f"{battle.p1_name}'s Team", value=f"{'🟢' * p1_alive}{'🔴' * (3 - p1_alive)}", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name=f"{battle.p2_name}'s Team", value=f"{'🟢' * p2_alive}{'🔴' * (3 - p2_alive)}", inline=True)
    return embed


def create_hp_bar(current_hp: int, max_hp: int, length: int = 10) -> str:
    if max_hp <= 0:
        return "❌"
    percentage = current_hp / max_hp
    filled = int(length * percentage)
    empty = length - filled
    return f"{'🟩' * filled}{'⬜' * empty}"


def check_cooldown(user_id: int) -> Optional[timedelta]:
    if user_id in battle_cooldowns:
        cooldown_end = battle_cooldowns[user_id]
        now = datetime.now()
        if now < cooldown_end:
            return cooldown_end - now
    return None


def set_cooldown(user_id: int, hours: int = 1):
    battle_cooldowns[user_id] = datetime.now() + timedelta(hours=hours)


def check_expired_battles():
    now = datetime.now()
    expired_guilds = [
        guild_id
        for guild_id, battle_data in active_battles.items()
        if "expires_at" in battle_data and now > battle_data["expires_at"]
    ]
    for guild_id in expired_guilds:
        log.info(f"Cleaning up expired battle in guild {guild_id}")
        del active_battles[guild_id]
    return len(expired_guilds)


class Battle(commands.GroupCog, group_name="battle"):
    """
    Interactive turn-based battles with your cards!
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def challenge(self, interaction: discord.Interaction, opponent: discord.Member):
        """
        Challenge another player to a battle!

        Parameters
        ----------
        opponent: discord.Member
            The player you want to battle
        """
        check_expired_battles()

        cooldown = check_cooldown(interaction.user.id)
        if cooldown:
            minutes = int(cooldown.total_seconds() / 60)
            await interaction.response.send_message(f"⏰ You're on cooldown! Try again in {minutes} minutes.", ephemeral=True)
            return

        cooldown = check_cooldown(opponent.id)
        if cooldown:
            minutes = int(cooldown.total_seconds() / 60)
            await interaction.response.send_message(
                f"⏰ {opponent.mention} is on cooldown! They can battle again in {minutes} minutes.", ephemeral=True
            )
            return

        if opponent.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't battle yourself!", ephemeral=True)
            return

        if opponent.bot:
            await interaction.response.send_message("❌ You can't battle bots!", ephemeral=True)
            return

        if interaction.guild_id in active_battles:
            await interaction.response.send_message(
                "❌ There's already a battle happening in this server! Wait for it to finish.", ephemeral=True
            )
            return

        challenger_player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        opponent_player, _ = await Player.objects.aget_or_create(discord_id=opponent.id)

        challenger_balls = await BallInstance.objects.filter(player=challenger_player, deleted=False).acount()
        if challenger_balls < 3:
            await interaction.response.send_message(
                f"❌ You need at least 3 {settings.plural_collectible_name} to battle!", ephemeral=True
            )
            return

        opponent_balls_count = await BallInstance.objects.filter(player=opponent_player, deleted=False).acount()
        if opponent_balls_count < 3:
            await interaction.response.send_message(
                f"❌ {opponent.mention} needs at least 3 {settings.plural_collectible_name} to battle!", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="⚔️ Battle Challenge!",
            description=(
                f"{interaction.user.mention} has challenged {opponent.mention} to a battle!\n\n"
                f"**Rules:**\n"
                f"• Each player must select exactly 3 {settings.plural_collectible_name}\n"
                f"• Use `/battle best` to auto-fill your 3 strongest\n"
                f"• Use `/battle add` to add specific cards interactively\n"
                f"• Once both players have 3 cards and click Ready, battle begins!\n"
                f"• Winner gets progress toward rewards!"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name=f"{interaction.user.name}'s Team", value="Empty (0/3)", inline=True)
        embed.add_field(name=f"{opponent.name}'s Team", value="Empty (0/3)", inline=True)

        view = discord.ui.View(timeout=60)

        async def accept_callback(button_interaction: discord.Interaction):
            if button_interaction.user.id != opponent.id:
                await button_interaction.response.send_message("❌ Only the challenged player can accept!", ephemeral=True)
                return
            battle = BattleInstance(p1_name=interaction.user.name, p2_name=opponent.name, p1_balls=[], p2_balls=[])
            message = button_interaction.message
            active_battles[interaction.guild_id] = {
                "battle": battle,
                "p1_id": interaction.user.id,
                "p2_id": opponent.id,
                "message": message,
                "expires_at": datetime.now() + timedelta(minutes=5),
            }
            embed.description = (
                f"⚔️ Battle accepted! Both players, add your 3 {settings.plural_collectible_name}!\n\n"
                f"Use `/battle best` to auto-fill or `/battle add` for specific cards.\n"
                f"⏰ **You have 5 minutes to add your cards!**"
            )
            embed.color = discord.Color.green()
            for item in view.children:
                item.disabled = True
            await button_interaction.response.edit_message(embed=embed, view=view)

        async def decline_callback(button_interaction: discord.Interaction):
            if button_interaction.user.id != opponent.id:
                await button_interaction.response.send_message("❌ Only the challenged player can decline!", ephemeral=True)
                return
            embed.description = f"❌ {opponent.mention} declined the battle challenge."
            embed.color = discord.Color.red()
            for item in view.children:
                item.disabled = True
            await button_interaction.response.edit_message(embed=embed, view=view)

        accept_button = discord.ui.Button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
        accept_button.callback = accept_callback
        decline_button = discord.ui.Button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
        decline_button.callback = decline_callback
        view.add_item(accept_button)
        view.add_item(decline_button)

        await interaction.response.send_message(f"{opponent.mention}, you've been challenged!", embed=embed, view=view)

    @app_commands.command()
    async def cancel(self, interaction: discord.Interaction):
        """
        Cancel the current active battle setup in this server.
        Only the challenger or the challenged player can cancel.
        """
        check_expired_battles()

        if interaction.guild_id not in active_battles:
            await interaction.response.send_message(
                "❌ There's no active battle to cancel!", ephemeral=True
            )
            return

        battle_data = active_battles[interaction.guild_id]

        # Only participants can cancel
        if interaction.user.id not in (battle_data["p1_id"], battle_data["p2_id"]):
            await interaction.response.send_message(
                "❌ Only the players in this battle can cancel it!", ephemeral=True
            )
            return

        # Update the public battle message to show it was cancelled
        message = battle_data.get("message")
        if message:
            try:
                cancelled_embed = discord.Embed(
                    title="❌ Battle Cancelled",
                    description=f"The battle was cancelled by {interaction.user.mention}.",
                    color=discord.Color.red(),
                )
                await message.edit(embed=cancelled_embed, view=None)
            except Exception as e:
                log.error(f"Failed to edit battle message on cancel: {e}")

        del active_battles[interaction.guild_id]

        await interaction.response.send_message(
            "✅ Battle cancelled successfully.", ephemeral=True
        )
        log.info(f"Battle in guild {interaction.guild_id} cancelled by {interaction.user.id}")

    @app_commands.command()
    async def redeem(self, interaction: discord.Interaction):
        """
        Redeem your battle win rewards
        """
        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        wins = player.extra_data.get("battle_wins", 0)
        rewards_available = wins // 3
        rewards_claimed = player.extra_data.get("battle_rewards_claimed", 0)

        if rewards_claimed >= rewards_available:
            wins_needed = 3 - (wins % 3)
            await interaction.response.send_message(
                f"❌ You don't have any rewards to claim!\n"
                f"Win {wins_needed} more battle(s) to earn your next reward.",
                ephemeral=True,
            )
            return

        all_balls = [ball async for ball in Ball.objects.filter(rarity__gt=0, enabled=True)]
        if not all_balls:
            await interaction.response.send_message("❌ No balls available for rewards. Contact an admin.", ephemeral=True)
            return

        sorted_rarities = sorted(ball.rarity for ball in all_balls)
        cutoff_index = int(len(sorted_rarities) * 0.55)
        max_rarity = sorted_rarities[cutoff_index] if cutoff_index < len(sorted_rarities) else sorted_rarities[-1]

        eligible_balls = [ball for ball in all_balls if ball.rarity <= max_rarity]
        if not eligible_balls:
            await interaction.response.send_message("❌ No eligible balls available. Contact an admin.", ephemeral=True)
            return

        random_ball = random.choice(eligible_balls)

        ball_instance = await BallInstance.objects.acreate(
            ball=random_ball,
            player=player,
            attack_bonus=random.randint(-settings.max_attack_bonus, settings.max_attack_bonus),
            health_bonus=random.randint(-settings.max_health_bonus, settings.max_health_bonus),
        )

        player.extra_data["battle_rewards_claimed"] = rewards_claimed + 1
        await player.asave()

        remaining_rewards = rewards_available - (rewards_claimed + 1)

        embed = discord.Embed(
            title="🎁 Battle Reward Claimed!",
            description="Congratulations! You received a rare card:",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Card", value=f"**{random_ball.country}**", inline=False)
        embed.add_field(
            name="Stats",
            value=f"⚔️ ATK: {ball_instance.attack_bonus:+}%\n💚 HP: {ball_instance.health_bonus:+}%",
            inline=True,
        )
        embed.add_field(name="Rarity", value=f"Top 55% rarest (rarity: {random_ball.rarity:.2%})", inline=True)

        if remaining_rewards > 0:
            embed.add_field(name="Remaining Rewards", value=f"🎁 {remaining_rewards} reward(s) left to claim!", inline=False)
        else:
            wins_to_next = 3 - (wins % 3)
            embed.add_field(name="Next Reward", value=f"Win {wins_to_next} more battle(s) to earn another reward!", inline=False)

        await interaction.response.send_message(embed=embed)
        log.info(f"Player {player.discord_id} redeemed battle reward: {random_ball.country}")

    @app_commands.command()
    async def best(self, interaction: discord.Interaction):
        """
        Automatically add your 3 strongest cards to the battle
        """
        check_expired_battles()

        if interaction.guild_id not in active_battles:
            await interaction.response.send_message("❌ There's no active battle setup! Use `/battle challenge` first.", ephemeral=True)
            return

        battle_data = active_battles[interaction.guild_id]

        if "expires_at" in battle_data and datetime.now() > battle_data["expires_at"]:
            await interaction.response.send_message("❌ This battle has expired! Start a new one with `/battle challenge`.", ephemeral=True)
            del active_battles[interaction.guild_id]
            return

        battle = battle_data["battle"]

        if interaction.user.id not in (battle_data["p1_id"], battle_data["p2_id"]):
            await interaction.response.send_message("❌ You're not part of this battle!", ephemeral=True)
            return

        is_p1 = interaction.user.id == battle_data["p1_id"]
        current_balls = battle.p1_balls if is_p1 else battle.p2_balls

        if len(current_balls) >= 3:
            await interaction.response.send_message("❌ You already have 3 cards selected!", ephemeral=True)
            return

        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)

        all_balls = [b async for b in BallInstance.objects.filter(player=player, deleted=False).select_related("ball")]
        sorted_balls = sorted(all_balls, key=lambda b: b.attack + b.health, reverse=True)[:3]

        if len(sorted_balls) < 3:
            await interaction.response.send_message(
                f"❌ You need at least 3 {settings.plural_collectible_name} to battle!", ephemeral=True
            )
            return

        for ball_inst in sorted_balls:
            battle_ball = BattleBall(
                name=ball_inst.ball.country,
                owner=interaction.user.name,
                health=ball_inst.health,
                attack=ball_inst.attack,
                max_health=ball_inst.health,
                emoji="",
            )
            current_balls.append(battle_ball)

        try:
            await self._update_battle_setup_message(interaction, battle_data)
        except Exception as e:
            log.error(f"Failed to update battle setup message: {e}")

        await interaction.response.send_message(
            f"✅ Added your 3 strongest {settings.plural_collectible_name}!", ephemeral=True
        )

    @app_commands.command()
    async def add(self, interaction: discord.Interaction):
        """
        Manage your battle team - add or remove cards interactively
        """
        check_expired_battles()

        if interaction.guild_id not in active_battles:
            await interaction.response.send_message("❌ There's no active battle setup! Use `/battle challenge` first.", ephemeral=True)
            return

        battle_data = active_battles[interaction.guild_id]

        if "expires_at" in battle_data and datetime.now() > battle_data["expires_at"]:
            await interaction.response.send_message("❌ This battle has expired! Start a new one with `/battle challenge`.", ephemeral=True)
            del active_battles[interaction.guild_id]
            return

        battle = battle_data["battle"]

        if interaction.user.id not in (battle_data["p1_id"], battle_data["p2_id"]):
            await interaction.response.send_message("❌ You're not part of this battle!", ephemeral=True)
            return

        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        user_balls = [b async for b in BallInstance.objects.filter(player=player, deleted=False).select_related("ball")]

        if not user_balls:
            await interaction.response.send_message("❌ You don't have any balls to add to your team!", ephemeral=True)
            return

        is_p1 = interaction.user.id == battle_data["p1_id"]
        # FIX: Pass self (the cog) so BattleTeamBuilder can call _update_battle_setup_message
        view = BattleTeamBuilder(battle_data, is_p1, user_balls, interaction.user, cog=self)
        embed = view.create_embed()

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command()
    async def remove(self, interaction: discord.Interaction):
        """
        Manage your battle team - same as /battle add
        """
        await self.add.callback(self, interaction)

    async def _update_battle_setup_message(self, interaction: discord.Interaction, battle_data: dict):
        battle = battle_data["battle"]
        message = battle_data.get("message")
        if message is None:
            return

        p1 = await self.bot.fetch_user(battle_data["p1_id"])
        p2 = await self.bot.fetch_user(battle_data["p2_id"])

        p1_count = len(battle.p1_balls)
        p2_count = len(battle.p2_balls)

        time_remaining = ""
        if "expires_at" in battle_data:
            remaining = battle_data["expires_at"] - datetime.now()
            if remaining.total_seconds() > 0:
                minutes = int(remaining.total_seconds() / 60)
                seconds = int(remaining.total_seconds() % 60)
                time_remaining = f"\n⏰ **Time remaining: {minutes}m {seconds}s**"

        embed = discord.Embed(
            title="⚔️ Battle Setup",
            description=f"Both players are selecting their teams!\nClick Ready when you have 3 cards.{time_remaining}",
            color=discord.Color.gold(),
        )

        p1_team_text = "\n".join([f"• {ball.name} (ATK: {ball.attack}, HP: {ball.health})" for ball in battle.p1_balls]) or "Empty"
        p2_team_text = "\n".join([f"• {ball.name} (ATK: {ball.attack}, HP: {ball.health})" for ball in battle.p2_balls]) or "Empty"

        embed.add_field(
            name=f"{p1.name}'s Team ({p1_count}/3)" + (" ✅" if p1_count == 3 and battle.p1_ready else ""),
            value=p1_team_text[:1024],
            inline=True,
        )
        embed.add_field(
            name=f"{p2.name}'s Team ({p2_count}/3)" + (" ✅" if p2_count == 3 and battle.p2_ready else ""),
            value=p2_team_text[:1024],
            inline=True,
        )

        if p1_count == 3 and p2_count == 3:
            view = discord.ui.View(timeout=120)

            async def ready_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id == battle_data["p1_id"]:
                    battle.p1_ready = True
                elif button_interaction.user.id == battle_data["p2_id"]:
                    battle.p2_ready = True
                else:
                    await button_interaction.response.send_message("❌ You're not part of this battle!", ephemeral=True)
                    return

                if battle.p1_ready and battle.p2_ready:
                    await self._start_interactive_battle(button_interaction, battle_data)
                else:
                    await button_interaction.response.send_message("✅ You're ready! Waiting for opponent...", ephemeral=True)
                    await self._update_battle_setup_message(button_interaction, battle_data)

            ready_button = discord.ui.Button(label="Ready!", style=discord.ButtonStyle.success, emoji="✅")
            ready_button.callback = ready_callback
            view.add_item(ready_button)
            await message.edit(embed=embed, view=view)
        else:
            await message.edit(embed=embed, view=None)

    async def _start_interactive_battle(self, interaction: discord.Interaction, battle_data: dict):
        battle = battle_data["battle"]
        if "expires_at" in battle_data:
            del battle_data["expires_at"]

        set_cooldown(battle_data["p1_id"], hours=1)
        set_cooldown(battle_data["p2_id"], hours=1)

        embed = create_battle_embed(battle, title="⚔️ Battle Started!")
        embed.description += "\n\n**Both players, select your first move!**"

        await interaction.response.edit_message(embed=embed, view=None)
        await self._battle_turn_loop(interaction, battle_data)

    async def _battle_turn_loop(self, interaction: discord.Interaction, battle_data: dict):
        battle = battle_data["battle"]
        channel = interaction.channel

        while not battle.is_battle_over():
            embed = create_battle_embed(battle)
            embed.description += f"\n\n**Turn {battle.current_turn + 1} - Select your moves!**"
            message = await channel.send(embed=embed)

            p1_view = BattleMoveView(battle, battle.p1_name)
            p2_view = BattleMoveView(battle, battle.p2_name)

            p1_user = await self.bot.fetch_user(battle_data["p1_id"])
            p2_user = await self.bot.fetch_user(battle_data["p2_id"])

            try:
                await message.edit(embed=embed, view=None)
                p1_msg = await channel.send(f"{p1_user.mention}, select your move!", view=p1_view)
                await p1_view.wait()
                await p1_msg.delete()
                if not p1_view.selected_move:
                    p1_view.selected_move = random.choice(list(MOVES.keys()))

                p2_msg = await channel.send(f"{p2_user.mention}, select your move!", view=p2_view)
                await p2_view.wait()
                await p2_msg.delete()
                if not p2_view.selected_move:
                    p2_view.selected_move = random.choice(list(MOVES.keys()))

            except Exception as e:
                log.error(f"Error in battle turn: {e}")
                await channel.send("❌ An error occurred in the battle. Battle cancelled.")
                del active_battles[interaction.guild_id]
                return

            p1_action = TurnAction(battle.p1_name, 0, p1_view.selected_move)
            p2_action = TurnAction(battle.p2_name, 0, p2_view.selected_move)
            turn_result = battle.execute_turn(p1_action, p2_action)

            result_embed = create_battle_embed(battle, title=f"⚔️ Turn {battle.current_turn} Results")
            result_text = ""
            for event in turn_result["events"]:
                result_text += event.get("message", "") + "\n"
            result_embed.add_field(
                name="📝 What Happened",
                value=result_text[:1024] or "Nothing happened",
                inline=False,
            )
            await channel.send(embed=result_embed)
            await asyncio.sleep(3)

        await self._end_battle(channel, battle_data)

    async def _end_battle(self, channel, battle_data: dict):
        battle = battle_data["battle"]
        winner_name = battle.get_winner()

        if winner_name == battle.p1_name:
            winner_id = battle_data["p1_id"]
            loser_id = battle_data["p2_id"]
        elif winner_name == battle.p2_name:
            winner_id = battle_data["p2_id"]
            loser_id = battle_data["p1_id"]
        else:
            winner_id = None
            loser_id = None

        if winner_id:
            winner_player, _ = await Player.objects.aget_or_create(discord_id=winner_id)
            loser_player, _ = await Player.objects.aget_or_create(discord_id=loser_id)

            winner_player.extra_data["battle_wins"] = winner_player.extra_data.get("battle_wins", 0) + 1
            winner_player.extra_data["last_battle_result"] = {
                "won": True,
                "opponent": battle.p2_name if winner_id == battle_data["p1_id"] else battle.p1_name,
            }
            winner_player.extra_data["burn_points"] = winner_player.extra_data.get("burn_points", 0) + 100
            await winner_player.asave()

            loser_player.extra_data["battle_losses"] = loser_player.extra_data.get("battle_losses", 0) + 1
            loser_player.extra_data["last_battle_result"] = {
                "won": False,
                "opponent": battle.p1_name if loser_id == battle_data["p2_id"] else battle.p2_name,
            }
            loser_player.extra_data["burn_points"] = loser_player.extra_data.get("burn_points", 0) + 10
            await loser_player.asave()

        embed = discord.Embed(
            title="🏆 Battle Complete!",
            description=f"**Winner: {winner_name}!**" if winner_name != "Draw" else "**It's a draw!**",
            color=discord.Color.gold() if winner_name != "Draw" else discord.Color.greyple(),
        )
        embed.add_field(
            name="📊 Battle Stats",
            value=f"**Total Turns:** {battle.current_turn}\n**Duration:** {len(battle.turn_history)} exchanges",
            inline=False,
        )

        if winner_id:
            loser_name = battle.p2_name if winner_name == battle.p1_name else battle.p1_name
            embed.add_field(
                name="🔥 Burn Points Earned",
                value=f"**{winner_name}:** +100 pts\n**{loser_name}:** +10 pts",
                inline=False,
            )
            winner_player, _ = await Player.objects.aget_or_create(discord_id=winner_id)
            wins = winner_player.extra_data.get("battle_wins", 0)
            rewards_available = wins // 3
            rewards_claimed = winner_player.extra_data.get("battle_rewards_claimed", 0)
            unclaimed = rewards_available - rewards_claimed
            wins_to_reward = 3 - (wins % 3)

            reward_text = f"{wins % 3}/3 wins"
            if unclaimed > 0:
                reward_text += f"\n🎁 **{unclaimed} reward(s) ready to claim!** Use `/battle redeem`"
            else:
                reward_text += f"\n{wins_to_reward} win(s) until next reward!"

            embed.add_field(name="🎁 Reward Progress", value=reward_text, inline=False)

        await channel.send(embed=embed)
        del active_battles[interaction.guild_id]
