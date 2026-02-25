# server.py
import os
import socket
import time
import threading
import random as r
import math
import pygame as pg
from shared_protocol import encode, decode

# ---------------- Headless setup (server has no window/audio) ----------------
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

pg.init()
clock = pg.time.Clock()

# ---------------- Network config ----------------
TICK_RATE = 60
SERVER_PORT = 5000
MAX_PACKET = 4096

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", SERVER_PORT))
sock.setblocking(False)
print(f"[SERVER] UDP listening on 0.0.0.0:{SERVER_PORT}")

# Diagnostics
DEBUG_SERVER = True
SERVER_HEARTBEAT_MS = 1000
_last_server_hb = 0

# Last seen addresses (player 1 and 2)
player_addresses = {}
# Latest inputs received (buttons are 0/1)
player_inputs = {
    1: {"left":0,"right":0,"jump":0,"attack":0,"block":0,"special":0},
    2: {"left":0,"right":0,"jump":0,"attack":0,"block":0,"special":0},
}

# ---------------- Game constants & globals ----------------
BG_COLOR = (30, 30, 30)
PLAYER1_COLOR = (255, 0, 0)
PLAYER2_COLOR = (0, 0, 255)
FIREBALL_COLOR = (255, 165, 0)  # Orange
BLOCK_SHIELD_COLOR = (50, 50, 255, 150)

initial_width, initial_height = 800, 500
width, height = initial_width, initial_height

# Placeholder fireball image (server doesn't need real assets)
fireball_image = pg.Surface((30, 30), pg.SRCALPHA)
pg.draw.circle(fireball_image, FIREBALL_COLOR, (15, 15), 14)

# Sprite groups
all_sprites = pg.sprite.Group()
fireballs = pg.sprite.Group()

ko_triggered = False
ko_y = -200

# ---------------- Classes ----------------
class Fireball(pg.sprite.Sprite):
    def __init__(self, start_x, start_y, direction, color, screen_width):
        super().__init__()
        self.direction = direction  # "left" or "right"
        self.speed = 500
        self.damage = 25
        self.screen_width = screen_width
        self.owner = None

        self.original_image = fireball_image
        self.image = pg.transform.scale(self.original_image, (30, 30))
        if self.direction == "left":
            self.image = pg.transform.flip(self.image, True, False)
        self.rect = self.image.get_rect(center=(start_x, start_y))

    def update(self, dt, *args):
        if self.direction == "right":
            self.rect.x += self.speed * dt
        else:
            self.rect.x -= self.speed * dt

        if self.rect.right < 0 or self.rect.left > self.screen_width:
            self.kill()

