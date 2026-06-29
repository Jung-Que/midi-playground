from utils import *
from square import Square
from math import exp


class Camera:
    def __init__(self, x: int = 0, y: int = 0):
        self.x = x
        self.y = y
        # ?x, ?y are variables for misc things
        self.ax = 0
        self.ay = 0
        self.bx = 0
        self.by = 0
        self.locked_on_square = True
        self.lock_type: CameraFollow = get_camera_follow(Config.camera_mode)

    def attempt_movement(self):
        if not self.locked_on_square:
            keys = pygame.key.get_pressed()
            shift_modifier = (keys[pygame.K_LSHIFT] | keys[pygame.K_RSHIFT]) + 1
            self.x += (keys[pygame.K_d] - keys[pygame.K_a]) * Config.CAMERA_SPEED * shift_modifier / FRAMERATE
            self.y += (keys[pygame.K_s] - keys[pygame.K_w]) * Config.CAMERA_SPEED * shift_modifier / FRAMERATE

    @property
    def pos(self):
        return self.x, self.y

    @pos.setter
    def pos(self, val: Union[tuple[int, int], list[int]]):
        self.x, self.y = val

    def offset(self, pos_or_rect: Union[pygame.Rect, tuple[int, int]]) -> Union[pygame.Rect, list[int]]:
        if isinstance(pos_or_rect, pygame.Rect):
            return pos_or_rect.move(-self.x, -self.y)
        else:
            return [pos_or_rect[0]-self.x, pos_or_rect[1]-self.y]

    def follow(self, square: Square, target=None):

        # square in center
        if self.lock_type == CameraFollow.Center:
            self.pos = [square.x - Config.SCREEN_WIDTH / 2, square.y - Config.SCREEN_HEIGHT / 2]

        # camera only follows if necessary
        if self.lock_type == CameraFollow.Lazy:
            lazy_follow_distance = 250
            while square.x - Config.SCREEN_WIDTH + lazy_follow_distance > self.x:
                self.x += 1
            while square.y - Config.SCREEN_HEIGHT + lazy_follow_distance > self.y:
                self.y += 1
            while square.x - lazy_follow_distance < self.x:
                self.x -= 1
            while square.y - lazy_follow_distance < self.y:
                self.y -= 1

        # smooth camera
        if self.lock_type == CameraFollow.Smoothed:
            easing_rate = 3
            self.x = (square.x - Config.SCREEN_WIDTH / 2) * easing_rate * Config.dt + self.x - easing_rate * self.x * Config.dt
            self.y = (square.y - Config.SCREEN_HEIGHT / 2) * easing_rate * Config.dt + self.y - easing_rate * self.y * Config.dt

        # camera in front of square
        if self.lock_type == CameraFollow.Predictive:
            self.ax = (square.x - Config.SCREEN_WIDTH / 2) * 3 * Config.dt + self.ax - 3 * self.ax * Config.dt
            self.ay = (square.y - Config.SCREEN_HEIGHT / 2) * 3 * Config.dt + self.ay - 3 * self.ay * Config.dt
            damping = 1
            self.bx = square.x - damping * (self.ax - square.x) - Config.SCREEN_WIDTH / 2 - Config.SCREEN_WIDTH / 2 * damping
            self.by = square.y - damping * (self.ay - square.y) - Config.SCREEN_HEIGHT / 2 - Config.SCREEN_HEIGHT / 2 * damping
            self.x = self.x*(1-3*Config.dt)+self.bx*3*Config.dt
            self.y = self.y*(1-3*Config.dt)+self.by*3*Config.dt

        if self.lock_type == CameraFollow.TargetLead:
            focus_x = square.x
            focus_y = square.y
            if target is not None:
                lead_x = (target[0] - square.x) * float(Config.camera_target_lead)
                lead_y = (target[1] - square.y) * float(Config.camera_target_lead)
                max_x = Config.SCREEN_WIDTH * float(Config.camera_max_lead_ratio)
                max_y = Config.SCREEN_HEIGHT * float(Config.camera_max_lead_ratio)
                focus_x += max(-max_x, min(max_x, lead_x))
                focus_y += max(-max_y, min(max_y, lead_y))

            desired_x = focus_x - Config.SCREEN_WIDTH / 2
            desired_y = focus_y - Config.SCREEN_HEIGHT / 2
            dt = max(float(Config.dt), 0.0)
            smoothing = max(float(Config.camera_smoothing_seconds), 0.001)
            alpha = 1.0 - exp(-dt / smoothing)
            max_step = max(float(Config.camera_max_speed), 1.0) * dt
            step_x = max(-max_step, min(max_step, (desired_x - self.x) * alpha))
            step_y = max(-max_step, min(max_step, (desired_y - self.y) * alpha))
            self.x += step_x
            self.y += step_y
