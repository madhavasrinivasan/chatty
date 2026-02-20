from jose import jwt
from app.core.config.config import Settings

class JWTService:
    def __init__(self):
        self.settings = Settings()

    def generate_token(self, data: dict):
        return jwt.encode(
            data.copy(),
            self.settings.jwt_secret,
            algorithm=self.settings.jwt_algorithm
        )