class Player(pg.sprite.Sprite):
    def __init__(self, x_ratio, color, controls, typeofspecial):
        super().__init__()
        self.color = color
        self.x_ratio = x_ratio
        self.width_ratio = 0.06
        self.height_ratio = 0.2

        self.image = pg.Surface((50, 100))
        self.image.fill(color)
        self.rect = self.image.get_rect()
        self.controls = controls
        self.speed = 300
        self.gravity = 0
        self.attacking = False
        self.blocking = False
        self.active_hitbox = None
        self.hit_opponents = []
        self.facing = "right" if x_ratio < 0.5 else "left"
        self.health = 100
        self.max_health = 100

        self.typeofspecial = typeofspecial
        self.special_attack = 300
        self.max_special = 300
        self.special_cost = 100
        self.can_special = True
        self.special_cooldown_timer = 0
        self.special_cooldown_time = 0.5

        self.block_stamina = 100
        self.max_block_stamina = 100
        self.block_drain_rate = 100
        self.block_recover_rate = 30

        self.combo_count = 0
        self.last_hit_time = 0
        self.combo_reset_time = 0.8

        self.stunned = False
        self.stun_timer = 0
        self.knockback_velocity = 0

        self.shockwave_damage = 30
        self.shockwave_base_damage = 15
        self.shockwave_height_multiplier = 0.08

        self.attack_type = "normal"

        self.max_jumps = 2
        self.jump_count = 0
        self.jump_pressed = False

        self.dashing = False
        self.dash_speed = 3000
        self.dash_time = 0.08
        self.dash_timer = 0
        self.dash_trail = []   # trail visuals not used on server
        self.dash_trail_max = 10

        self.max_dash_charges = 3
        self.dash_charges = 0
        self.dash_hit_this_dash = False

        self.display_health = self.health

        if self.typeofspecial == "joker":
            self.joker_deck = ["fireball", "dash", "heal", "shockwave"]
            r.shuffle(self.joker_deck)
            self.joker_index = 0
            self.joker_current = self.joker_deck[self.joker_index]
        else:
            self.joker_deck = None
            self.joker_index = None
            self.joker_current = None

    def initial_setup(self, screen_width, screen_height):
        w = int(screen_width * self.width_ratio)
        h = int(screen_height * self.height_ratio)
        floor = 0.8 * screen_height
        self.image = pg.Surface((w, h))
        self.image.fill(self.color)
        self.rect = self.image.get_rect(midbottom=(int(screen_width * self.x_ratio), floor))

    def update_rect(self, screen_width, screen_height):
        w = int(screen_width * self.width_ratio)
        h = int(screen_height * self.height_ratio)
        floor = 0.8 * screen_height
        old_midbottom = self.rect.midbottom

        self.image = pg.Surface((w, h))
        self.image.fill(self.color)
        self.rect = self.image.get_rect(midbottom=old_midbottom)

        if self.rect.bottom > floor:
            self.rect.bottom = floor
        if self.rect.left < 0:
            self.rect.left = 0
        if self.rect.right > screen_width:
            self.rect.right = screen_width

    def handle_input(self, dt, screen_height, screen_width):
        # Overridden in NetPlayer; server doesn't read keyboard
        return

    def apply_gravity(self, screen_height, screen_width=None):
        floor = 0.8 * screen_height
        GRAVITY_ACCELERATION_RATIO = 0.002
        GRAVITY_ACCELERATION = screen_height * GRAVITY_ACCELERATION_RATIO
        WALL_SLIDE_SPEED_RATIO = 0.5

        wall_touch = self.is_touching_wall(screen_width) if screen_width else None
        if wall_touch and self.is_airborne(screen_height):
            self.gravity = min(self.gravity, GRAVITY_ACCELERATION / WALL_SLIDE_SPEED_RATIO)

        self.gravity += GRAVITY_ACCELERATION
        self.rect.y += self.gravity

        if self.rect.bottom >= floor:
            self.rect.bottom = floor
            self.gravity = 0
            self.jump_count = 0

        if self.knockback_velocity != 0:
            self.rect.x += self.knockback_velocity
            self.knockback_velocity *= 0.85
            if abs(self.knockback_velocity) < 1:
                self.knockback_velocity = 0

        if self.stunned:
            self.stun_timer -= 1 / 60
            if self.stun_timer <= 0:
                self.stunned = False

    def start_attack(self, screen_height):
        self.attack_type = "normal"
        self.attacking = True
        self.attack_timer = 0.2
        w, h = self.rect.width, self.rect.height
        airborne = self.is_airborne(screen_height)

        # Keep deterministic; use grounded/airborne branches
        if airborne:
            self.active_hitbox = pg.Rect(self.rect.centerx - 0.2*w, self.rect.top - 0.5*h, 0.4*w, 0.5*h)
        else:
            if self.facing == "right":
                self.active_hitbox = pg.Rect(self.rect.right, self.rect.top + 0.2*h, 0.35*w, 0.6*h)
            else:
                self.active_hitbox = pg.Rect(self.rect.left - 0.35*w, self.rect.top + 0.2*h, 0.35*w, 0.6*h)
        self.hit_opponents = []

    def start_special(self, screen_width):
        self.special_attack -= self.special_cost
        self.can_special = False
        self.special_cooldown_timer = self.special_cooldown_time

        if self.typeofspecial == "joker":
            chosen = self.joker_current
        else:
            chosen = self.typeofspecial

        if chosen == "fireball":
            self.attack_type = "fireball"
            start_x = self.rect.centerx
            start_y = self.rect.centery - 10
            fireball = Fireball(start_x, start_y, self.facing, FIREBALL_COLOR, screen_width)
            fireball.owner = self
            all_sprites.add(fireball)
            fireballs.add(fireball)

        elif chosen == "dash":
            self.attack_type = "dash"
            self.dashing = True
            self.dash_timer = self.dash_time
            self.dash_charges = self.max_dash_charges
            self.dash_hit_this_dash = False
            self.hit_opponents = []

        elif chosen == "heal":
            self.health += 20
            if self.health > self.max_health:
                self.health = self.max_health

        elif chosen == "shockwave":
            self.attack_type = "shockwave"
            w, h = self.rect.width, self.rect.height
            shockwave_radius = max(w, h) * 1.85
            self.active_hitbox = pg.Rect(self.rect.centerx - shockwave_radius/2, self.rect.centery - shockwave_radius/2, shockwave_radius, shockwave_radius)
            self.attacking = True
            self.attack_timer = 0.3
            self.hit_opponents = []

        if self.typeofspecial == "joker":
            self.joker_index += 1
            if self.joker_index >= len(self.joker_deck):
                self.joker_index = 0
            self.joker_current = self.joker_deck[self.joker_index]

    def update_attack(self, dt):
        if self.attacking:
            self.attack_timer -= dt
            if self.attack_timer <= 0:
                self.attacking = False
                self.active_hitbox = None
                self.hit_opponents = []
        if not self.can_special:
            self.special_cooldown_timer -= dt
            if self.special_cooldown_timer <= 0:
                self.can_special = True

    def gain_special(self, amount):
        self.special_attack += amount
        if self.special_attack > self.max_special:
            self.special_attack = self.max_special

    def deal_damage(self, amount):
        self.health -= amount
        if self.health < 0:
            self.health = 0
        self.gain_special(amount * 0.5)

    def update(self, dt, screen_width, screen_height):
        # Ensure rect size & floor clamping uses current screen size.
        self.update_rect(screen_width, screen_height)

        # >>> APPLY INPUTS HERE <<<
        # NetPlayer overrides handle_input; base Player's is a no-op, so this is safe.
        self.handle_input(dt, screen_height, screen_width)

        # Dash movement & hitbox
        if self.dashing:
            direction = 1 if self.facing == "right" else -1
            self.rect.x += direction * self.dash_speed * dt
            self.dash_timer -= dt
            self.active_hitbox = pg.Rect(
                min(self.rect.centerx, self.rect.centerx + direction * self.dash_speed * dt),
                self.rect.top,
                self.rect.width * 0.6 + self.dash_speed * dt,
                self.rect.height
            )
            if self.dash_timer <= 0:
                self.dashing = False
                self.active_hitbox = None
                self.dash_hit_this_dash = False
                self.hit_opponents = []
        if not self.dashing and self.attack_type == "dash":
            self.attack_type = "normal"

        self.apply_gravity(screen_height, screen_width)
        self.update_attack(dt)
        self.display_health += (self.health - self.display_health) * 0.12

    def is_airborne(self, screen_height):
        floor = 0.8 * screen_height
        return self.rect.bottom < floor - 5

    def is_touching_wall(self, screen_width):
        if self.rect.left <= 0:
            return "left"
        elif self.rect.right >= screen_width:
            return "right"
        return None

