from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from models import VehiclePosition


@runtime_checkable
class PositionSink(Protocol):
    def put(self, position: VehiclePosition) -> None: ...


@runtime_checkable
class PositionSource(Protocol):
    def get_positions(self) -> list[VehiclePosition]: ...


@runtime_checkable
class CookieProvider(Protocol):
    @property
    def cookies(self) -> dict[str, str]: ...

    @property
    def user_agent(self) -> str: ...


@runtime_checkable
class DataWriter(Protocol):
    def write_routes(self, data: dict[str, Any]) -> None: ...

    def write_positions(self, positions: list[dict[str, Any]]) -> None: ...


@runtime_checkable
class Startable(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class ConnectionStats(Protocol):
    @property
    def is_connected(self) -> bool: ...

    @property
    def stats(self) -> dict[str, Any]: ...
