import random
from math import exp
from utils import *
import pygame


class Particle:
    SPEED_VARIATION = 4
    SIZE_MIN = 7
    SIZE_MAX = 14
    DRAG = 5.0

    def __init__(
            self,
            pos: list[float],
            delta: list[float],
            invert_color: bool = False,
            *,
            color=None,
            lifetime: float = 0.25,
            size_range: tuple[int, int] = None,
            speed_scale: float = 1.0,
    ):
        self.pos = pos.copy()
        size_min, size_max = size_range or (Particle.SIZE_MIN, Particle.SIZE_MAX)
        self.initial_size = float(random.randint(size_min, size_max))
        self.size = self.initial_size
        jitter_x = random.randint(-Particle.SPEED_VARIATION, Particle.SPEED_VARIATION) / 8
        jitter_y = random.randint(-Particle.SPEED_VARIATION, Particle.SPEED_VARIATION) / 8
        speed = Config.PARTICLE_SPEED * FRAMERATE * float(speed_scale)
        self.velocity = [
            (delta[0] + jitter_x) * speed,
            (delta[1] + jitter_y) * speed,
        ]
        self.lifetime = max(float(lifetime), 0.001)
        self.elapsed = 0.0
        default_color = get_colors()["hallway"] if not invert_color else get_colors()["background"]
        self.color = pygame.Color(color or default_color)
        self.alpha = 255

    def age(self):
        dt = max(0.0, min(float(Config.dt), 0.1))
        self.elapsed += dt
        damping = exp(-Particle.DRAG * dt)
        travel_factor = (1.0 - damping) / Particle.DRAG
        self.x += self.velocity[0] * travel_factor
        self.y += self.velocity[1] * travel_factor
        self.velocity[0] *= damping
        self.velocity[1] *= damping
        progress = min(self.elapsed / self.lifetime, 1.0)
        self.size = max(0.0, self.initial_size * (1.0 - progress))
        self.alpha = int(255 * (1.0 - progress) ** 2)
        return progress >= 1.0

    @property
    def x(self):
        return self.pos[0]

    @x.setter
    def x(self, val: float):
        self.pos[0] = val

    @property
    def y(self):
        return self.pos[1]

    @y.setter
    def y(self, val: float):
        self.pos[1] = val

    @property
    def rect(self):
        return pygame.Rect(self.x-self.size/2, self.y-self.size/2, *(2*[self.size]))
