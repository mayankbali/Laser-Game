from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import json
import random
import math
import time
import threading
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'laser_game_secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# Game Constants
WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 700
PLAYER_SIZE = 8
START_ZONE_WIDTH = 100
FINISH_ZONE_WIDTH = 100
FPS = 60

# Projectile Constants
PROJECTILE_SIZE = 6
PROJECTILE_SPEED_MIN = 80
PROJECTILE_SPEED_MAX = 150

# Player Colors (RGB values)
PLAYER_COLORS = [
    [255, 0, 0],    # Red
    [0, 0, 255],    # Blue
    [0, 255, 0],    # Green
    [255, 255, 0],  # Yellow
    [255, 0, 255],  # Purple
    [0, 255, 255],  # Cyan
    [255, 165, 0],  # Orange
    [255, 192, 203] # Pink
]

class Projectile:
    def __init__(self, spawn_side, target_pos=None):
        self.size = PROJECTILE_SIZE
        self.speed = random.uniform(PROJECTILE_SPEED_MIN, PROJECTILE_SPEED_MAX)
        self.spawn_side = spawn_side
        self.alive = True
        self.creation_time = time.time()
        
        # Set spawn position based on side
        if spawn_side == 'left':
            self.pos = [-self.size, random.randint(0, WINDOW_HEIGHT)]
            if target_pos:
                # Aim towards target
                dx = target_pos[0] - self.pos[0]
                dy = target_pos[1] - self.pos[1]
                length = math.sqrt(dx**2 + dy**2)
                self.velocity = [dx/length * self.speed, dy/length * self.speed]
            else:
                # Random direction towards right side
                angle = random.uniform(-math.pi/4, math.pi/4)
                self.velocity = [self.speed * math.cos(angle), self.speed * math.sin(angle)]
                
        elif spawn_side == 'right':
            self.pos = [WINDOW_WIDTH + self.size, random.randint(0, WINDOW_HEIGHT)]
            if target_pos:
                dx = target_pos[0] - self.pos[0]
                dy = target_pos[1] - self.pos[1]
                length = math.sqrt(dx**2 + dy**2)
                self.velocity = [dx/length * self.speed, dy/length * self.speed]
            else:
                angle = random.uniform(3*math.pi/4, 5*math.pi/4)
                self.velocity = [self.speed * math.cos(angle), self.speed * math.sin(angle)]
                
        elif spawn_side == 'top':
            self.pos = [random.randint(0, WINDOW_WIDTH), -self.size]
            if target_pos:
                dx = target_pos[0] - self.pos[0]
                dy = target_pos[1] - self.pos[1]
                length = math.sqrt(dx**2 + dy**2)
                self.velocity = [dx/length * self.speed, dy/length * self.speed]
            else:
                angle = random.uniform(math.pi/4, 3*math.pi/4)
                self.velocity = [self.speed * math.cos(angle), self.speed * math.sin(angle)]
                
        elif spawn_side == 'bottom':
            self.pos = [random.randint(0, WINDOW_WIDTH), WINDOW_HEIGHT + self.size]
            if target_pos:
                dx = target_pos[0] - self.pos[0]
                dy = target_pos[1] - self.pos[1]
                length = math.sqrt(dx**2 + dy**2)
                self.velocity = [dx/length * self.speed, dy/length * self.speed]
            else:
                angle = random.uniform(-3*math.pi/4, -math.pi/4)
                self.velocity = [self.speed * math.cos(angle), self.speed * math.sin(angle)]
    
    def update(self, dt):
        if not self.alive:
            return
            
        # Update position
        self.pos[0] += self.velocity[0] * dt
        self.pos[1] += self.velocity[1] * dt
        
        # Check if projectile is off screen
        if (self.pos[0] < -self.size * 2 or self.pos[0] > WINDOW_WIDTH + self.size * 2 or
            self.pos[1] < -self.size * 2 or self.pos[1] > WINDOW_HEIGHT + self.size * 2):
            self.alive = False
    
    def check_collision(self, player_pos, player_size):
        """Check if projectile collides with player"""
        if not self.alive:
            return False
            
        distance = math.sqrt((self.pos[0] - player_pos[0])**2 + (self.pos[1] - player_pos[1])**2)
        return distance <= (self.size + player_size)
    
    def to_dict(self):
        return {
            'pos': self.pos,
            'size': self.size,
            'alive': self.alive,
            'spawn_side': self.spawn_side
        }

