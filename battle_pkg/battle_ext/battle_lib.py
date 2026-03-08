from dataclasses import dataclass, field
import random
from enum import Enum
from typing import Optional


class MoveType(Enum):
    """Types of moves available in battle"""
    ATTACK = "attack"
    HEAVY_ATTACK = "heavy_attack"
    DEFEND = "defend"
    HEAL = "heal"


@dataclass
class BattleMove:
    """Represents a move that can be used in battle"""
    name: str
    move_type: MoveType
    description: str
    emoji: str

    def execute(self, attacker: "BattleBall", defender: "BattleBall") -> dict:
        result = {
            "success": False,
            "damage": 0,
            "heal": 0,
            "message": "",
            "crit": False,
            "miss": False,
        }

        if random.random() < 0.1:
            result["miss"] = True
            result["message"] = f"{attacker.name} missed!"
            return result

        if self.move_type == MoveType.ATTACK:
            damage = int(attacker.attack * random.uniform(0.8, 1.2))
            if random.random() < 0.1:
                damage = int(damage * 1.5)
                result["crit"] = True
            defender.health -= damage
            result["damage"] = damage
            result["success"] = True
            if result["crit"]:
                result["message"] = f"💥 Critical hit! {attacker.name} dealt {damage} damage to {defender.name}!"
            else:
                result["message"] = f"{attacker.name} dealt {damage} damage to {defender.name}!"

        elif self.move_type == MoveType.HEAVY_ATTACK:
            if random.random() < 0.3:
                result["miss"] = True
                result["message"] = f"{attacker.name}'s heavy attack missed!"
                return result
            damage = int(attacker.attack * random.uniform(1.2, 1.8))
            defender.health -= damage
            result["damage"] = damage
            result["success"] = True
            result["message"] = f"💪 {attacker.name} landed a heavy attack! {damage} damage to {defender.name}!"

        elif self.move_type == MoveType.DEFEND:
            attacker.defending = True
            result["success"] = True
            result["message"] = f"🛡️ {attacker.name} is defending!"

        elif self.move_type == MoveType.HEAL:
            heal_amount = int(attacker.max_health * 0.2)
            old_health = attacker.health
            attacker.health = min(attacker.health + heal_amount, attacker.max_health)
            actual_heal = attacker.health - old_health
            result["heal"] = actual_heal
            result["success"] = True
            result["message"] = f"💚 {attacker.name} healed for {actual_heal} HP!"

        if defender.health <= 0:
            defender.health = 0
            defender.dead = True

        return result


MOVES = {
    "attack": BattleMove("Quick Attack", MoveType.ATTACK, "A standard attack", "⚔️"),
    "heavy": BattleMove("Heavy Strike", MoveType.HEAVY_ATTACK, "Powerful but risky", "💪"),
    "defend": BattleMove("Defend", MoveType.DEFEND, "Brace for impact", "🛡️"),
    "heal": BattleMove("Recover", MoveType.HEAL, "Restore some HP", "💚"),
}


@dataclass
class BattleBall:
    name: str
    owner: str
    health: int
    attack: int
    max_health: int = 0
    emoji: str = ""
    dead: bool = False
    defending: bool = False

    def __post_init__(self):
        if self.max_health == 0:
            self.max_health = self.health


@dataclass
class TurnAction:
    player: str
    ball_index: int
    move: str
    target_index: int = 0


