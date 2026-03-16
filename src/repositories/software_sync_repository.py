"""GhrmSoftwareSyncRepository — data access for software sync records."""
from typing import Optional
from plugins.ghrm.src.models.ghrm_software_sync import GhrmSoftwareSync


class GhrmSoftwareSyncRepository:
    def __init__(self, session) -> None:
        self.session = session

    def find_by_package_id(self, package_id: str) -> Optional[GhrmSoftwareSync]:
        return self.session.query(GhrmSoftwareSync).filter(GhrmSoftwareSync.software_package_id == package_id).first()

    def save(self, sync: GhrmSoftwareSync) -> GhrmSoftwareSync:
        self.session.add(sync)
        self.session.flush()
        self.session.commit()
        return sync