class LaserLine:
    def __init__(self, start_pos, end_pos, is_horizontal=False, rotation_config=None):
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.is_horizontal = is_horizontal
        self.animation_offset = 0
        self.pulse_direction = 1
        
        # Rotation properties - properly handle None case
        if rotation_config is None:
            rotation_config = {}
        
        self.rotation_config = rotation_config
        self.is_rotating = rotation_config.get('enabled', False)
        self.rotation_type = rotation_config.get('type', 'continuous')  # 'continuous' or 'player_triggered'
        self.rotation_speed = rotation_config.get('speed', 0.02)
        self.rotation_center = rotation_config.get('center', [(start_pos[0] + end_pos[0]) / 2, (start_pos[1] + end_pos[1]) / 2])
        self.rotation_angle = rotation_config.get('initial_angle', 0)
        self.rotation_range = rotation_config.get('range', math.pi)  # Total rotation range in radians
        self.rotation_direction = rotation_config.get('direction', 1)  # 1 or -1
        self.is_fast = rotation_config.get('is_fast', False)  # New: track if this is a fast laser
        
        # For player-triggered rotation
        self.trigger_distance = rotation_config.get('trigger_distance', 150)
        self.is_triggered = False
        
        # Store original positions for rotation calculations
        self.original_start = list(start_pos)
        self.original_end = list(end_pos)
        self.original_length = math.sqrt((end_pos[0] - start_pos[0])**2 + (end_pos[1] - start_pos[1])**2)
        self.original_angle = math.atan2(end_pos[1] - start_pos[1], end_pos[0] - start_pos[0])
    
    def update(self, players=None):
        # Animate laser pulsing effect
        self.animation_offset += 0.1 * self.pulse_direction
        if self.animation_offset > 1:
            self.pulse_direction = -1
        elif self.animation_offset < 0:
            self.pulse_direction = 1
        
        # Handle rotation
        if self.is_rotating:
            if self.rotation_type == 'continuous':
                self.rotation_angle += self.rotation_speed * self.rotation_direction
                # Keep angle within range
                if abs(self.rotation_angle) > self.rotation_range / 2:
                    self.rotation_direction *= -1
            
            elif self.rotation_type == 'player_triggered' and players:
                # Check if any player is within trigger distance
                player_nearby = False
                for player in players.values():
                    if player.alive and not player.finished:
                        distance = math.sqrt((player.pos[0] - self.rotation_center[0])**2 + 
                                           (player.pos[1] - self.rotation_center[1])**2)
                        if distance <= self.trigger_distance:
                            player_nearby = True
                            break
                
                if player_nearby and not self.is_triggered:
                    self.is_triggered = True
                elif not player_nearby and self.is_triggered:
                    self.is_triggered = False
                
                if self.is_triggered:
                    self.rotation_angle += self.rotation_speed * self.rotation_direction
                    # Keep angle within range
                    if abs(self.rotation_angle) > self.rotation_range / 2:
                        self.rotation_direction *= -1
            
            # Apply rotation to laser positions
            self._apply_rotation()
    
    def _apply_rotation(self):
        """Apply current rotation to laser line positions"""
        total_angle = self.original_angle + self.rotation_angle
        
        half_length = self.original_length / 2
        
        # Calculate new start and end positions
        self.start_pos = [
            self.rotation_center[0] - half_length * math.cos(total_angle),
            self.rotation_center[1] - half_length * math.sin(total_angle)
        ]
        
        self.end_pos = [
            self.rotation_center[0] + half_length * math.cos(total_angle),
            self.rotation_center[1] + half_length * math.sin(total_angle)
        ]
    
    def to_dict(self):
        return {
            'start_pos': self.start_pos,
            'end_pos': self.end_pos,
            'animation_offset': self.animation_offset,
            'is_rotating': self.is_rotating,
            'rotation_center': self.rotation_center,
            'rotation_angle': self.rotation_angle,
            'is_triggered': getattr(self, 'is_triggered', False),
            'is_fast': getattr(self, 'is_fast', False)  # Send fast laser info to client
        }
    
    def check_collision(self, player_pos, player_size):
        """Check if player collides with this laser line"""
        px, py = player_pos
        x1, y1 = self.start_pos
        x2, y2 = self.end_pos
        
        # Distance from point to line
        A = py - y1
        B = x1 - px
        C = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        
        if (x2 - x1)**2 + (y2 - y1)**2 == 0:
            return math.sqrt((px - x1)**2 + (py - y1)**2) <= player_size
        
        distance = abs(C) / math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        
        # Check if collision point is within line segment
        dot_product = (px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)
        line_length_squared = (x2 - x1)**2 + (y2 - y1)**2
        
        t = dot_product / line_length_squared
        
        if t < 0 or t > 1:
            # Collision point is outside line segment
            dist_to_start = math.sqrt((px - x1)**2 + (py - y1)**2)
            dist_to_end = math.sqrt((px - x2)**2 + (py - y2)**2)
            return min(dist_to_start, dist_to_end) <= player_size
        
        return distance <= player_size

