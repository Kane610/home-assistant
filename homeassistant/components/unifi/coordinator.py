"""UniFi Network data update coordinator."""

from datetime import timedelta
from typing import TYPE_CHECKING

from aiounifi.interfaces.api_handlers import APIHandler, ItemEvent, UnsubscribeType

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import LOGGER

if TYPE_CHECKING:
    from .hub.hub import UnifiHub

POLL_INTERVAL = timedelta(seconds=10)


class UnifiDataUpdateCoordinator[HandlerT: APIHandler](DataUpdateCoordinator[None]):
    """Coordinator managing websocket or polling updates for a UniFi API handler."""

    def __init__(
        self,
        hub: UnifiHub,
        handler: HandlerT,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hub.hass,
            LOGGER,
            name=f"UniFi {type(handler).__name__}",
            config_entry=hub.config.entry,
            update_interval=POLL_INTERVAL,
        )
        self._hub = hub
        self._handler = handler
        self._supports_websocket = bool(
            handler.process_messages or handler.remove_messages
        )
        self._websocket_unsub: UnsubscribeType | None = None
        self._update_mode()
        hub.config.entry.async_on_unload(
            async_dispatcher_connect(
                hub.hass, hub.signal_options_update, self._update_mode
            )
        )

    @property
    def handler(self) -> HandlerT:
        """Return the aiounifi handler managed by this coordinator."""
        return self._handler

    @property
    def _should_poll(self) -> bool:
        """Return if this coordinator should poll for updates."""
        return self.update_interval is not None

    async def async_request_refresh_after_control(self) -> None:
        """Refresh after a control call when polling is in use.

        In websocket mode, state updates are expected to arrive via pushed events.
        """
        if self._should_poll:
            await self.async_request_refresh()

    @callback
    def _update_mode(self) -> None:
        """Update websocket/polling mode based on hub configuration."""
        use_websocket_updates = (
            not self._hub.config.option_polling and self._supports_websocket
        )

        if use_websocket_updates and self._websocket_unsub is None:
            self._websocket_unsub = self._handler.subscribe(self._async_handle_update)

        if not use_websocket_updates and self._websocket_unsub is not None:
            self._websocket_unsub()
            self._websocket_unsub = None

        self.update_interval = None if use_websocket_updates else POLL_INTERVAL

    @callback
    def _async_handle_update(self, event: ItemEvent, obj_id: str) -> None:
        """Signal listeners when websocket updates are received."""
        self.async_set_updated_data(None)

    async def _async_update_data(self) -> None:
        """Update data from the API handler."""
        await self._handler.update()
