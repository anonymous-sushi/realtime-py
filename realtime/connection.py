import asyncio
import json
import logging
from collections import defaultdict
from functools import wraps
from typing import Any, Callable
import urllib.parse as urlparse
from urllib.parse import urlencode

import websockets

from realtime.channel import Channel
from realtime.exceptions import NotConnectedError
from realtime.message import HEARTBEAT_PAYLOAD, PHOENIX_CHANNEL, ChannelEvents, Message

logging.basicConfig(
    format="%(asctime)s:%(levelname)s - %(message)s", level=logging.INFO)


def ensure_connection(func: Callable):
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any):
        if not args[0].connected:
            raise NotConnectedError(func.__name__)

        return func(*args, **kwargs)

    return wrapper

def appendParams(url, params):
    url_parts = list(urlparse.urlparse(url))
    query = dict(urlparse.parse_qsl(url_parts[4]))
    query.update(params)
    url_parts[4] = urlencode(query)
    return urlparse.urlunparse(url_parts)

class Socket:
    def __init__(self, url: str, params: dict = {}, hb_interval: int = 5) -> None:
        """
        `Socket` is the abstraction for an actual socket connection that receives and 'reroutes' `Message` according to its `topic` and `event`.
        Socket-Channel has a 1-many relationship.
        Socket-Topic has a 1-many relationship.
        :param url: Websocket URL of the Realtime server. starts with `ws://` or `wss://`
        :param params: Optional parameters for connection.
        :param hb_interval: WS connection is kept alive by sending a heartbeat message. Optional, defaults to 5.
        """
        self.url = url
        self.channels = defaultdict(list)
        self.connected = False
        self.params: dict = params
        if self.params:
            self.url = appendParams(url, params)
        self.hb_interval: int = hb_interval
        self.ws_connection: websockets.client.WebSocketClientProtocol
        self.kept_alive: bool = False

    # @ensure_connection
    # def listen(self) -> None:
    #     """
    #     Wrapper for async def _listen() to expose a non-async interface
    #     In most cases, this should be the last method executed as it starts an infinite listening loop.
    #     :return: None
    #     """
    #     loop = asyncio.get_running_loop()
    #     loop.run_until_complete(asyncio.gather(
    #         self._listen(), self._keep_alive()))

    async def listen(self) -> None:
        """
        An infinite loop that keeps listening.
        :return: None
        """
        async for msg in self.ws_connection:
            try:
                msg = Message(**json.loads(msg))
                if msg.event == ChannelEvents.reply:
                    continue
                for channel in self.channels.get(msg.topic, []):
                    for cl in channel.listeners:
                        if cl.event == msg.event:
                            await cl.callback(msg.payload)

            except websockets.exceptions.ConnectionClosed:
                print("Exception connection closed")
                logging.exception("Connection closed")
                break

    # async def connect(self) -> None:
    #     """
    #     Wrapper for async def _connect() to expose a non-async interface
    #     """
    #     loop = asyncio.get_running_loop()
    #     task = asyncio.create_task(self._connect())
    #     await task
    #     self.connected = True

    async def connect(self) -> None:
        ws_connection = await websockets.connect(self.url)
        if ws_connection.open:
            print("Connection was successful")
            self.ws_connection = ws_connection
            self.connected = True
        else:
            print("Failed connection")
            raise Exception("Connection Failed")

    async def disconnect(self) -> None:
        await self.ws_connection.close()
        print("Closed WS", self.ws_connection.open)
        self.connected = False

    async def status(self) -> None:
        return self.ws_connection.open

    async def _keep_alive(self) -> None:
        """
        Sending heartbeat to server every 5 seconds
        Ping - pong messages to verify connection is alive
        """
        while True:
            try:
                print("sending heartbeat")
                data = dict(
                    topic=PHOENIX_CHANNEL,
                    event=ChannelEvents.heartbeat,
                    payload=HEARTBEAT_PAYLOAD,
                    ref=None,
                )
                await self.ws_connection.send(json.dumps(data))
                await asyncio.sleep(self.hb_interval)
            except websockets.exceptions.ConnectionClosed as e:
                print("Connection with server closed", e)
                await self.connect()

    async def subscribe(self, topic) -> None:
        try:
            data = dict(
                topic=topic,
                event="phx_join",
                payload={},
                ref=None,
            )
            await self.ws_connection.send(json.dumps(data))
            print("subscribed")
        except websockets.exceptions.ConnectionClosed:
            print("Connection with server closed",e)

    @ensure_connection
    def set_channel(self, topic: str) -> Channel:
        """
        :param topic: Initializes a channel and creates a two-way association with the socket
        :return: Channel
        """

        chan = Channel(self, topic, self.params)
        self.channels[topic].append(chan)

        return chan

    def summary(self) -> None:
        """
        Prints a list of topics and event the socket is listening to
        :return: None
        """
        for topic, chans in self.channels.items():
            for chan in chans:
                print(
                    f"Topic: {topic} | Events: {[e for e, _ in chan.callbacks]}]")
