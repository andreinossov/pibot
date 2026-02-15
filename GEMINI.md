# pibot — WebRTC Video Bot

Python bot that connects to pikalo signaling rooms and plays back a sample video via WebRTC (aiortc).

## Architecture
- `bot.py` — Main bot: connects to `sig.piedpie.net`, handles WebRTC offer/answer, streams `sample.mp4`
- `test_bot_caller.py` — Test harness for calling the bot
- Room ID derived from `bot@piedpie.net` hex-encoded

## Dependencies
- `aiortc` for WebRTC
- `websockets` for signaling
- `pm2` via `ecosystem.config.js` for process management
