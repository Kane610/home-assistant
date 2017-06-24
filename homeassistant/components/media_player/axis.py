"""
Play media via gstreamer.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.gstreamer/
"""
import logging

import voluptuous as vol

from homeassistant.components.media_player import (
    MEDIA_TYPE_MUSIC, SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_SET,
    SUPPORT_PAUSE, SUPPORT_STOP, SUPPORT_PLAY_MEDIA,
    PLATFORM_SCHEMA, MediaPlayerDevice)
from homeassistant.const import (
    STATE_IDLE, CONF_NAME, EVENT_HOMEASSISTANT_STOP)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect


_LOGGER = logging.getLogger(__name__)


DOMAIN = 'axis'

SUPPORT_AXIS = SUPPORT_STOP | SUPPORT_PLAY_MEDIA
#SUPPORT_AXIS = SUPPORT_STOP | SUPPORT_PAUSE | SUPPORT_PLAY_MEDIA

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME): cv.string,
})


# pylint: disable=unused-argument
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup the Gstreamer platform."""
    config = {'alias': 'alias',
              'name': 'axis',
              'host': '10.0.1.20',
              'username': 'root',
              'password': 'pass'}

    # def _shutdown(call):
    #     """Quit the player on shutdown."""
    #     player.quit()

    # hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, _shutdown)
    add_devices([AxisDeviceSpeaker(hass, config)])


class AxisDeviceSpeaker(MediaPlayerDevice):
    """Representation of a Gstreamer device."""

    def __init__(self, hass, config):
        """Initialize the Gstreamer device."""
        self._speaker = speaker(config)
        self._speaker.signal_parent = self._update_callback
        self._name = config['name']
        self._state = STATE_IDLE
        async_dispatcher_connect(hass,
                                 DOMAIN + '_' + config[CONF_NAME] + '_new_ip',
                                 self._new_ip)

    def _update_callback(self):
        """Update the speaker's state, if needed."""
        self.update()
        self.schedule_update_ha_state()

    def update(self):
        """Update properties."""
        self._state = self._speaker.state

    def play_media(self, media_type, media_id, **kwargs):
        """Play media."""
        if media_type != MEDIA_TYPE_MUSIC:
            _LOGGER.error('invalid media type')
            return
        self._speaker.play(media_id)

    def media_stop(self):
        """Send stop command."""
        self._speaker.stop()

    def media_pause(self):
        """Send stop command."""
        self.media_stop()

    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_AXIS

    @property
    def state(self):
        """Return the state of the player."""
        return self._state

    def _new_ip(self, host):
        """Set new IP for speaker."""
        self._speaker.url = host

#########################

import urllib
import urllib.request

import gi  # pylint: disable=import-error
gi.require_version('Gst', '1.0')
from gi.repository import Gst

# Gst.debug_set_active(True)
# Gst.debug_set_default_threshold(4)

Gst.init(None)

STATE_IDLE = 'idle'
STATE_PLAYING = 'playing'
STATE_PAUSED = 'paused'
STATE_STOPPED = 'stopped'

MAX_RETRIES = 10


