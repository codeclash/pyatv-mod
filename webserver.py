"""Simple json api client implemented in pyatv"""

import os
import datetime
import traceback
from enum import Enum
from ipaddress import ip_address

import logging
import asyncio
from aiohttp import WSMsgType, web

import pyatv
from pyatv.interface import (
    App,
    Apps,
    Audio,
    DeviceListener,
    Playing,
    Power,
    PowerListener,
    PushListener,
    RemoteControl,
    Stream,
    retrieve_commands,
)

from aiohttp_swagger import *

DEVICES = """
<script>
setTimeout(function(){
   window.location.reload(1);
}, 5 * 1000);
</script>
<div id="devices">DEVICES</div>
"""


STATE = """
<script>
let socket = new WebSocket('ws://' + location.host + '/ws/DEVICE_ID');
socket.onopen = function(e) {
  document.getElementById('status').innerText = 'Connected!';
};
socket.onmessage = function(event) {
  document.getElementById('state').innerText = event.data;
};
socket.onclose = function(event) {
  if (event.wasClean) {
    document.getElementById('status').innerText = 'Connection closed cleanly!';
  } else {
    document.getElementById('status').innerText = 'Disconnected due to error!';
  }
  document.getElementById('state').innerText = "";
};
socket.onerror = function(error) {
  document.getElementById('status').innerText = 'Failed to connect!';
};
</script>
<div id="status">Connecting...</div>
<div id="state"></div>
"""


class PushPrinter(DeviceListener, PushListener, PowerListener):
    """Listener for device and push updates events."""

    def __init__(self, app, identifier):
        """Initialize a new PushPrinter."""
        self.app = app
        self.identifier = identifier

    def connection_lost(self, exception: Exception) -> None:
        """Call when connection was lost."""
        self._remove()
        payload = output(False, exception=exception, values={"connection": "lost"})
        self._send_json(payload)

    def connection_closed(self) -> None:
        """Call when connection was closed."""
        self._remove()
        payload = output(True, values={"connection": "closed"})
        self._send_json(payload)

    def _remove(self):
        self.app["atv"].pop(self.identifier)
        self.app["listeners"].remove(self)

    def _send_json(self, payload):
        clients = self.app["clients"].get(self.identifier, [])
        for client in clients:
            asyncio.ensure_future(client.send_json(payload))

    def playstatus_update(self, updater, playstatus: Playing) -> None:
        """Call when play status was updated."""
        atv = self.app["atv"][self.identifier]
        payload = output_playing(playstatus, atv.metadata.app)
        self._send_json(payload)

    def playstatus_error(self, updater, exception: Exception) -> None:
        """Call when an error occurred."""
        payload = output(False, exception=exception)
        self._send_json(payload)

    def powerstate_update(self, old_state, new_state):
        """Call when power state was updated."""
        payload = output(True, values={"power_state": new_state.name.lower()})
        self._send_json(payload)


def output(success: bool, error=None, exception=None, values=None):
    """Produce output in intermediate format before conversion"""
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()
    result = {"result": "success" if success else "failure", "datetime": str(now)}
    if error:
        result["error"] = error
    if exception:
        result["exception"] = str(exception)
        result["stacktrace"] = "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
    if values:
        result.update(**values)
    return result


def output_playing(playing: Playing, app: App):
    """Produce output for what is currently playing."""

    def _convert(field):
        if isinstance(field, Enum):
            return field.name.lower()
        return field if field else None

    commands = retrieve_commands(Playing)
    values = {k: _convert(getattr(playing, k)) for k in commands}
    if app:
        values["app"] = app.name
        values["app_id"] = app.identifier
    else:
        values["app"] = None
        values["app_id"] = None
    return output(True, values=values)


def web_command(method):
    """Decorate a web request handler."""

    async def _handler(request):
        device_id = request.match_info["id"]
        atv = request.app["atv"].get(device_id)
        if not atv:
            return web.json_response(
                output(
                    False,
                    error=f"Not connected to {device_id}",
                    values={"connection": "empty"}
                )
            )
        return await method(request, atv)

    return _handler


def add_credentials(config, query):
    """Add credentials to pyatv device configuration."""
    for service in config.services:
        proto_name = service.protocol.name.lower()
        if proto_name in query:
            config.set_credentials(service.protocol, query.get(proto_name))

    for service in config.services:
        if service.credentials:
          return True

    return False


routes = web.RouteTableDef()


