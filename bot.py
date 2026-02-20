import asyncio
import json
import logging
import os
import ssl
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.contrib.media import MediaPlayer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pibot")

SIGNALING_URL = "wss://sig.piedpie.net"
BOT_EMAIL = "bot@piedpie.net"
BOT_ROOM_ID = BOT_EMAIL.lower().strip()[:30].encode().hex()
SAMPLE_VIDEO = "sample.mp4"

class PikaloBot:
    def __init__(self):
        self.pc = None
        self.ws = None
        self.my_id = None
        self.player = None

    async def connect(self):
        url = f"{SIGNALING_URL}/{BOT_ROOM_ID}"
        logger.info(f"Connecting to signaling server: {url}")
        
        # Disable SSL verification if needed (common in dev environments)
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
            # Reply to server keepalive pings to prevent connection timeout
            await self.ws.send(json.dumps({"type": "pong"}))
            return

        logger.info(f"Received message: {msg_type}")

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

        elif msg_type == "signal":
            payload = msg.get("payload", {})
            signal_type = payload.get("type")
            sender_id = msg.get("sender") or msg.get("senderId")

            if signal_type == "offer":
                # Ignore duplicate offers while a call is active (race condition protection)
                if self.pc and self.pc.iceConnectionState not in ("closed", "failed", "disconnected"):
                    logger.info(f"Ignoring duplicate offer from {sender_id} -- active PC in state {self.pc.iceConnectionState}")
                else:
                    logger.info(f"Received offer from {sender_id}. Auto-accepting...")
                    await self.accept_call(sender_id, payload.get("sdp"))

            elif signal_type == "ice-candidate":
                if self.pc:
                    candidate_dict = payload.get("candidate")
                    if not candidate_dict:
                        return
                    # Skip candidates with missing required fields
                    ip = candidate_dict.get("address") or candidate_dict.get("ip")
                    foundation = candidate_dict.get("foundation")
                    if not foundation or not ip:
                        logger.debug("Skipping ICE candidate with missing fields")
                        return
                    try:
                        # aiortc expects camelCase kwargs
                        candidate = RTCIceCandidate(
                            component=candidate_dict.get("component"),
                            foundation=foundation,
                            ip=ip,
                            port=candidate_dict.get("port"),
                            priority=candidate_dict.get("priority"),
                            protocol=candidate_dict.get("protocol"),
                            type=candidate_dict.get("type"),
                            relatedAddress=candidate_dict.get("relatedAddress"),
                            relatedPort=candidate_dict.get("relatedPort"),
                            sdpMid=candidate_dict.get("sdpMid"),
                            sdpMLineIndex=candidate_dict.get("sdpMLineIndex")
                        )
                        await self.pc.addIceCandidate(candidate)
                        logger.info("Added ICE candidate")
                    except Exception as e:
                        logger.warning(f"Failed to add ICE candidate: {e}")

            elif signal_type == "hangup":
                logger.info("Call hung up by peer")
                await self.cleanup()

    async def accept_call(self, sender_id, sdp):
        if self.pc:
            await self.cleanup()

        self.pc = RTCPeerConnection()
        
        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info(f"ICE connection state: {self.pc.iceConnectionState}")
            if self.pc.iceConnectionState == "failed":
                await self.cleanup()

        @self.pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                logger.info(f"Sending ICE candidate to {sender_id}")
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

        # Add video track
        if os.path.exists(SAMPLE_VIDEO):
            self.player = MediaPlayer(SAMPLE_VIDEO)
            if self.player.video:
                self.pc.addTrack(self.player.video)
                logger.info("Added video track from sample.mp4")
            if self.player.audio:
                self.pc.addTrack(self.player.audio)
                logger.info("Added audio track from sample.mp4")
        else:
            logger.warning("sample.mp4 not found. Sending no media.")

        # Handle offer
        offer = RTCSessionDescription(sdp=sdp["sdp"], type=sdp["type"])
        await self.pc.setRemoteDescription(offer)
        
        # Create answer
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)

        # Send answer
        await self.ws.send(json.dumps({
            "type": "signal",
            "target": sender_id,
            "payload": {
                "type": "answer",
                "sdp": {
                    "type": self.pc.localDescription.type,
                    "sdp": self.pc.localDescription.sdp
                },
                "identity": {
                    "username": "WebRTC Bot",
                    "picture": ""
                }
            }
        }))
        logger.info("Sent answer")

    async def cleanup(self):
        if self.pc:
            await self.pc.close()
            self.pc = None
        if self.player:
            # MediaPlayer doesn't have a close, but it stops when tracks are removed
            self.player = None
        logger.info("Cleaned up call state")

async def main():
    bot = PikaloBot()
    while True:
        try:
            await bot.connect()
        except Exception as e:
            logger.error(f"Connection error: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