class NetPlayer(Player):
    """Same as Player, but reads inputs from network dict instead of keyboard."""
    def __init__(self, x_ratio, color, player_id, typeofspecial):
        super().__init__(x_ratio, color, controls={}, typeofspecial=typeofspecial)
        self.player_id = player_id
        self.net_inputs = {"left":0,"right":0,"jump":0,"attack":0,"block":0,"special":0}

    def set_inputs(self, inputs: dict):
        self.net_inputs.update({k:int(bool(inputs.get(k,0))) for k in ["left","right","jump","attack","block","special"]})

    def handle_input(self, dt, screen_height, screen_width):
        global ko_triggered
        if ko_triggered or self.stunned:
            return
        inp = self.net_inputs

        if not self.dashing:
            if inp["left"]:
                self.rect.x -= self.speed * dt
                self.facing = "left"
            if inp["right"]:
                self.rect.x += self.speed * dt
                self.facing = "right"

        # Jump / double-jump / wall-jump
        JUMP_VELOCITY = screen_height * (-0.036)
        wall_touch = None
        if self.rect.left <= 0: wall_touch = "left"
        elif self.rect.right >= screen_width: wall_touch = "right"

        if inp["jump"]:
            if self.is_airborne(screen_height) and self.jump_count < self.max_jumps:
                self.gravity = JUMP_VELOCITY
                self.jump_count += 1
            elif not self.is_airborne(screen_height) and self.jump_count == 0:
                self.gravity = JUMP_VELOCITY
                self.jump_count = 1
            elif wall_touch:
                self.gravity = JUMP_VELOCITY
                if wall_touch == "left":
                    self.rect.x += int(0.1 * screen_width); self.facing = "right"
                else:
                    self.rect.x -= int(0.1 * screen_width); self.facing = "left"

        # Blocking
        if inp["block"]:
            self.blocking = self.block_stamina > 0
        else:
            self.blocking = False

        if self.blocking:
            self.block_stamina -= self.block_drain_rate * dt
            if self.block_stamina < 0:
                self.block_stamina = 0
                self.blocking = False
        else:
            self.block_stamina += self.block_recover_rate * dt
            if self.block_stamina > self.max_block_stamina:
                self.block_stamina = self.max_block_stamina

        # Attack
        if inp["attack"] and not self.attacking and not self.blocking:
            self.start_attack(screen_height)

        # Special
        if inp["special"] and self.special_attack >= self.special_cost and self.can_special and not self.attacking and not self.blocking:
            self.start_special(screen_width)