@routes.get("/version")
async def version(request):
    """
    ---
    description: Get version of pyatv
    tags:
    - version
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    return web.json_response(
        output(True, values={"version": pyatv.const.__version__})
    )


@routes.get("/devices")
async def devices(request):
    """
    ---
    description: List devices
    tags:
    - devices
    produces:
    - text/html
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    devices = []
    for device in request.app["atv"]:
        devices.append(
            f"<a href='/state/{device}' target='_blank'>{device}</a>"
        )
    if devices:
        devices = str("</br>".join(devices))
    else:
        devices = str("Empty devices list")
    return web.Response(
        text=DEVICES.replace("DEVICES", devices),
        content_type="text/html",
    )


@routes.get("/state/{id}")
async def state(request):
    """
    ---
    description: Handle request to receive push updates
    tags:
    - state
    produces:
    - text/html
    parameters:
      - in: query
        name: id
        description: deviceId
        required: true
        schema:
          type: string
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """
    
    return web.Response(
        text=STATE.replace("DEVICE_ID", request.match_info["id"]),
        content_type="text/html",
    )


@routes.get("/scan/")
async def scan(request):
    """
    ---
    description: Scan for devices.
    tags:
    - scan
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    def _convert(hosts):
        if hosts:
            ip_split = hosts.split(",")
            return [ip_address(ip) for ip in ip_split]
        return None

    hosts = _convert(request.query.get("hosts"))
    atvs = []
    for atv in await pyatv.scan(loop=asyncio.get_event_loop(), hosts=hosts):
        services = []
        for service in atv.services:
            services.append(
                {"protocol": service.protocol.name.lower(), "port": service.port}
            )
        atvs.append(
            {
                "name": atv.name,
                "address": str(atv.address),
                "identifier": atv.identifier,
                "services": services,
            }
        )
    return web.json_response(output(True, values={"devices": atvs}))

@routes.get("/connect/{id}")
async def connect(request):
    """
    ---
    description: Connect to a device
    tags:
    - connect
    produces:
    - text/json
    parameters:
      - in: path
        name: id
        description: deviceId
        required: true
        schema:
          type: string
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    loop = asyncio.get_event_loop()
    device_id = request.match_info["id"]
    if device_id in request.app["atv"]:
        return web.json_response(
            output(True, values={"connection": "connected"})
        )

    options = {}
    if ip_address(device_id):
        options["hosts"] = [device_id]
    else:
        options["identifier"] = device_id
    results = await pyatv.scan(loop=loop, **options)
    if not results:
        return web.json_response(output(False, error="Device not found"))

    if not add_credentials(results[0], request.query):
      return web.json_response(
          output(False, error="Failed to connect device, empty Credentials")
      )

    try:
        atv = await pyatv.connect(results[0], loop=loop)
    except Exception as ex:
        return web.json_response(
            output(False, error="Failed to connect device", exception=ex)
        )

    push_listener  = PushPrinter(request.app, device_id)

    atv.power.listener = push_listener
    atv.listener = push_listener
    atv.push_updater.listener = push_listener
    atv.push_updater.start()
    request.app["listeners"].append(push_listener)

    # cache it
    request.app["atv"][device_id] = atv
    return web.json_response(output(True, values={"connection": "connected"}))


## _________________________________________________________________________________________________________
@routes.get("/playfile/{id}")
async def playfile(request):
    """
    ---
    description: Connect to a device
    tags:
    - playfile
    produces:
    - text/json
    parameters:
      - in: path
        name: id
        description: device_id
        required: true
        schema:
          type: string
      - in: query
        name: url
        description: url of mp3
        required: true
        schema:
          type: string
      - in: query
        name: volumepercent
        description: volume in percent [0.0 - 100.0]
        required: false
        schema:
          type: number
    responses:
        "200":
            description: successful operation. Played url on device
        "405":
            description: invalid HTTP Method
    """

    loop = asyncio.get_event_loop()

    device_id = request.match_info["id"]
    url = request.rel_url.query['url']
    volumepercent = request.rel_url.query['volumepercent']
    volume = None

    if volumepercent is not None:
        volume = float(volumepercent)

    atv = None

    if device_id in request.app["atv"]:
        atv = request.app["atv"][device_id]
    else:
        atvs = await pyatv.scan(loop, identifier=device_id, timeout=5)        
        if not atvs:
            return web.json_response(
                output(False, error="Device not found"))
            
        conf = atvs[0]
        atv = await pyatv.connect(conf, loop)

    # listener = PushUpdatePrinter()
    # atv.push_updater.listener = listener
    # atv.push_updater.start()

    try:
        previousVolume = atv.audio.volume

        """
        if volume is not None:
            await atv.audio.set_volume(volume)
            await asyncio.sleep(1)
        """

        await atv.stream.stream_file(url)
        await asyncio.sleep(1)

        """
        if volume is not None:
            await atv.audio.set_volume(previousVolume)
        await asyncio.sleep(1)
        """
    except Exception as ex:
        return web.json_response(
            output(False, error="Error", exception=ex)
        )
    finally:
        atv.close()

    return web.json_response(output(True, values={"played": url}))
