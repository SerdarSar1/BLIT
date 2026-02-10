# client.py
import socket
import threading
import time
import copy
import pygame as pg
from shared_protocol import encode, decode

SERVER_IP = input("Enter server IP: ").strip()
SERVER_PORT = 5000
player_id = int(input("Player number (1 or 2): ").strip())

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setblocking(False)
sock.sendto(encode({"join": player_id}), (SERVER_IP, SERVER_PORT))

# Networking thread shares
state_lock = threading.Lock()
inputs_lock = threading.Lock()
latest_inputs = {"left":0,"right":0,"jump":0,"attack":0,"special":0,"block":0}

pg.init()
# Desired client render framerate (increase for smoother rendering)
CLIENT_FPS = 120

# Use double buffering / hardware surface where available for better performance
display_flags = pg.RESIZABLE | pg.DOUBLEBUF | pg.HWSURFACE
try:
    screen = pg.display.set_mode((800, 500), display_flags, vsync=0)
except TypeError:
    # Some pygame builds don't support vsync arg
    screen = pg.display.set_mode((800, 500), display_flags)
pg.display.set_caption(f"Fighter Client - Player {player_id}")
clock = pg.time.Clock()

# Diagnostics / safety
MAX_RECV_PER_FRAME = 50
CLIENT_HEARTBEAT_MS = 2000
DEBUG_CLIENT = True

# Latest state from server
state = {
    "p1": {"x":100,"y":350,"w":40,"h":80,"hp":100,"hpMax":100,"hpDisp":100,
           "special":0,"specialMax":300,"block":100,"blockMax":100,
           "facing":"right","blocking":False,"attacking":False,"attackType":"normal","stunned":False,"hitbox":None},
    "p2": {"x":500,"y":350,"w":40,"h":80,"hp":100,"hpMax":100,"hpDisp":100,
           "special":0,"specialMax":300,"block":100,"blockMax":100,
           "facing":"left","blocking":False,"attacking":False,"attackType":"normal","stunned":False,"hitbox":None},
    "fireballs": [],
    "ko": False,
    "width": 800, "height": 500
}

def get_inputs(keys, pid: int) -> dict:
    """Use your original control scheme depending on player id."""
    if pid == 1:
        return {
            "left":   int(keys[pg.K_a]),
            "right":  int(keys[pg.K_d]),
            "jump":   int(keys[pg.K_w]),
            "attack": int(keys[pg.K_e]),
            "special":int(keys[pg.K_r]),
            "block":  int(keys[pg.K_t]),
        }
    else:
        return {
            "left":   int(keys[pg.K_LEFT]),
            "right":  int(keys[pg.K_RIGHT]),
            "jump":   int(keys[pg.K_UP]),
            "attack": int(keys[pg.K_KP1]),
            "special":int(keys[pg.K_KP2]),
            "block":  int(keys[pg.K_KP3]),
        }