@dataclass
class BattleInstance:
    p1_name: str
    p2_name: str
    p1_balls: list[BattleBall] = field(default_factory=list)
    p2_balls: list[BattleBall] = field(default_factory=list)
    p1_active_index: int = 0
    p2_active_index: int = 0
    current_turn: int = 0
    turn_history: list[dict] = field(default_factory=list)
    winner: str = ""
    p1_ready: bool = False
    p2_ready: bool = False

    def get_active_ball(self, player: str) -> Optional[BattleBall]:
        if player == self.p1_name:
            if self.p1_active_index < len(self.p1_balls):
                return self.p1_balls[self.p1_active_index]
        else:
            if self.p2_active_index < len(self.p2_balls):
                return self.p2_balls[self.p2_active_index]
        return None

    def get_next_alive_ball_index(self, player: str) -> Optional[int]:
        balls = self.p1_balls if player == self.p1_name else self.p2_balls
        current_index = self.p1_active_index if player == self.p1_name else self.p2_active_index
        for i in range(current_index + 1, len(balls)):
            if not balls[i].dead:
                return i
        return None

    def switch_to_next_ball(self, player: str) -> bool:
        next_index = self.get_next_alive_ball_index(player)
        if next_index is None:
            return False
        if player == self.p1_name:
            self.p1_active_index = next_index
        else:
            self.p2_active_index = next_index
        return True

    def is_battle_over(self) -> bool:
        p1_alive = any(not ball.dead for ball in self.p1_balls)
        p2_alive = any(not ball.dead for ball in self.p2_balls)
        return not (p1_alive and p2_alive)

    def get_winner(self) -> Optional[str]:
        p1_alive = any(not ball.dead for ball in self.p1_balls)
        p2_alive = any(not ball.dead for ball in self.p2_balls)
        if not p1_alive and not p2_alive:
            return "Draw"
        elif not p1_alive:
            return self.p2_name
        elif not p2_alive:
            return self.p1_name
        return None

    def execute_turn(self, p1_action: TurnAction, p2_action: TurnAction) -> dict:
        self.current_turn += 1
        turn_result = {
            "turn": self.current_turn,
            "events": [],
            "p1_active": self.get_active_ball(self.p1_name),
            "p2_active": self.get_active_ball(self.p2_name),
        }

        p1_ball = self.get_active_ball(self.p1_name)
        p2_ball = self.get_active_ball(self.p2_name)

        if not p1_ball or not p2_ball:
            return turn_result

        if p1_ball.attack > p2_ball.attack:
            first, second = (self.p1_name, p1_action), (self.p2_name, p2_action)
        elif p2_ball.attack > p1_ball.attack:
            first, second = (self.p2_name, p2_action), (self.p1_name, p1_action)
        else:
            if random.random() < 0.5:
                first, second = (self.p1_name, p1_action), (self.p2_name, p2_action)
            else:
                first, second = (self.p2_name, p2_action), (self.p1_name, p1_action)

        result1 = self._execute_single_action(first[0], first[1])
        turn_result["events"].append(result1)

        if self.is_battle_over():
            self.winner = self.get_winner()
            return turn_result

        second_ball = self.get_active_ball(second[0])
        if second_ball and not second_ball.dead:
            result2 = self._execute_single_action(second[0], second[1])
            turn_result["events"].append(result2)

        if self.is_battle_over():
            self.winner = self.get_winner()

        self.turn_history.append(turn_result)
        return turn_result

    def _execute_single_action(self, player: str, action: TurnAction) -> dict:
        attacker = self.get_active_ball(player)
        defender = self.get_active_ball(self.p2_name if player == self.p1_name else self.p1_name)

        if not attacker or not defender:
            return {"message": "Error: Ball not found", "success": False}

        move = MOVES.get(action.move)
        if not move:
            return {"message": "Error: Invalid move", "success": False}

        if defender.defending and move.move_type in (MoveType.ATTACK, MoveType.HEAVY_ATTACK):
            result = move.execute(attacker, defender)
            if result.get("damage"):
                original_damage = result["damage"]
                result["damage"] = int(result["damage"] * 0.5)
                defender.health += original_damage - result["damage"]
                result["message"] += " (Reduced by defend!)"
            defender.defending = False
        else:
            result = move.execute(attacker, defender)
            if defender.defending:
                defender.defending = False

        if defender.dead:
            result["message"] += f" 💀 {defender.name} was knocked out!"
            opponent = self.p2_name if player == self.p1_name else self.p1_name
            switched = self.switch_to_next_ball(opponent)
            if switched:
                next_ball = self.get_active_ball(opponent)
                result["message"] += f"\n🔄 {next_ball.name} enters the battle!"

        return result


def create_battle_from_instances(
    p1_name: str,
    p2_name: str,
    p1_ball_instances: list,
    p2_ball_instances: list,
) -> BattleInstance:
    if len(p1_ball_instances) != 5 or len(p2_ball_instances) != 5:
        raise ValueError("Each player must have exactly 5 balls!")

    p1_balls = [
        BattleBall(
            name=ball.ball.country,
            owner=p1_name,
            health=ball.health,
            attack=ball.attack,
            max_health=ball.health,
            emoji=ball.ball.emoji_id if hasattr(ball.ball, "emoji_id") else "",
        )
        for ball in p1_ball_instances
    ]

    p2_balls = [
        BattleBall(
            name=ball.ball.country,
            owner=p2_name,
            health=ball.health,
            attack=ball.attack,
            max_health=ball.health,
            emoji=ball.ball.emoji_id if hasattr(ball.ball, "emoji_id") else "",
        )
        for ball in p2_ball_instances
    ]

    return BattleInstance(
        p1_name=p1_name,
        p2_name=p2_name,
        p1_balls=p1_balls,
        p2_balls=p2_balls,
    )
