"""Track both clients and devices using UniFi Network."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Generic, TypeVar

import aiounifi
from aiounifi.interfaces.api_handlers import ItemEvent
from aiounifi.interfaces.clients import Clients
from aiounifi.interfaces.devices import Devices
from aiounifi.models.client import Client
from aiounifi.models.device import Device
from aiounifi.models.event import Event, EventKey

from homeassistant.components.device_tracker import DOMAIN, ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN as UNIFI_DOMAIN
from .controller import UniFiController

LOGGER = logging.getLogger(__name__)

CLIENT_TRACKER = "client"
DEVICE_TRACKER = "device"

CLIENT_CONNECTED_ATTRIBUTES = [
    "_is_guest_by_uap",
    "ap_mac",
    "authorized",
    "essid",
    "ip",
    "is_11r",
    "is_guest",
    "note",
    "qos_policy_applied",
    "radio",
    "radio_proto",
    "vlan",
]

CLIENT_STATIC_ATTRIBUTES = [
    "hostname",
    "mac",
    "name",
    "oui",
]


CLIENT_CONNECTED_ALL_ATTRIBUTES = CLIENT_CONNECTED_ATTRIBUTES + CLIENT_STATIC_ATTRIBUTES

WIRED_CONNECTION = (EventKey.WIRED_CLIENT_CONNECTED,)
WIRED_DISCONNECTION = (EventKey.WIRED_CLIENT_DISCONNECTED,)
WIRELESS_CONNECTION = (
    EventKey.WIRELESS_CLIENT_CONNECTED,
    EventKey.WIRELESS_CLIENT_ROAM,
    EventKey.WIRELESS_CLIENT_ROAMRADIO,
    EventKey.WIRELESS_GUEST_CONNECTED,
    EventKey.WIRELESS_GUEST_ROAM,
    EventKey.WIRELESS_GUEST_ROAMRADIO,
)
WIRELESS_DISCONNECTION = (
    EventKey.WIRELESS_CLIENT_DISCONNECTED,
    EventKey.WIRELESS_GUEST_DISCONNECTED,
)


_DataT = TypeVar("_DataT")
_HandlerT = TypeVar("_HandlerT")


@callback
def async_device_available_fn(controller: UniFiController, obj_id: str) -> bool:
    """Check if device object is disabled."""
    device = controller.api.devices[obj_id]
    return controller.available and not device.disabled


@callback
def async_device_heartbeat_timedelta_fn(
    controller: UniFiController, obj_id: str
) -> timedelta:
    """Check if device object is disabled."""
    device = controller.api.devices[obj_id]
    return timedelta(seconds=device.next_interval + 60)


@callback
def async_device_is_connected_fn(controller: UniFiController, obj_id: str) -> bool:
    """Check if device object is disabled."""
    device = controller.api.devices[obj_id]
    return device.state == 1


@callback
def async_client_allowed_fn(controller: UniFiController, obj_id: str) -> bool:
    """Check if client is allowed."""
    client = controller.api.clients[obj_id]
    if not controller.option_track_clients:
        return False

    if client.mac not in controller.wireless_clients:
        if not controller.option_track_wired_clients:
            return False

    elif (
        client.essid
        and controller.option_ssid_filter
        and client.essid not in controller.option_ssid_filter
    ):
        return False

    return True


@callback
def async_client_heartbeat_timedelta_fn(
    controller: UniFiController, obj_id: str
) -> timedelta:
    """Check if device object is disabled."""
    return controller.option_detection_time


@callback
def async_client_is_connected_fn(controller: UniFiController, obj_id: str) -> bool:
    """Check if device object is disabled."""
    client = controller.api.clients[obj_id]
    if client.is_wired != (is_wired := client.mac not in controller.wireless_clients):
        return False  # Wired bug in action
    if (
        not is_wired
        and client.essid
        and controller.option_ssid_filter
        and client.essid not in controller.option_ssid_filter
    ):
        return False

    if (
        dt_util.utcnow() - dt_util.utc_from_timestamp(float(client.last_seen or 0))
        > controller.option_detection_time
    ):
        return False
    return True


@dataclass
class UnifiEntityLoader(Generic[_HandlerT, _DataT]):
    """Validate and load entities from different UniFi handlers."""

    allowed_fn: Callable[[UniFiController, str], bool]
    api_handler_fn: Callable[[aiounifi.Controller], _HandlerT]
    available_fn: Callable[[UniFiController, str], bool]
    event_is_on: tuple[EventKey, ...] | None
    event_to_subscribe: tuple[EventKey, ...] | None
    heartbeat_timedelta_fn: Callable[[UniFiController, str], timedelta]
    is_connected_fn: Callable[[UniFiController, str], bool]
    name_fn: Callable[[_DataT], str | None]
    object_fn: Callable[[aiounifi.Controller, str], _DataT]
    supported_fn: Callable[[aiounifi.Controller, str], bool | None]
    unique_id_fn: Callable[[UniFiController, str], str]
    ip_address_fn: Callable[[aiounifi.Controller, str], str]
    hostname_fn: Callable[[aiounifi.Controller, str], str | None]


@dataclass
class UnifiEntityDescription(EntityDescription, UnifiEntityLoader[_HandlerT, _DataT]):
    """Class describing UniFi switch entity."""


ENTITY_DESCRIPTIONS: tuple[UnifiEntityDescription, ...] = (
    UnifiEntityDescription[Clients, Client](
        key="Client device scanner",
        has_entity_name=True,
        allowed_fn=async_client_allowed_fn,
        api_handler_fn=lambda api: api.clients,
        available_fn=lambda controller, _: controller.available,
        event_is_on=WIRED_CONNECTION + WIRELESS_CONNECTION,
        event_to_subscribe=WIRED_CONNECTION
        + WIRED_DISCONNECTION
        + WIRELESS_CONNECTION
        + WIRELESS_DISCONNECTION,
        heartbeat_timedelta_fn=async_client_heartbeat_timedelta_fn,
        is_connected_fn=async_client_is_connected_fn,
        name_fn=lambda client: client.name or client.hostname,
        object_fn=lambda api, obj_id: api.clients[obj_id],
        supported_fn=lambda controller, obj_id: True,
        unique_id_fn=lambda controller, obj_id: f"{obj_id}-{controller.site}",
        ip_address_fn=lambda api, obj_id: api.clients[obj_id].ip,
        hostname_fn=lambda api, obj_id: None,
    ),
    UnifiEntityDescription[Devices, Device](
        key="UniFi device scanner",
        has_entity_name=True,
        icon="mdi:ethernet",
        allowed_fn=lambda controller, obj_id: controller.option_track_devices,
        api_handler_fn=lambda api: api.devices,
        available_fn=async_device_available_fn,
        event_is_on=None,
        event_to_subscribe=None,
        heartbeat_timedelta_fn=async_device_heartbeat_timedelta_fn,
        is_connected_fn=async_device_is_connected_fn,
        name_fn=lambda device: device.name or device.model,
        object_fn=lambda api, obj_id: api.devices[obj_id],
        supported_fn=lambda controller, obj_id: True,
        unique_id_fn=lambda controller, obj_id: obj_id,
        ip_address_fn=lambda api, obj_id: api.devices[obj_id].ip,
        hostname_fn=lambda api, obj_id: None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker for UniFi Network integration."""
    controller: UniFiController = hass.data[UNIFI_DOMAIN][config_entry.entry_id]
    controller.entities[DOMAIN] = {CLIENT_TRACKER: set(), DEVICE_TRACKER: set()}

    @callback
    def async_load_entities(description: UnifiEntityDescription) -> None:
        """Load and subscribe to UniFi devices."""
        entities: list[ScannerEntity] = []
        api_handler = description.api_handler_fn(controller.api)

        @callback
        def async_create_entity(event: ItemEvent, obj_id: str) -> None:
            """Create UniFi entity."""
            if not description.allowed_fn(
                controller, obj_id
            ) or not description.supported_fn(controller.api, obj_id):
                return

            entity = UnifiScannerEntity(obj_id, controller, description)
            if event == ItemEvent.ADDED:
                async_add_entities([entity])
                return
            entities.append(entity)

        for obj_id in api_handler:
            async_create_entity(ItemEvent.CHANGED, obj_id)
        async_add_entities(entities)

        api_handler.subscribe(async_create_entity, ItemEvent.ADDED)

    for description in ENTITY_DESCRIPTIONS:
        async_load_entities(description)


