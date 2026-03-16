"""GhrmAccessLogRepository — write-only audit log for access events."""
from typing import Optional, Dict, Any
from plugins.ghrm.src.models.ghrm_access_log import GhrmAccessLog


class GhrmAccessLogRepository:
    def __init__(self, session) -> None:
        self.session = session

    def log(
        self,
        user_id: Optional[str],
        package_id: Optional[str],
        action: str,
        triggered_by: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = GhrmAccessLog(
            user_id=user_id,
            package_id=package_id,
            action=action,
            triggered_by=triggered_by,
            meta=meta or {},
        )
        self.session.add(entry)
        self.session.flush()
        self.session.commit()

    def find_by_user(
        self, user_id: str, page: int = 1, per_page: int = 20
    ) -> Dict[str, Any]:
        q = (
            self.session.query(GhrmAccessLog)
            .filter(GhrmAccessLog.user_id == user_id)
            .order_by(GhrmAccessLog.created_at.desc())
        )
        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        return {
            "items": [i.to_dict() for i in items],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
