import asyncio
import json
import logging
import ssl
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tester")

SIGNALING_URL = "wss://sig.piedpie.net"
TARGET_ROOM_ID = "pibot"

async def test_call():
    url = f"{SIGNALING_URL}/tester"
    logger.info(f"Connecting to signaling server as tester: {url}")
    
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with websockets.connect(url, ssl=ssl_context) as ws:
        await ws.send(json.dumps({"type": "join"}))
        
        # Connect to target room to find the bot
        target_url = f"{SIGNALING_URL}/{TARGET_ROOM_ID}"
        async with websockets.connect(target_url, ssl=ssl_context) as target_ws:
            await target_ws.send(json.dumps({"type": "join"}))
            logger.info(f"Joined target room: {TARGET_ROOM_ID}")

            bot_uuid = None
            
            # Wait for bot to announce itself or receive user-joined
            async for message in target_ws:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "signal" and data.get("payload", {}).get("type") == "my-uuid":
                    bot_uuid = data["payload"]["uuid"]
                    logger.info(f"Found bot UUID: {bot_uuid}")
                    break
                elif msg_type == "user-joined":
                    # If the bot is already there, we might not get user-joined
                    # But if we join, the bot should send my-uuid signal to us
                    pass

            if not bot_uuid:
                logger.error("Could not find bot UUID")
                return

            # Initiate WebRTC
            pc = RTCPeerConnection()
            
            @pc.on("track")
            def on_track(track):
                logger.info(f"Received track: {track.kind}")

            # Create offer
            pc.addTransceiver("video", direction="recvonly")
            pc.addTransceiver("audio", direction="recvonly")
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Send offer to bot
            logger.info(f"Sending offer to bot {bot_uuid}")
            await target_ws.send(json.dumps({
                "type": "signal",
                "target": bot_uuid,
                "payload": {
                    "type": "offer",
                    "sdp": {
                        "type": pc.localDescription.type,
                        "sdp": pc.localDescription.sdp
                    },
                    "identity": {
                        "username": "Tester",
                        "picture": ""
                    }
                }
            }))

            # Wait for answer
            async for message in target_ws:
                data = json.loads(message)
                if data.get("type") == "signal" and data.get("payload", {}).get("type") == "answer":
                    logger.info("Received answer from bot!")
                    answer = RTCSessionDescription(
                        sdp=data["payload"]["sdp"]["sdp"],
                        type=data["payload"]["sdp"]["type"]
                    )
                    await pc.setRemoteDescription(answer)
                    logger.info("Remote description set. Connection established!")
                    break

            # Keep alive for a bit to see tracks
            await asyncio.sleep(5)
            await pc.close()
            logger.info("Test completed successfully")

if __name__ == "__main__":
    asyncio.run(test_call())
