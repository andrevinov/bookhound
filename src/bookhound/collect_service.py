from typing import Protocol

from bookhound.discovery_pipeline import DiscoveryPipelineResult
from bookhound.repositories import CollectSummary


class CollectionPersistenceBoundary(Protocol):
    def save_discovery_result(self, result: DiscoveryPipelineResult) -> CollectSummary:
        raise NotImplementedError


class CollectService:
    def __init__(self, repositories: CollectionPersistenceBoundary) -> None:
        self.repositories = repositories

    def save_result(self, result: DiscoveryPipelineResult) -> CollectSummary:
        return self.repositories.save_discovery_result(result)
