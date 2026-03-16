"""GhrmUserGithubAccessRepository — data access for user GitHub OAuth records."""
from typing import Optional, List
from datetime import datetime
from plugins.ghrm.src.models.ghrm_user_github_access import GhrmUserGithubAccess


class GhrmUserGithubAccessRepository:
    def __init__(self, session) -> None:
        self.session = session

    def find_by_user_id(self, user_id: str) -> Optional[GhrmUserGithubAccess]:
        return (
            self.session.query(GhrmUserGithubAccess)
            .filter(GhrmUserGithubAccess.user_id == user_id)
            .first()
        )

    def find_grace_expired(self, now: datetime) -> List[GhrmUserGithubAccess]:
        from plugins.ghrm.src.models.ghrm_user_github_access import AccessStatus

        return (
            self.session.query(GhrmUserGithubAccess)
            .filter(
                GhrmUserGithubAccess.access_status == AccessStatus.GRACE,
                GhrmUserGithubAccess.grace_expires_at <= now,
            )
            .all()
        )

    def save(self, access: GhrmUserGithubAccess) -> GhrmUserGithubAccess:
        self.session.add(access)
        self.session.flush()
        self.session.commit()
        return access

    def delete(self, access_id: str) -> bool:
        access = (
            self.session.query(GhrmUserGithubAccess)
            .filter(GhrmUserGithubAccess.id == access_id)
            .first()
        )
        if access:
            self.session.delete(access)
            self.session.flush()
            self.session.commit()
            return True
        return False