class Player:
    def __init__(self, player_id, session_id, start_pos):
        self.id = player_id
        self.session_id = session_id
        self.pos = list(start_pos)
        self.start_pos = start_pos
        self.color = PLAYER_COLORS[player_id % len(PLAYER_COLORS)]
        self.alive = True
        self.finished = False
        self.finish_time = None
        self.trail = []
        self.trail_max_length = 20
        self.last_update = time.time()
    
    def update_position(self, new_pos):
        if self.alive and not self.finished:
            self.trail.append(tuple(self.pos))
            if len(self.trail) > self.trail_max_length:
                self.trail.pop(0)
            self.pos = list(new_pos)
            self.last_update = time.time()
    
    def reset(self):
        self.pos = list(self.start_pos)
        self.alive = True
        self.finished = False
        self.finish_time = None
        self.trail = []
    
    def to_dict(self):
        return {
            'id': self.id,
            'pos': self.pos,
            'color': self.color,
            'alive': self.alive,
            'finished': self.finished,
            'trail': self.trail[-10:]  # Send only last 10 trail points
        }

class GameLevel:
    def __init__(self, level_number):
        self.level = level_number
        self.laser_lines = []
        self.projectiles = []
        self.last_projectile_spawn = 0
        self.projectile_spawn_interval = self._calculate_projectile_spawn_interval()
        self.generate_level()
    
    def _calculate_projectile_spawn_interval(self):
        """Calculate projectile spawn interval based on level"""
        if self.level < 12:
            return float('inf')  # No projectiles before level 12
        
        # Start with 3 second intervals at level 12, decrease to minimum 0.5 seconds
        base_interval = 3.0
        level_factor = self.level - 12
        min_interval = 0.5
        
        interval = max(min_interval, base_interval - (level_factor * 0.2))
        return interval
    
    def _get_random_alive_player_pos(self, players):
        """Get position of a random alive player for targeting"""
        if not players:
            return None
            
        alive_players = [p for p in players.values() if p.alive and not p.finished]
        if not alive_players:
            return None
            
        return random.choice(alive_players).pos.copy()
    
    def spawn_projectile(self, players=None):
        """Spawn a new projectile from a random direction"""
        if self.level < 12:
            return
            
        spawn_sides = ['left', 'right', 'top', 'bottom']
        spawn_side = random.choice(spawn_sides)
        
        # 30% chance to target a player, 70% chance for random direction
        target_pos = None
        if random.random() < 0.3 and players:
            target_pos = self._get_random_alive_player_pos(players)
        
        projectile = Projectile(spawn_side, target_pos)
        self.projectiles.append(projectile)
        print(f"Projectile spawned from {spawn_side} {'(targeting player)' if target_pos else '(random direction)'}")
    
    def generate_level(self):
        self.laser_lines = []
        
        # Calculate number of lasers based on level (increasing difficulty)
        num_lasers = min(3 + self.level * 2, 15)
        
        # Determine if we should add rotating lasers (level 5+)
        has_rotating_lasers = self.level >= 5
        
        # Generate regular laser lines
        for i in range(num_lasers):
            # Randomly choose horizontal or vertical
            is_horizontal = random.choice([True, False])
            
            # Determine if this laser should rotate (for level 7+)
            rotation_config = None  # Default to None
            
            if has_rotating_lasers:
                # Base rotation chances
                rotation_chance = random.random()
                
                # For level 10+, add fast rotation option
                if self.level >= 10:
                    # 15% chance for fast continuous, 10% for fast player-triggered, 
                    # 25% for normal continuous, 15% for normal player-triggered
                    if rotation_chance < 0.15:
                        # Fast continuous rotation
                        rotation_config = {
                            'enabled': True,
                            'type': 'continuous',
                            'speed': random.uniform(0.08, 0.15),  # Much faster
                            'range': random.uniform(math.pi/2, math.pi * 1.5),  # Larger range
                            'direction': random.choice([1, -1]),
                            'is_fast': True  # Mark as fast for visual effects
                        }
                    elif rotation_chance < 0.25:
                        # Fast player-triggered rotation
                        rotation_config = {
                            'enabled': True,
                            'type': 'player_triggered',
                            'speed': random.uniform(0.10, 0.18),  # Very fast when triggered
                            'range': random.uniform(math.pi, math.pi * 1.8),  # Large range
                            'direction': random.choice([1, -1]),
                            'trigger_distance': random.randint(100, 150),  # Closer trigger for surprise
                            'is_fast': True
                        }
                    elif rotation_chance < 0.50:
                        # Normal continuous rotation
                        rotation_config = {
                            'enabled': True,
                            'type': 'continuous',
                            'speed': random.uniform(0.01, 0.03),
                            'range': random.uniform(math.pi/3, math.pi),
                            'direction': random.choice([1, -1]),
                            'is_fast': False
                        }
                    elif rotation_chance < 0.65:
                        # Normal player-triggered rotation
                        rotation_config = {
                            'enabled': True,
                            'type': 'player_triggered',
                            'speed': random.uniform(0.02, 0.04),
                            'range': random.uniform(math.pi/2, math.pi),
                            'direction': random.choice([1, -1]),
                            'trigger_distance': random.randint(120, 180),
                            'is_fast': False
                        }
                else:
                    # Level 5-9: Original rotation logic (normal speed only)
                    if rotation_chance < 0.3:
                        rotation_config = {
                            'enabled': True,
                            'type': 'continuous',
                            'speed': random.uniform(0.01, 0.03),
                            'range': random.uniform(math.pi/3, math.pi),
                            'direction': random.choice([1, -1]),
                            'is_fast': False
                        }
                    elif rotation_chance < 0.5:
                        rotation_config = {
                            'enabled': True,
                            'type': 'player_triggered',
                            'speed': random.uniform(0.02, 0.04),
                            'range': random.uniform(math.pi/2, math.pi),
                            'direction': random.choice([1, -1]),
                            'trigger_distance': random.randint(120, 180),
                            'is_fast': False
                        }
            
            if is_horizontal:
                # Horizontal laser
                y = random.randint(50, WINDOW_HEIGHT - 50)
                start_x = random.randint(START_ZONE_WIDTH + 50, WINDOW_WIDTH - FINISH_ZONE_WIDTH - 200)
                end_x = start_x + random.randint(100, 300)
                end_x = min(end_x, WINDOW_WIDTH - FINISH_ZONE_WIDTH - 50)
                
                start_pos = [start_x, y]
                end_pos = [end_x, y]
                
                if rotation_config is not None:
                    # Set rotation center
                    rotation_config['center'] = [(start_x + end_x) / 2, y]
                
                laser = LaserLine(start_pos, end_pos, True, rotation_config)
            else:
                # Vertical laser
                x = random.randint(START_ZONE_WIDTH + 50, WINDOW_WIDTH - FINISH_ZONE_WIDTH - 50)
                start_y = random.randint(50, WINDOW_HEIGHT - 200)
                end_y = start_y + random.randint(100, 200)
                end_y = min(end_y, WINDOW_HEIGHT - 50)
                
                start_pos = [x, start_y]
                end_pos = [x, end_y]
                
                if rotation_config is not None:
                    # Set rotation center
                    rotation_config['center'] = [x, (start_y + end_y) / 2]
                
                laser = LaserLine(start_pos, end_pos, False, rotation_config)
            
            self.laser_lines.append(laser)
    
    def update(self, players=None, dt=1/60):
        # Update lasers
        for laser in self.laser_lines:
            laser.update(players)
        
        # Update projectiles
        for projectile in self.projectiles[:]:  # Use slice copy to avoid modification during iteration
            projectile.update(dt)
            if not projectile.alive:
                self.projectiles.remove(projectile)
        
        # Spawn new projectiles based on interval
        current_time = time.time()
        if (self.level >= 12 and 
            current_time - self.last_projectile_spawn >= self.projectile_spawn_interval):
            self.spawn_projectile(players)
            self.last_projectile_spawn = current_time
    
    def check_laser_collisions(self, player_pos):
        for laser in self.laser_lines:
            if laser.check_collision(player_pos, PLAYER_SIZE):
                return True
        return False
    
    def check_projectile_collisions(self, player_pos):
        for projectile in self.projectiles:
            if projectile.check_collision(player_pos, PLAYER_SIZE):
                return True
        return False
    
    def reset_projectiles(self):
        """Clear all projectiles and reset spawn timer"""
        self.projectiles = []
        self.last_projectile_spawn = time.time()
        self.projectile_spawn_interval = self._calculate_projectile_spawn_interval()
    
    def to_dict(self):
        return {
            'level': self.level,
            'lasers': [laser.to_dict() for laser in self.laser_lines],
            'projectiles': [projectile.to_dict() for projectile in self.projectiles if projectile.alive]
        }