# ---------------- World setup ----------------
player1 = NetPlayer(0.2, PLAYER1_COLOR, player_id=1, typeofspecial="heal")
player2 = NetPlayer(0.8, PLAYER2_COLOR, player_id=2, typeofspecial="joker")
player1.initial_setup(width, height)
player2.initial_setup(width, height)
players = pg.sprite.Group(player1, player2)
all_sprites.add(player1, player2)

def reset_game():
    global ko_triggered, ko_y
    ko_triggered = False
    ko_y = -200

    player1.health = 100
    player2.health = 100

    player1.special_attack = 0
    player2.special_attack = 0

    player1.block_stamina = player1.max_block_stamina
    player2.block_stamina = player2.max_block_stamina

    player1.rect.midbottom = (int(width * player1.x_ratio), int(0.8 * height))
    player2.rect.midbottom = (int(width * player2.x_ratio), int(0.8 * height))

    player1.gravity = 0
    player2.gravity = 0

    player1.attacking = False
    player2.attacking = False

    player1.active_hitbox = None
    player2.active_hitbox = None

    player1.facing = "right"
    player2.facing = "left"

    player1.stunned = False
    player2.stunned = False
    player1.combo_count = 0
    player2.combo_count = 0
    player1.knockback_velocity = 0
    player2.knockback_velocity = 0

    for f in list(fireballs):
        f.kill()

def handle_attack(attacker: Player, defender: Player):
    now = pg.time.get_ticks() / 1000

    # Dash contact
    if attacker.attack_type == "dash" and attacker.dashing and attacker.active_hitbox:
        if attacker.active_hitbox.colliderect(defender.rect) and defender not in attacker.hit_opponents:
            defender.deal_damage(30)
            defender.stunned = True
            defender.stun_timer = 0.4
            defender.knockback_velocity = 15 if attacker.facing == "right" else -15

            attacker.hit_opponents.append(defender)
            attacker.dash_hit_this_dash = True
        return

    # Normal/shockwave
    if attacker.active_hitbox and attacker.active_hitbox.colliderect(defender.rect) and defender not in attacker.hit_opponents:
        if now - attacker.last_hit_time > attacker.combo_reset_time:
            attacker.combo_count = 0

        attacker.combo_count += 1
        attacker.last_hit_time = now
        attacker.hit_opponents.append(defender)

        damage = 5
        if attacker.attack_type == "shockwave":
            floor = 0.8 * height
            height_above_ground = max(0, floor - attacker.rect.bottom)
            damage = attacker.shockwave_base_damage + height_above_ground * attacker.shockwave_height_multiplier

        if not defender.blocking:
            defender.deal_damage(damage)

        attacker.gain_special(damage * 1.5)
        defender.gain_special(damage * 0.5)

        if attacker.combo_count >= 4:
            defender.stunned = True
            defender.stun_timer = 0.7
            defender.knockback_velocity = 18 if attacker.facing == "right" else -18
            attacker.combo_count = 0

