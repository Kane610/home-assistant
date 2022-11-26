"""Sensor platform for UniFi Network integration.

Support for bandwidth sensors of network clients.
Support for uptime sensors of network clients.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic

import aiounifi
from aiounifi.interfaces.api_handlers import ItemEvent
from aiounifi.interfaces.clients import Clients
from aiounifi.models.client import Client

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DATA_MEGABYTES
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN as UNIFI_DOMAIN
from .controller import UniFiController
from .entity import DataT, HandlerT, UnifiEntity, UnifiEntityDescription


@callback
def async_client_rx_value_fn(controller: UniFiController, client: Client) -> float:
    """Calculate if all apps are enabled."""
    if client.mac not in controller.wireless_clients:
        return client.wired_rx_bytes_r / 1000000
    return client.rx_bytes_r / 1000000


@callback
def async_client_tx_value_fn(controller: UniFiController, client: Client) -> float:
    """Calculate if all apps are enabled."""
    if client.mac not in controller.wireless_clients:
        return client.wired_tx_bytes_r / 1000000
    return client.tx_bytes_r / 1000000


@callback
def async_client_uptime_value_fn(
    controller: UniFiController, client: Client
) -> datetime:
    """Calculate the uptime of the client."""
    if client.uptime < 1000000000:
        return dt_util.now() - timedelta(seconds=client.uptime)
    return dt_util.utc_from_timestamp(float(client.uptime))


@callback
def async_client_device_info_fn(api: aiounifi.Controller, obj_id: str) -> DeviceInfo:
    """Create device registry entry for client."""
    client = api.clients[obj_id]
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, obj_id)},
        default_manufacturer=client.oui,
        default_name=client.name or client.hostname,
    )


@dataclass
class UnifiEntityLoader(Generic[HandlerT, DataT]):
    """Validate and load entities from different UniFi handlers."""

    value_fn: Callable[[UniFiController, DataT], datetime | float]


@dataclass
class UnifiSensorEntityDescription(
    SensorEntityDescription,
    UnifiEntityDescription[HandlerT, DataT],
    UnifiEntityLoader[HandlerT, DataT],
):
    """Class describing UniFi sensor entity."""


ENTITY_DESCRIPTIONS: tuple[UnifiSensorEntityDescription, ...] = (
    UnifiSensorEntityDescription[Clients, Client](
        key="Bandwidth sensor RX",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=DATA_MEGABYTES,
        has_entity_name=True,
        allowed_fn=lambda controller, _: controller.option_allow_bandwidth_sensors,
        api_handler_fn=lambda api: api.clients,
        available_fn=lambda controller, _: controller.available,
        device_info_fn=async_client_device_info_fn,
        event_is_on=None,
        event_to_subscribe=None,
        name_fn=lambda _: "RX",
        object_fn=lambda api, obj_id: api.clients[obj_id],
        supported_fn=lambda controller, _: controller.option_allow_bandwidth_sensors,
        unique_id_fn=lambda obj_id: f"rx-{obj_id}",
        value_fn=async_client_rx_value_fn,
    ),
    UnifiSensorEntityDescription[Clients, Client](
        key="Bandwidth sensor TX",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=DATA_MEGABYTES,
        has_entity_name=True,
        allowed_fn=lambda controller, _: controller.option_allow_bandwidth_sensors,
        api_handler_fn=lambda api: api.clients,
        available_fn=lambda controller, _: controller.available,
        device_info_fn=async_client_device_info_fn,
        event_is_on=None,
        event_to_subscribe=None,
        name_fn=lambda _: "TX",
        object_fn=lambda api, obj_id: api.clients[obj_id],
        supported_fn=lambda controller, _: controller.option_allow_bandwidth_sensors,
        unique_id_fn=lambda obj_id: f"tx-{obj_id}",
        value_fn=async_client_tx_value_fn,
    ),
    UnifiSensorEntityDescription[Clients, Client](
        key="Uptime sensor",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        has_entity_name=True,
        allowed_fn=lambda controller, _: controller.option_allow_uptime_sensors,
        api_handler_fn=lambda api: api.clients,
        available_fn=lambda controller, obj_id: controller.available,
        device_info_fn=async_client_device_info_fn,
        event_is_on=None,
        event_to_subscribe=None,
        name_fn=lambda client: "Uptime",
        object_fn=lambda api, obj_id: api.clients[obj_id],
        supported_fn=lambda controller, _: controller.option_allow_uptime_sensors,
        unique_id_fn=lambda obj_id: f"uptime-{obj_id}",
        value_fn=async_client_uptime_value_fn,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for UniFi Network integration."""
    controller = hass.data[UNIFI_DOMAIN][config_entry.entry_id]

    @callback
    def async_load_entities(description: UnifiSensorEntityDescription) -> None:
        """Load and subscribe to UniFi devices."""
        entities: list[SensorEntity] = []
        api_handler = description.api_handler_fn(controller.api)

        @callback
        def async_create_entity(event: ItemEvent, obj_id: str) -> None:
            """Create UniFi entity."""
            if not description.allowed_fn(
                controller, obj_id
            ) or not description.supported_fn(controller, obj_id):
                return

            entity = UnifiSensorEntity(obj_id, controller, description)
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


class UnifiSensorEntity(UnifiEntity, SensorEntity):
    """Base representation of a UniFi switch."""

    entity_description: UnifiSensorEntityDescription

    @callback
    def async_initiate_state(self) -> None:
        """Initiate entity state.

        Initiate native_value.
        """
        obj_id = self._obj_id
        controller = self.controller
        description = self.entity_description

        obj = description.object_fn(controller.api, obj_id)
        self._attr_native_value = description.value_fn(controller, obj)

    @callback
    def async_update_state(self, event: ItemEvent, obj_id: str) -> None:
        """Update entity state.

        Update native_value.
        """
        description = self.entity_description
        if not description.supported_fn(self.controller, self._obj_id):
            self.hass.async_create_task(self.remove_item({self._obj_id}))
            return

        obj = description.object_fn(self.controller.api, self._obj_id)
        if (value := description.value_fn(self.controller, obj)) != self.native_value:
            self._attr_native_value = value
