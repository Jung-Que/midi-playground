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
        self.zoom = 1.0
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
            return pygame.Rect(
                round((pos_or_rect.x - self.x) * self.zoom),
                round((pos_or_rect.y - self.y) * self.zoom),
                max(1, round(pos_or_rect.width * self.zoom)),
                max(1, round(pos_or_rect.height * self.zoom)),
            )
        else:
            return [
                round((pos_or_rect[0] - self.x) * self.zoom),
                round((pos_or_rect[1] - self.y) * self.zoom),
            ]

    def world_view(self, screen_rect: pygame.Rect) -> pygame.Rect:
        zoom = max(self.zoom, 0.001)
        return pygame.Rect(
            self.x + screen_rect.x / zoom,
            self.y + screen_rect.y / zoom,
            screen_rect.width / zoom,
            screen_rect.height / zoom,
        )

    def _follow_shorts(self, square: Square, target=None):
        target = target or square.pos
        padding = float(Config.SQUARE_SIZE)
        min_x = min(square.x, target[0]) - padding
        max_x = max(square.x, target[0]) + padding
        min_y = min(square.y, target[1]) - padding
        max_y = max(square.y, target[1]) + padding
        margin_x = max(0.05, min(float(Config.shorts_safe_margin_x), 0.35))
        margin_y = max(0.05, min(float(Config.shorts_safe_margin_y), 0.35))
        safe_left = Config.SCREEN_WIDTH * margin_x
        safe_right = Config.SCREEN_WIDTH * (1 - margin_x)
        safe_top = Config.SCREEN_HEIGHT * margin_y
        safe_bottom = Config.SCREEN_HEIGHT * (1 - margin_y)
        safe_width = max(safe_right - safe_left, 1.0)
        safe_height = max(safe_bottom - safe_top, 1.0)
        required_width = max(max_x - min_x, Config.SQUARE_SIZE)
        required_height = max(max_y - min_y, Config.SQUARE_SIZE)
        target_zoom = min(1.0, safe_width / required_width, safe_height / required_height)
        target_zoom = max(float(Config.shorts_min_zoom), target_zoom)

        dt = max(float(Config.dt), 0.0)
        smoothing = max(float(Config.camera_smoothing_seconds), 0.001)
        alpha = 1.0 - exp(-dt / smoothing)
        if target_zoom < self.zoom:
            self.zoom = target_zoom
        else:
            self.zoom += (target_zoom - self.zoom) * alpha

        focus_x = (min_x + max_x) / 2
        focus_y = (min_y + max_y) / 2
        desired_x = focus_x - Config.SCREEN_WIDTH / (2 * self.zoom)
        desired_y = focus_y - Config.SCREEN_HEIGHT / (2 * self.zoom)
        max_step = max(float(Config.camera_max_speed), 1.0) * dt / max(self.zoom, 0.001)
        self.x += max(-max_step, min(max_step, (desired_x - self.x) * alpha))
        self.y += max(-max_step, min(max_step, (desired_y - self.y) * alpha))

        # Translation constraints keep both the square and immediate target inside the recording-safe frame.
        lower_x = max_x - safe_right / self.zoom
        upper_x = min_x - safe_left / self.zoom
        lower_y = max_y - safe_bottom / self.zoom
        upper_y = min_y - safe_top / self.zoom
        if lower_x <= upper_x:
            self.x = max(lower_x, min(upper_x, self.x))
        if lower_y <= upper_y:
            self.y = max(lower_y, min(upper_y, self.y))

    def follow(self, square: Square, target=None):

        if Config.shorts_mode:
            self._follow_shorts(square, target)
            return
        self.zoom = 1.0

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
