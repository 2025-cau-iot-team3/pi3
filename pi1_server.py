import asyncio
import json
import logging
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
import av
from picamera2 import Picamera2

# 라이브러리 설치
# sudo apt-get install python3-picamera2
# python3 -m venv venv --system-site-packages
# source venv/bin/activate
# pip install websockets aiortc

# 'pactl list sources'에서 찾은 마이크 이름
PULSE_DEVICE_NAME = "alsa_input.usb-TTGK_Technology_Hi-MAX_330212CA241009-00.mono-fallback"
active_players = set()

config = RTCConfiguration(
    iceServers=[
        RTCIceServer("stun:stun.l.google.com:19302")
    ]
)

class Picamera2VideoStreamTrack(VideoStreamTrack):
    # Picamera2를 사용하여 비디오 프레임을 캡처하고 스트리밍하는 트랙
    def __init__(self):
        super().__init__()
        print("Picamera2 초기화 중...")

        self.picam2 = Picamera2()
        config = self.picam2.create_video_configuration(
            main={"size": (240, 180)},
            lores={"size": (240, 180), "format": "YUV420"}
        )
        self.picam2.configure(config)
        self.picam2.start()
        print("Picamera2 카메라 초기화 완료.")

    async def recv(self):
        """비디오 프레임을 비동기적으로 캡처하여 반환"""
        pts, time_base = await self.next_timestamp()

        try:
            array = await asyncio.to_thread(self.picam2.capture_array, "lores")
        except Exception as e:
            print(f"Picamera2 캡처 에러: {e}")
            raise

        video_frame = av.VideoFrame.from_ndarray(array, format="yuv420p")
        video_frame.pts = pts
        video_frame.time_base = time_base

        return video_frame

    def stop(self):
        self.picam2.stop()
        print("Picamera2 카메라가 해제되었습니다.")

def create_pi_microphone_player():
    print(f"오디오 장치 '{PULSE_DEVICE_NAME}' (PulseAudio)를 엽니다...")
    return MediaPlayer(PULSE_DEVICE_NAME, format="pulse", options = {'rate':'8000', 'ac':'1'})

# WebSocket 핸들러 함수
async def handler(websocket):
    print(f"클라이언트 연결됨: {websocket.remote_address}")
    pc = RTCPeerConnection(configuration=config)
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        if pc.connectionState == "failed":
            await pc.close()

    cam_track = None
    mic_player = None
    
    try:
        cam_track = Picamera2VideoStreamTrack()
        pc.addTrack(cam_track)

        mic_player = create_pi_microphone_player()
        if mic_player.audio:
            active_players.add(mic_player)
            pc.addTrack(mic_player.audio)
        else:
            print("[에러] 오디오 트랙을 찾을 수 없습니다.")
            
    except Exception as e:
        print(f"미디어 장치 로드 실패: {e}")
        await websocket.close(code=1011, reason="Media device error")
        return

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                command = data.get("command")
                payload = data.get("payload", {})
                print(f"<- 수신: {command}")

                if command == "motor_control":
                    pass # TODO: 모터 제어 명령 처리

                elif command == "video_offer":
                    print("[WebRTC] Offer(1단계) 수신")
                    offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
                    await pc.setRemoteDescription(offer)
                    
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)
                    while pc.iceGatheringState != "complete":
                        await asyncio.sleep(0.1)
                    print("[ICE] (서버) 주소 수집 완료.")
                    
                    response = {
                        "source": "pi_1_system",
                        "command": "video_answer",
                        "payload": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
                    }
                    await websocket.send(json.dumps(response))
                    print("[WebRTC] Answer(2단계) 전송 완료. P2P 시작.")
            
            except Exception as e:
                print(f"[에러] 메시지 처리 중 오류: {e}")

    except websockets.exceptions.ConnectionClosed:
        print(f"클라이언트 연결 끊어짐: {websocket.remote_address}")
    finally:
        await pc.close()
        if cam_track:
            cam_track.stop()
        if mic_player in active_players:
            active_players.remove(mic_player) # (오디오 정리 추가)
            mic_player.stop()

async def main():
    logging.basicConfig(level=logging.INFO)
    async with websockets.serve(handler, "0.0.0.0", 8000):
        print("Pi 1 통합 WebSocket 서버가 8000번 포트에서 시작되었습니다.")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