def step_game(dt):
    global ko_triggered, ko_y

    # Apply inputs
    player1.set_inputs(player_inputs[1])
    player2.set_inputs(player_inputs[2])

    # Update world
    players.update(dt, width, height)
    fireballs.update(dt)

    # Passive special gain
    player1.gain_special(10 * dt)
    player2.gain_special(10 * dt)

    # Basic attack collisions
    handle_attack(player1, player2)
    handle_attack(player2, player1)

    # Fireball collisions
    for fireball in list(fireballs):
        if fireball.owner != player1 and fireball.rect.colliderect(player1.rect):
            attacker = fireball.owner; defender = player1
            now = pg.time.get_ticks() / 1000

            if now - attacker.last_hit_time > attacker.combo_reset_time:
                attacker.combo_count = 0

            attacker.combo_count += 1
            attacker.last_hit_time = now

            if not defender.blocking:
                defender.deal_damage(fireball.damage)

            attacker.gain_special(fireball.damage * 1.5)
            defender.gain_special(fireball.damage * 0.5)

            if attacker.combo_count >= 4:
                defender.stunned = True
                defender.stun_timer = 0.9
                defender.knockback_velocity = 35 if attacker.facing == "right" else -35
                attacker.combo_count = 0

            fireball.kill()

        elif fireball.owner != player2 and fireball.rect.colliderect(player2.rect):
            attacker = fireball.owner; defender = player2
            now = pg.time.get_ticks() / 1000

            if now - attacker.last_hit_time > attacker.combo_reset_time:
                attacker.combo_count = 0

            attacker.combo_count += 1
            attacker.last_hit_time = now

            if not defender.blocking:
                defender.deal_damage(fireball.damage)

            attacker.gain_special(fireball.damage * 1.5)
            defender.gain_special(fireball.damage * 0.5)

            if attacker.combo_count >= 4:
                defender.stunned = True
                defender.stun_timer = 0.9
                defender.knockback_velocity = 35 if attacker.facing == "right" else -35
                attacker.combo_count = 0

            fireball.kill()

    # KO logic (no drawing on server)
    if not ko_triggered and (player1.health <= 0 or player2.health <= 0):
        ko_triggered = True
        ko_y = -0.4 * height

    if ko_triggered:
        ko_y += 600 * dt
        if ko_y > 0.3 * height:
            ko_y = 0.3 * height

def pack_state() -> dict:
    """Pack minimal full state for clients to render."""
    fb_list = [{"x": f.rect.centerx, "y": f.rect.centery, "dir": f.direction} for f in fireballs]

    def p_to_dict(p: Player):
        hb = None
        if p.active_hitbox:
            rct = p.active_hitbox
            hb = {"x": rct.x, "y": rct.y, "w": rct.width, "h": rct.height}
        return {
            "x": p.rect.x, "y": p.rect.y,
            "w": p.rect.width, "h": p.rect.height,
            "hp": p.health, "hpMax": p.max_health,
            "hpDisp": p.display_health,
            "special": p.special_attack, "specialMax": p.max_special,
            "block": p.block_stamina, "blockMax": p.max_block_stamina,
            "facing": p.facing, "blocking": p.blocking,
            "attacking": p.attacking, "attackType": p.attack_type,
            "stunned": p.stunned,
            "hitbox": hb
        }

    return {
        "p1": p_to_dict(player1),
        "p2": p_to_dict(player2),
        "fireballs": fb_list,
        "ko": ko_triggered,
        "width": width, "height": height
    }

# ---------------- UDP listener (thread) ----------------
def network_listener():
    global player_inputs
    while True:
        try:
            data, addr = sock.recvfrom(MAX_PACKET)
        except BlockingIOError:
            time.sleep(0.001)
            continue

        msg = decode(data)
        if not msg:
            continue

        if "join" in msg:
            pid = int(msg["join"])
            player_addresses[pid] = addr
            print(f"[SERVER] Player {pid} joined from {addr}")

        elif "player" in msg and "inputs" in msg:
            pid = int(msg["player"])
            if pid in player_inputs:
                player_inputs[pid] = {k:int(bool(v)) for k, v in msg["inputs"].items()}
                if DEBUG_SERVER:
                    # lightweight log: occasionally print inputs
                    now_ms = int(time.time() * 1000)
                    if now_ms % 5000 < 50:
                        print(f"[SERVER] recv inputs from {pid}: {player_inputs[pid]}")

        elif msg.get("replay") == 1:
            reset_game()

threading.Thread(target=network_listener, daemon=True).start()

# ---------------- Main server loop ----------------
try:
    while True:
        dt = clock.tick(TICK_RATE) / 1000.0
        step_game(dt)

        state_packet = encode(pack_state())
        # Send state to connected players
        for pid, addr in list(player_addresses.items()):
            try:
                sock.sendto(state_packet, addr)
            except Exception as e:
                print(f"[SERVER] sendto error to {addr}: {e}")

        # Periodic heartbeat for diagnostics
        if DEBUG_SERVER:
            now_ms = int(time.time() * 1000)
            if now_ms - _last_server_hb > SERVER_HEARTBEAT_MS:
                _last_server_hb = now_ms
                print(f"[SERVER] heartbeat - players={list(player_addresses.keys())} inputs_p1={player_inputs.get(1)} inputs_p2={player_inputs.get(2)}")

except KeyboardInterrupt:
    print("\n[SERVER] Shutting down.")
finally:
    sock.close()

    pg.quit()