class GameManager:
    def __init__(self):
        self.players = {}
        self.current_level = 1
        self.level = GameLevel(self.current_level)
        self.game_state = 'waiting'  # waiting, playing, level_complete
        self.round_winner = None
        self.level_timer = 0
        self.last_update = time.time()
        self.running = True
        
        # Start game loop
        self.game_thread = threading.Thread(target=self.game_loop)
        self.game_thread.daemon = True
        self.game_thread.start()
    
    def add_player(self, session_id):
        player_id = len(self.players)
        start_y = 100 + player_id * 60
        if start_y > WINDOW_HEIGHT - 100:
            start_y = 100 + (player_id % 8) * 60
        
        player = Player(player_id, session_id, [50, start_y])
        self.players[session_id] = player
        print(f"Player {player_id} joined (session: {session_id})")
        return player
    
    def remove_player(self, session_id):
        if session_id in self.players:
            player_id = self.players[session_id].id
            del self.players[session_id]
            print(f"Player {player_id} left (session: {session_id})")
    
    def move_player(self, session_id, new_pos):
        if session_id in self.players:
            player = self.players[session_id]
            if player.alive and not player.finished and self.game_state == 'playing':
                # Keep player in bounds
                new_pos[0] = max(PLAYER_SIZE, min(WINDOW_WIDTH - PLAYER_SIZE, new_pos[0]))
                new_pos[1] = max(PLAYER_SIZE, min(WINDOW_HEIGHT - PLAYER_SIZE, new_pos[1]))
                player.update_position(new_pos)
    
    def start_game(self):
        if self.game_state == 'waiting' and len(self.players) > 0:
            self.game_state = 'playing'
            print("Game started!")
    
    def next_level(self):
        self.current_level += 1
        self.level = GameLevel(self.current_level)
        self.game_state = 'playing'
        self.round_winner = None
        
        # Reset all players
        for player in self.players.values():
            player.reset()
        
        print(f"Starting level {self.current_level}")
        if self.current_level >= 5:
            print("Rotating lasers enabled!")
        if self.current_level >= 10:
            print("FAST rotating lasers enabled! 🚨")
        if self.current_level >= 12:
            print("PROJECTILES enabled! 💥 Watch out for incoming objects!")
    
    def game_loop(self):
        while self.running:
            current_time = time.time()
            dt = current_time - self.last_update
            self.last_update = current_time
            
            if self.game_state == 'playing':
                # Pass players to level update for player-triggered rotation and projectile targeting
                self.level.update(self.players, dt)
                
                # Check for collisions and finish line
                for player in self.players.values():
                    if player.alive and not player.finished:
                        # Check laser collisions
                        if self.level.check_laser_collisions(player.pos):
                            player.alive = False
                            print(f"Player {player.id} hit a laser!")
                        
                        # Check projectile collisions
                        elif self.level.check_projectile_collisions(player.pos):
                            player.alive = False
                            print(f"Player {player.id} hit a projectile!")
                        
                        # Check finish line
                        elif player.pos[0] >= WINDOW_WIDTH - FINISH_ZONE_WIDTH:
                            player.finished = True
                            player.finish_time = current_time
                            if self.round_winner is None:
                                self.round_winner = player.id
                                print(f"Player {player.id} wins the round!")
                                self.game_state = 'level_complete'
                                self.level_timer = current_time
                
                # Check if all players are dead or finished
                active_players = [p for p in self.players.values() if p.alive and not p.finished]
                if not active_players and self.game_state == 'playing':
                    self.game_state = 'level_complete'
                    self.level_timer = current_time
            
            elif self.game_state == 'level_complete':
                # Wait 3 seconds before next level
                if current_time - self.level_timer > 3:
                    self.next_level()
            
            # Broadcast game state to all clients
            self.broadcast_game_state()
            
            time.sleep(1/60)  # 60 FPS
    
    def broadcast_game_state(self):
        game_data = {
            'players': {sid: player.to_dict() for sid, player in self.players.items()},
            'level_data': self.level.to_dict(),
            'game_state': self.game_state,
            'winner': self.round_winner,
            'current_level': self.current_level
        }
        
        socketio.emit('game_state', game_data)
    
    def get_game_state(self):
        return {
            'players': {sid: player.to_dict() for sid, player in self.players.items()},
            'level_data': self.level.to_dict(),
            'game_state': self.game_state,
            'winner': self.round_winner,
            'current_level': self.current_level
        }

# Global game manager
game_manager = GameManager()

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')
    player = game_manager.add_player(request.sid)
    
    # Send initial data to the new player
    emit('player_init', {
        'player_id': player.id,
        'session_id': request.sid
    })
    
    # Send current game state
    emit('game_state', game_manager.get_game_state())

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')
    game_manager.remove_player(request.sid)

@socketio.on('move_player')
def handle_move_player(data):
    game_manager.move_player(request.sid, data['pos'])

@socketio.on('start_game')
def handle_start_game():
    game_manager.start_game()

if __name__ == '__main__':
    print("Starting Laser Obstacle Course Web Server...")
    print("Open your browser and go to: http://localhost:5555")
    socketio.run(app, host='0.0.0.0', port=5555, debug=False)