class speaker(object):
    def __init__(self, config):
        self._url = config['host']
        self._username = config['username']
        self._password = config['password']
        self._state = STATE_IDLE
        self.signal_parent = None
        self._set_up_pipeline()
        self._got_error = False
        self.retries = 0

    def _set_up_pipeline(self):
        """Set up pipeline describing how gstreamer will be configured."""
        uri = '{0}:{1}@{2}'.format(self._username, self._password, self._url)
        pipeline = [
            'audioconvert', '!',
            'audioresample', '!',
            'audio/x-raw,rate=8000', '!',
            'mulawenc', '!',
            'curlhttpsink',
            'content-type=audio/basic',
            'location=http://%s/axis-cgi/audio/transmit.cgi' % (uri),
            'use-content-length=true',
            'sync=false',
        ]
        self._pipeline = " ".join(pipeline)

    def play(self, media_id):
        self.media_id = media_id
        self.set_up_stream(media_id)
        self.start()

    def set_up_stream(self, media_id):
        self.prepare_file(media_id)

        pipeline_string = self._pipeline
        self._player = Gst.ElementFactory.make('playbin', 'player')
        sink = Gst.parse_bin_from_description(pipeline_string, True)
        self._player.set_property('audio-sink', sink)
        local_path, _ = urllib.request.urlretrieve(media_id)
        print(local_path)
        self._player.set_property('uri', 'file://{}'.format(local_path))
        #  Set up message handler
        bus = self._player.get_bus()
        bus.set_sync_handler(self._on_message, self._player)

    def set_up_stream2(self, media_id):
        """Configure gstreamer with pipeline and appsink."""
        pipeline_string = 'playbin ' + self._pipeline
        # Simplest way to create a pipeline
        self._player = Gst.parse_launch(pipeline_string)
        #  Set up message handler
        bus = self._stream.get_bus()
        bus.set_sync_handler(self._on_message, self._player)

    def prepare_file(self, media_id):
        """Blah"""
        local_path, _ = urllib.request.urlretrieve(media_id)
        pipeline = [
            'filesrc location=%s' % (local_path), '!'
            'audioconvert', '!',
            'audioresample', '!',
            'audio/x-raw,rate=8000', '!',
            'mulawenc', '!',
            'filesink location=%s' % ('/tmp/axis_tts')
        ]
        pipeline_string = " ".join(pipeline)
        convert = Gst.parse_launch(pipeline_string)
        convert.set_state(Gst.State.PLAYING)

    def start(self):
        """Start pipeline."""
        if self.state in [STATE_IDLE, STATE_PAUSED]:
            self._player.set_state(Gst.State.PLAYING)
            self.state = STATE_PLAYING
            _LOGGER.info("Stream started")

    def stop(self):
        """Stop pipeline."""
        if self.state in [STATE_PLAYING, STATE_PAUSED]:
            self._player.set_state(Gst.State.NULL)
            self.state = STATE_STOPPED
            self.state = STATE_IDLE
            _LOGGER.info("Stream stopped")

    def pause(self):
        """Pause pipeline."""
        if self.state == STATE_PLAYING:
            self._player.set_state(Gst.State.PAUSED)
            self.state = STATE_PAUSED
            _LOGGER.info("Stream paused")

    def retry(self):
        """Retry if we get an error."""
        if self.state == STATE_PLAYING:
            self.retries = self.retries + 1
            self.set_up_stream(self.media_id)
            self._player.set_state(Gst.State.PLAYING)
            _LOGGER.debug('Connection error, retrying')

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, state):
        self._state = state
        if self.signal_parent is not None:
            self.signal_parent()
        print(state)

    @property
    def url(self):
        return self._url

    @url.setter
    def url(self, url):
        """Update url of device."""
        self._url = url
        self._set_up_pipeline()
        _LOGGER.debug("New IP (%s) set for speaker %s", self.url, self.name)

    def _on_message(self, bus, message, pipeline):
        """When a message is received from Gstreamer."""
        if message.type == Gst.MessageType.EOS:
            print('eos')
            if self._got_error and self.retries < MAX_RETRIES:
                self.retry()
            else:
                self.state = STATE_IDLE
                self.retries = 0
                _LOGGER.debug('Speaker got EOS')
            self._got_error = False

        elif message.type == Gst.MessageType.ERROR:
            self._got_error = True
            err, _ = message.parse_error()
            _LOGGER.debug('Speaker error: %s', err)
            print('error', err)

        else:
            _LOGGER.debug('Speaker bus message type: %s', message.type)

        return Gst.BusSyncReply.PASS

# curlbasesink
# gstcurlbasesink.c:391:gst_curl_base_sink_render:<curlhttpsink0> 
# failed to transfer data: Failed sending data to the peer