class UnifiScannerEntity(ScannerEntity):
    """Representation of a UniFi scanner."""

    entity_description: UnifiEntityDescription
    _attr_should_poll = False

    def __init__(
        self,
        obj_id: str,
        controller: UniFiController,
        description: UnifiEntityDescription,
    ) -> None:
        """Set up UniFi scanner entity."""
        self._obj_id = obj_id
        self.controller = controller
        self.entity_description = description

        self._controller_connection_state_changed = False
        self._ignore_events = False
        self._removed = False

        self._attr_available = description.available_fn(controller, obj_id)
        self._attr_unique_id: str = description.unique_id_fn(controller, obj_id)

        obj = description.object_fn(controller.api, obj_id)
        self._is_connected = description.is_connected_fn(controller, obj_id)
        self._attr_name = description.name_fn(obj)

    @property
    def is_connected(self):
        """Return true if the device is connected to the network."""
        return self._is_connected

    @property
    def hostname(self) -> str | None:
        """Return hostname of the device."""
        return self.entity_description.hostname_fn(self.controller.api, self._obj_id)

    @property
    def ip_address(self) -> str:
        """Return the primary ip address of the device."""
        return self.entity_description.ip_address_fn(self.controller.api, self._obj_id)

    @property
    def mac_address(self) -> str:
        """Return the mac address of the device."""
        return self._obj_id

    @property
    def source_type(self) -> SourceType:
        """Return the source type, eg gps or router, of the device."""
        return SourceType.ROUTER

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._attr_unique_id

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        description = self.entity_description
        handler = description.api_handler_fn(self.controller.api)
        self.async_on_remove(
            handler.subscribe(
                self.async_signalling_callback,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self.controller.signal_reachable,
                self.async_signal_reachable_callback,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self.controller.signal_options_update,
                self.options_updated,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self.controller.signal_remove,
                self.remove_item,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{self.controller.signal_heartbeat_missed}_{self.unique_id}",
                self._make_disconnected,
            )
        )
        if description.event_to_subscribe is not None:
            self.async_on_remove(
                self.controller.api.events.subscribe(
                    self.async_event_callback,
                    description.event_to_subscribe,
                )
            )

    @callback
    def _make_disconnected(self, *_):
        """No heart beat by device."""
        self._is_connected = False
        self.async_write_ha_state()

    @callback
    def async_signalling_callback(self, event: ItemEvent, obj_id: str) -> None:
        """Update the switch state."""
        if event == ItemEvent.DELETED and obj_id == self._obj_id:
            self.hass.async_create_task(self.remove_item({self._obj_id}))
            return

        description = self.entity_description
        if not description.supported_fn(self.controller.api, self._obj_id):
            self.hass.async_create_task(self.remove_item({self._obj_id}))
            return

        state_changed = False
        is_connected = self.entity_description.is_connected_fn(
            self.controller, self._obj_id
        )

        if self._controller_connection_state_changed:
            self._controller_connection_state_changed = False

            if self.controller.available:
                state_changed = self._is_connected and is_connected
            else:
                self.controller.async_heartbeat(self.unique_id)

        else:
            if not self._ignore_events:  # Prioritize normal data updates over events
                self._ignore_events = True
            state_changed = self._is_connected or is_connected

        if state_changed:
            self._is_connected = is_connected
            self.controller.async_heartbeat(
                self.unique_id,
                dt_util.utcnow()
                + self.entity_description.heartbeat_timedelta_fn(
                    self.controller, self._obj_id
                ),
            )

        self._attr_available = description.available_fn(self.controller, self._obj_id)
        self.async_write_ha_state()

    @callback
    def async_signal_reachable_callback(self) -> None:
        """Call when controller connection state change."""
        self.async_signalling_callback(ItemEvent.ADDED, self._obj_id)

    @callback
    def async_event_callback(self, event: Event) -> None:
        """Event subscription callback."""
        if event.mac != self._obj_id or self._ignore_events:
            return

        description = self.entity_description
        assert isinstance(description.event_to_subscribe, tuple)
        assert isinstance(description.event_is_on, tuple)

        if event.key in description.event_is_on:
            self.controller.async_heartbeat(self.unique_id)
            self._is_connected = True
            self.async_write_ha_state()
        else:
            self.controller.async_heartbeat(
                self.unique_id,
                dt_util.utcnow()
                + self.entity_description.heartbeat_timedelta_fn(
                    self.controller, self._obj_id
                ),
            )

    async def options_updated(self) -> None:
        """Config entry options are updated, remove entity if option is disabled."""
        if not self.entity_description.allowed_fn(self.controller, self._obj_id):
            await self.remove_item({self._obj_id})

    async def remove_item(self, keys: set) -> None:
        """Remove entity if object ID is part of set."""
        if self._obj_id not in keys or self._removed:
            return
        self._removed = True
        if self.registry_entry:
            er.async_get(self.hass).async_remove(self.entity_id)
        else:
            await self.async_remove(force_remove=True)
