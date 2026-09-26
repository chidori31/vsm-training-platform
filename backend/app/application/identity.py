from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.demo_personas import PERSONAS
from app.application.errors import UseCaseError
from app.application.sessions import database_time
from app.domain.profiles import EmployeeProfile
from app.persistence.identity import DemoToken, UserProfile


@dataclass(frozen=True)
class DemoLogin:
    access_token: str
    expires_at: datetime
    profile: EmployeeProfile


class IdentityService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def demo_login(self, persona_id: str = "demo-employee") -> DemoLogin:
        persona = next((p for p in PERSONAS if p["id"] == persona_id), None)
        if persona is None:
            raise UseCaseError("unknown_demo_persona", "Unknown demo persona")
        profile = EmployeeProfile(persona["id"], persona["display_name"])
        token = token_urlsafe(32)
        with Session(self.engine) as database, database.begin():
            database.execute(
                insert(UserProfile)
                .values(**persona)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            expires = database_time(database) + timedelta(hours=24)
            database.add(
                DemoToken(
                    token_hash=sha256(token.encode()).hexdigest(),
                    employee_id=profile.id,
                    expires_at=expires,
                )
            )
        return DemoLogin(token, expires, profile)

    def authenticate(self, token: str) -> EmployeeProfile:
        with Session(self.engine) as database:
            record = database.get(DemoToken, sha256(token.encode()).hexdigest())
            if record is None or record.expires_at <= database_time(database):
                raise UseCaseError("unauthorized", "Invalid or expired bearer token")
            profile = database.get(UserProfile, record.employee_id)
            if profile is None:
                raise UseCaseError("unauthorized", "Profile is unavailable")
            return EmployeeProfile(profile.id, profile.display_name)