def draw_block_shield(surface, player_dict):
    if player_dict["blocking"]:
        w, h = player_dict["w"], player_dict["h"]
        shield_w = int(w * 2.1)
        shield_h = int(h * 1.3)

        shield_surface = pg.Surface((shield_w, shield_h), pg.SRCALPHA)
        pg.draw.ellipse(shield_surface, (50, 50, 255, 150), shield_surface.get_rect())

        rect = shield_surface.get_rect(center=(player_dict["x"] + w//2, player_dict["y"] + h//2))
        surface.blit(shield_surface, rect)

def draw_bars(surface, width, height, p1, p2):
    bar_height = 0.05 * height
    seg_width = width * 0.03
    special_segments = 3
    block_bar_height = bar_height / 2
    spacing = 5

    # P1 HP (chip + real)
    pg.draw.rect(surface, (60, 60, 60), (0, 0, width * 0.35, bar_height))
    pg.draw.rect(surface, (230, 230, 230), (0, 0, (width*0.35) * (p1["hpDisp"]/p1["hpMax"]), bar_height))
    color_hp1 = (255,0,0) if p1["hp"]/p1["hpMax"] >= 0.3 else (255,80,80)
    pg.draw.rect(surface, color_hp1, (0, 0, (width*0.35) * (p1["hp"]/p1["hpMax"]), bar_height))

    # P1 Special
    bar_y_special = bar_height + spacing
    special_bar_width = seg_width * special_segments
    bar_x = 0
    pg.draw.rect(surface, (50,0,50), (bar_x, bar_y_special, special_bar_width, bar_height))
    filled_width = special_bar_width * (p1["special"]/p1["specialMax"])
    pg.draw.rect(surface, (255,0,0), (bar_x, bar_y_special, filled_width, bar_height))
    for j in range(1, special_segments):
        line_x = bar_x + j * seg_width
        pg.draw.line(surface, (255,255,255), (line_x, bar_y_special), (line_x, bar_y_special+bar_height), 2)

    # P1 Block stamina
    bar_y_block = bar_y_special + bar_height + spacing
    block_bar_width = seg_width * 3
    bar_x = 0
    pg.draw.rect(surface, (20,20,70), (bar_x, bar_y_block, block_bar_width, block_bar_height))
    filled_width = block_bar_width * (p1["block"]/p1["blockMax"])
    pg.draw.rect(surface, (0,200,255), (bar_x, bar_y_block, filled_width, block_bar_height))

    # P2 HP (chip + real)
    bar_width_p2 = width * 0.35
    pg.draw.rect(surface, (60,60,60), (width - bar_width_p2, 0, bar_width_p2, bar_height))
    chip_width = bar_width_p2 * (p2["hpDisp"]/p2["hpMax"])
    pg.draw.rect(surface, (230,230,230), (width - chip_width, 0, chip_width, bar_height))
    real_width = bar_width_p2 * (p2["hp"]/p2["hpMax"])
    color_hp2 = (0,0,255) if p2["hp"]/p2["hpMax"] >= 0.3 else (255,80,80)
    pg.draw.rect(surface, color_hp2, (width - real_width, 0, real_width, bar_height))

    # P2 Special
    bar_y_special = bar_height + spacing
    special_bar_width = seg_width * special_segments
    bar_x = width - special_bar_width
    pg.draw.rect(surface, (50,0,50), (bar_x, bar_y_special, special_bar_width, bar_height))
    filled_width = special_bar_width * (p2["special"]/p2["specialMax"])
    pg.draw.rect(surface, (0,0,255), (bar_x + special_bar_width - filled_width, bar_y_special, filled_width, bar_height))
    for j in range(1, special_segments):
        line_x = bar_x + j * seg_width
        pg.draw.line(surface, (255,255,255), (line_x, bar_y_special), (line_x, bar_y_special+bar_height), 2)

    # P2 Block stamina
    bar_y_block = bar_y_special + bar_height + spacing
    block_bar_width = seg_width * 3
    bar_x = width - block_bar_width
    pg.draw.rect(surface, (20,20,70), (bar_x, bar_y_block, block_bar_width, block_bar_height))
    filled_width = block_bar_width * (p2["block"]/p2["blockMax"])
    pg.draw.rect(surface, (0,200,255), (bar_x + block_bar_width - filled_width, bar_y_block, filled_width, block_bar_height))

def draw_state(surface, s):
    # Size from server (we'll just render with current window size)
    w, h = surface.get_size()
    p1, p2 = s["p1"], s["p2"]

    surface.fill((30, 30, 30))

    # Players
    pg.draw.rect(surface, (255,0,0), (p1["x"], p1["y"], p1["w"], p1["h"]))
    pg.draw.rect(surface, (0,0,255), (p2["x"], p2["y"], p2["w"], p2["h"]))

    # Shields
    draw_block_shield(surface, p1)
    draw_block_shield(surface, p2)

    # Hitboxes
    if p1["hitbox"]:
        hb = p1["hitbox"]
        pg.draw.rect(surface, (255,0,0), pg.Rect(hb["x"], hb["y"], hb["w"], hb["h"]), 2)
    if p2["hitbox"]:
        hb = p2["hitbox"]
        pg.draw.rect(surface, (0,0,255), pg.Rect(hb["x"], hb["y"], hb["w"], hb["h"]), 2)

    # Fireballs
    for f in s["fireballs"]:
        pg.draw.circle(surface, (255,165,0), (int(f["x"]), int(f["y"])), 10)

    # UI bars
    draw_bars(surface, w, h, p1, p2)

    # KO overlay
    if s["ko"]:
        font = pg.font.SysFont("Arial", int(0.18*h), bold=True)
        text = font.render("KO", True, (255,255,255))
        rect = text.get_rect(center=(w//2, int(0.3*h)))
        surface.blit(text, rect)

        font2 = pg.font.SysFont("Arial", int(0.06*h), bold=True)
        winner_str = "WINNER PLAYER 2" if p1["hp"] <= 0 else "WINNER PLAYER 1"
        winner = font2.render(winner_str, True, (255,255,0))
        surface.blit(winner, winner.get_rect(center=(w//2, int(0.45*h))))

def send_inputs_now(inputs: dict):
    try:
        sock.sendto(encode({"player": player_id, "inputs": inputs}), (SERVER_IP, SERVER_PORT))
    except Exception:
        pass

def network_thread_func():
    """Runs in background: periodically sends latest inputs and receives server state."""
    global state
    TICK = 1.0 / 60.0
    while True:
        start = time.time()
        # send latest inputs
        with inputs_lock:
            to_send = dict(latest_inputs)
        send_inputs_now(to_send)

        # receive up to a few packets
        try:
            for _ in range(10):
                try:
                    data, addr = sock.recvfrom(8192)
                except BlockingIOError:
                    break
                s = decode(data)
                if s:
                    with state_lock:
                        state = s
        except Exception:
            pass

        elapsed = time.time() - start
        sleep_for = TICK - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)

# Start networking thread
nt = threading.Thread(target=network_thread_func, daemon=True)
nt.start()

running = True
last_heartbeat = 0
while running:
    dt = clock.tick(CLIENT_FPS)
    now_ms = pg.time.get_ticks()
    if DEBUG_CLIENT and now_ms - last_heartbeat > CLIENT_HEARTBEAT_MS:
        print("[CLIENT] main loop alive — draining up to", MAX_RECV_PER_FRAME, "pkts/frame")
        last_heartbeat = now_ms

    # Process events first (keeps window responsive)
    for event in pg.event.get():
        if event.type == pg.QUIT:
            running = False
        elif event.type == pg.VIDEORESIZE:
            # Recreate surface to the new size so OS window stays responsive
            screen = pg.display.set_mode((event.w, event.h), display_flags)
            # Recreate fonts sized for the new window
            w, h = screen.get_size()
            try:
                font_ko_large = pg.font.SysFont("Arial", int(0.18 * h), bold=True)
                font_ko_small = pg.font.SysFont("Arial", int(0.06 * h), bold=True)
            except Exception:
                font_ko_large = None
                font_ko_small = None
        elif event.type == pg.KEYDOWN:
            # Ask server to reset after KO
            if state.get("ko") and event.key == pg.K_RETURN:
                try:
                    sock.sendto(encode({"replay": 1}), (SERVER_IP, SERVER_PORT))
                except Exception:
                    pass

    # Ensure Pygame's internal state is up-to-date for key.get_pressed()
    pg.event.pump()

    # Update latest_inputs for the network thread to send
    keys = pg.key.get_pressed()
    with inputs_lock:
        latest_inputs = get_inputs(keys, player_id)

    # Use thread-updated state (copy under lock to avoid races)
    with state_lock:
        s_copy = copy.deepcopy(state)

    # Draw
    draw_state(screen, s_copy)
    pg.display.flip()

pg.quit()