## _________________________________________________________________________________________________________



##############################################################################################################
#http://192.168.1.98:8080/playit/192.168.1.21/{url}?address=192.168.1.214&url=%2Fhome%2Fjingle.mp3

@routes.get("/playsound/{id}/{url}")
async def playsound(request):
    """
    ---
    description: Connect to a device
    tags:
    - playsound
    produces:
    - text/json
    parameters:
      - in: path
        name: id
        description: device_id
        required: true
        schema:
          type: string
      - in: path
        name: url
        description: url of mp3
        required: true
        schema:
          type: string
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    loop = asyncio.get_event_loop()

    device_id = request.match_info["id"]
    url = request.match_info["url"]

    atv = None

    if device_id in request.app["atv"]:
        atv = request.app["atv"][device_id]
    else:
        atvs = await pyatv.scan(loop, identifier=device_id, timeout=5)        
        if not atvs:
            return web.json_response(
                output(False, error="Device not found"))
            
        conf = atvs[0]
        atv = await pyatv.connect(conf, loop)

    # listener = PushUpdatePrinter()
    # atv.push_updater.listener = listener
    # atv.push_updater.start()

    try:
        #print("* Starting to stream", filename)
        await atv.stream.stream_file(url)
        await asyncio.sleep(1)
    finally:
        atv.close()

    return web.json_response(output(True, values={"played": "jingle"}))

# ******************************************************************************

##############################################################################################################

@routes.get("/close/{id}")
@web_command
async def close_connection(request, atv):
    """
    ---
    description: Close connection
    tags:
    - close
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    atv.close()
    return web.json_response(output(True, values={"connection": "closed"}))



@routes.get("/setvolume/{id}/{volumepercent}")
#@web_command
async def command(request):
    """
    ---
    description: set volume
    tags:
    - setvolume
    produces:
    - text/json
    parameters:
      - in: path
        name: id
        description: deviceId
        required: true
        schema:
          type: string
      - in: path
        name: volumepercent
        description: volumepercent
        required: true
        schema:
          type: number
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    loop = asyncio.get_event_loop()
        
    device_id = request.match_info["id"]
    volumepercent = request.match_info["volumepercent"]
    level = float(volumepercent)
    
    atv = None
    
    if device_id in request.app["atv"]:
        atv = request.app["atv"][device_id]
    else:
        atvs = await pyatv.scan(loop, identifier=device_id, timeout=5)        
        if not atvs:
            return web.json_response(
                output(False, error="Device not found"))
            
        conf = atvs[0]
        atv = await pyatv.connect(conf, loop)
    
    try:
        #currentVolme = atv.audio.volume
        await atv.audio.set_volume(level)
    finally:
        atv.close()

    return web.json_response(output(True, values={"volume": volumepercent}))

        


@routes.get("/playing/{id}")
@web_command
async def playing(request, atv):
    """
    ---
    description: current play status
    tags:
    - playing
    produces:
    - text/json
    responses:
        "200":
            description: successful operation. Return "pong" text
        "405":
            description: invalid HTTP Method
    """

    try:
        playstatus = await atv.metadata.playing()
    except Exception as ex:
        return web.json_response(
            output(False, error="Remote control command failed", exception=ex)
        )
    return web.json_response(output_playing(playstatus, atv.metadata.app))


@routes.get("/ws/{id}")
@web_command
async def websocket_handler(request, atv):
    """Handle incoming websocket requests."""
    device_id = request.match_info["id"]

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["clients"].setdefault(device_id, []).append(ws)

    playstatus = await atv.metadata.playing()
    await ws.send_json(output_playing(playstatus, atv.metadata.app))

    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            # Handle custom commands from client here
            if msg.data == "close":
                await ws.close()
        elif msg.type == WSMsgType.ERROR:
            print(f"Connection closed with exception: {ws.exception()}")

    request.app["clients"][device_id].remove(ws)

    return ws


async def on_shutdown(app: web.Application) -> None:
    """Call when application is shutting down."""
    for atv in app["atv"].values():
        atv.close()


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = os.environ.get("PORT", 8080)

    access_log = logging.getLogger('aiohttp.access')
    access_log.setLevel(logging.INFO)
    access_log.addHandler(logging.StreamHandler())
    access_log_format = '%a %t "%r" %s %b "%{User-Agent}i" %Tfsec'

    app = web.Application()
    app["atv"] = {}
    app["listeners"] = []
    app["clients"] = {}

    app.add_routes(routes)
    app.on_shutdown.append(on_shutdown)
    setup_swagger(app)
    web.run_app(
        app, host=host, port=port,
        access_log=access_log,
        access_log_format=access_log_format
    )


if __name__ == "__main__":
    main()
