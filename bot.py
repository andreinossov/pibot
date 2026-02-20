import asyncio
import json
import logging
import os
import ssl
import aiohttp
import websockets
from aioice.candidate import Candidate
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pibot")

SIGNALING_URL = "wss://sig.piedpie.net"
BOT_EMAIL = "bot@piedpie.net"
BOT_ROOM_ID = BOT_EMAIL.lower().strip()[:30].encode().hex()
SAMPLE_VIDEO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.mp4")
REG_SERVER = "https://reg.piedpie.net"
ICE_TIMEOUT_SECONDS = 15


class CallSession:
    """Represents a single call with one peer."""

    def __init__(self, sender_id):
        self.sender_id = sender_id
        self.pc = None
        self.player = None
        self.ice_timeout_task = None

    async def close(self):
        if self.ice_timeout_task:
            self.ice_timeout_task.cancel()
            self.ice_timeout_task = None
        if self.pc:
            await self.pc.close()
            self.pc = None
        if self.player:
            self.player = None


class PikaloBot:
    def __init__(self):
        self.ws = None
        self.my_id = None
        self.ice_servers = []
        self.calls = {}  # sender_id -> CallSession

    async def fetch_turn_credentials(self):
        """Fetch ephemeral TURN credentials from the registration server."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{REG_SERVER}/turn-credentials?userId=pibot",
                    ssl=False
                ) as resp:
                    data = await resp.json()
                    if data.get("success") and data.get("iceServers"):
                        self.ice_servers = []
                        for server in data["iceServers"]:
                            urls = server.get("urls", [])
                            if isinstance(urls, str):
                                urls = [urls]
                            self.ice_servers.append(RTCIceServer(
                                urls=urls,
                                username=server.get("username"),
                                credential=server.get("credential")
                            ))
                        logger.info(f"Fetched TURN credentials ({len(self.ice_servers)} servers)")
                    else:
                        logger.warning(f"Failed to fetch TURN credentials: {data}")
        except Exception as e:
            logger.error(f"Error fetching TURN credentials: {e}")

    async def connect(self):
        await self.fetch_turn_credentials()

        url = f"{SIGNALING_URL}/{BOT_ROOM_ID}"
        logger.info(f"Connecting to signaling server: {url}")

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        self.ws = await websockets.connect(url, ssl=ssl_context)
        await self.ws.send(json.dumps({"type": "join"}))
        logger.info("Joined room: " + BOT_ROOM_ID)

        async for message in self.ws:
            data = json.loads(message)
            await self.handle_message(data)

    async def handle_message(self, msg):
        msg_type = msg.get("type")

        if msg_type == "ping":
            await self.ws.send(json.dumps({"type": "pong"}))
            return

        if msg_type == "connected":
            self.my_id = msg.get("payload", {}).get("userId")
            logger.info(f"My UUID: {self.my_id}")

        elif msg_type == "user-joined":
            sender_id = msg.get("payload", {}).get("userId") or msg.get("senderId") or msg.get("sender")
            if sender_id and self.my_id:
                logger.info(f"User joined: {sender_id}. Sending my-uuid.")
                await self.ws.send(json.dumps({
                    "type": "signal",
                    "target": sender_id,
                    "payload": {
                        "type": "my-uuid",
                        "uuid": self.my_id
                    }
                }))

        elif msg_type == "user-left":
            sender_id = msg.get("payload", {}).get("userId") or msg.get("senderId") or msg.get("sender")
            if sender_id and sender_id in self.calls:
                logger.info(f"User left: {sender_id}. Cleaning up call.")
                await self.cleanup_call(sender_id)

        elif msg_type == "signal":
            payload = msg.get("payload", {})
            signal_type = payload.get("type")
            sender_id = msg.get("sender") or msg.get("senderId")

            if signal_type == "offer":
                logger.info(f"Received offer from {sender_id}. Auto-accepting... (active calls: {len(self.calls)})")
                await self.accept_call(sender_id, payload.get("sdp"))

            elif signal_type == "ice-candidate":
                session = self.calls.get(sender_id)
                if session and session.pc:
                    candidate_dict = payload.get("candidate")
                    if not candidate_dict:
                        return

                    candidate_sdp = candidate_dict.get("candidate", "")
                    sdp_mid = candidate_dict.get("sdpMid", "0")
                    sdp_mline_index = candidate_dict.get("sdpMLineIndex", 0)

                    if not candidate_sdp:
                        return

                    try:
                        parsed = Candidate.from_sdp(candidate_sdp)
                        candidate = RTCIceCandidate(
                            component=parsed.component,
                            foundation=parsed.foundation,
                            ip=parsed.host,
                            port=parsed.port,
                            priority=parsed.priority,
                            protocol=parsed.transport,
                            type=parsed.type,
                            relatedAddress=parsed.related_address,
                            relatedPort=parsed.related_port,
                            sdpMid=sdp_mid,
                            sdpMLineIndex=sdp_mline_index
                        )
                        await session.pc.addIceCandidate(candidate)
                    except Exception as e:
                        logger.warning(f"Failed to add ICE candidate for {sender_id}: {e}")

            elif signal_type == "hangup":
                logger.info(f"Call hung up by {sender_id}")
                await self.cleanup_call(sender_id)

    async def accept_call(self, sender_id, sdp):
        # Clean up any existing call with this same sender
        if sender_id in self.calls:
            await self.cleanup_call(sender_id)

        session = CallSession(sender_id)
        self.calls[sender_id] = session

        # Create PC with TURN servers
        config = RTCConfiguration(iceServers=self.ice_servers) if self.ice_servers else RTCConfiguration()
        session.pc = RTCPeerConnection(configuration=config)
        logger.info(f"Created PeerConnection for {sender_id} (total calls: {len(self.calls)})")

        @session.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            state = session.pc.iceConnectionState
            logger.info(f"[{sender_id[:8]}] ICE: {state}")
            if state in ("connected", "completed"):
                logger.info(f"[{sender_id[:8]}] Call connected!")
                if session.ice_timeout_task:
                    session.ice_timeout_task.cancel()
                    session.ice_timeout_task = None
            elif state == "failed":
                logger.error(f"[{sender_id[:8]}] ICE failed")
                await self.cleanup_call(sender_id)
            elif state == "disconnected":
                # Peer disconnected — clean up after a short grace period
                await asyncio.sleep(3)
                if sender_id in self.calls and session.pc and session.pc.iceConnectionState == "disconnected":
                    logger.info(f"[{sender_id[:8]}] Peer disconnected, cleaning up")
                    await self.cleanup_call(sender_id)

        @session.pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                await self.ws.send(json.dumps({
                    "type": "signal",
                    "target": sender_id,
                    "payload": {
                        "type": "ice-candidate",
                        "candidate": {
                            "candidate": candidate.sdp,
                            "sdpMid": candidate.sdpMid,
                            "sdpMLineIndex": candidate.sdpMLineIndex
                        }
                    }
                }))

        # Add media tracks
        if os.path.exists(SAMPLE_VIDEO):
            session.player = MediaPlayer(SAMPLE_VIDEO)
            if session.player.video:
                session.pc.addTrack(session.player.video)
            if session.player.audio:
                session.pc.addTrack(session.player.audio)
            logger.info(f"[{sender_id[:8]}] Added media tracks")
        else:
            logger.warning("sample.mp4 not found — no media")

        # Handle offer
        offer = RTCSessionDescription(sdp=sdp["sdp"], type=sdp["type"])
        await session.pc.setRemoteDescription(offer)

        # Create and send answer
        answer = await session.pc.createAnswer()
        await session.pc.setLocalDescription(answer)
        await self.ws.send(json.dumps({
            "type": "signal",
            "target": sender_id,
            "payload": {
                "type": "answer",
                "sdp": {
                    "type": session.pc.localDescription.type,
                    "sdp": session.pc.localDescription.sdp
                },
                "identity": {
                    "username": "WebRTC Bot",
                    "picture": ""
                }
            }
        }))
        logger.info(f"[{sender_id[:8]}] Sent answer")

        # ICE timeout
        async def ice_timeout():
            await asyncio.sleep(ICE_TIMEOUT_SECONDS)
            if sender_id in self.calls and session.pc and session.pc.iceConnectionState not in ("connected", "completed", "closed"):
                logger.warning(f"[{sender_id[:8]}] ICE timeout — cleaning up")
                await self.cleanup_call(sender_id)

        session.ice_timeout_task = asyncio.create_task(ice_timeout())

    async def cleanup_call(self, sender_id):
        session = self.calls.pop(sender_id, None)
        if session:
            await session.close()
            logger.info(f"[{sender_id[:8]}] Cleaned up (remaining calls: {len(self.calls)})")


async def main():
    bot = PikaloBot()
    while True:
        try:
            await bot.connect()
        except Exception as e:
            logger.error(f"Connection error: {e}. Retrying in 5s...")
            # Clean up all calls on disconnect
            for sid in list(bot.calls.keys()):
                await bot.cleanup_call(sid)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
