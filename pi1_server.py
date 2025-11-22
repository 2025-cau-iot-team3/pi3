import asyncio
import json
import logging
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
import av
from picamera2 import Picamera2
from yolo_detector import YoloDetector
import cv2

# 라이브러리 설치
# sudo apt-get install python3-picamera2
# python3 -m venv venv --system-site-packages
# source venv/bin/activate
# pip install websockets aiortc

# 'pactl list sources'에서 찾은 마이크 이름
PULSE_DEVICE_NAME = "alsa_input.usb-TTGK_Technology_Hi-MAX_330212CA241009-00.mono-fallback"
YOLO_MODEL_PATH = "/home/hunseok/yolov8n.onnx"
YOLO_INTERVAL = 5.0 # 5초마다 실행

active_players = set()
clients = {
    "pi2_hardware": None,
    "pi3_controller": None
}

config = RTCConfiguration(
    iceServers=[
        RTCIceServer("stun:stun.l.google.com:19302")
    ]
)

global_picam2 = None
global_yolo = None

class Picamera2VideoStreamTrack(VideoStreamTrack):
    # Picamera2를 사용하여 비디오 프레임을 캡처하고 스트리밍하는 트랙
    def __init__(self):
        super().__init__()

    async def recv(self):
        """비디오 프레임을 비동기적으로 캡처하여 반환"""
        pts, time_base = await self.next_timestamp()

        try:
            await asyncio.sleep(0.01)
            array = await asyncio.to_thread(global_picam2.capture_array, "lores")
        except Exception as e:
            print(f"Picamera2 캡처 에러: {e}")
            raise

        video_frame = av.VideoFrame.from_ndarray(array, format="yuv420p")
        video_frame.pts = pts
        video_frame.time_base = time_base

        return video_frame

    def stop(self):
        super().stop()

def create_pi_microphone_player():
    print(f"오디오 장치 '{PULSE_DEVICE_NAME}' (PulseAudio)를 엽니다...")
    return MediaPlayer(PULSE_DEVICE_NAME, format="pulse", options = {'rate':'8000', 'ac':'1'})

# WebSocket 핸들러 함수
async def handler(websocket):
    print(f"클라이언트 연결됨: {websocket.remote_address}")
    # 접속한 클라이언트를 목록에 추가
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

    except Exception as e:
        print(f"카메라 장치 로드 실패: {e}")
        await websocket.close(code=1011, reason="Camera Error")
        return
    try:
        mic_player = create_pi_microphone_player()
        if mic_player and mic_player.audio:
            active_players.add(mic_player)
            pc.addTrack(mic_player.audio)
            print("오디오 트랙 추가 완료.")
        else:
            print("오디오 트랙을 찾을 수 없습니다. (영상만 전송합니다)")
            mic_player = None
    except Exception as e:
        print(f"오디오 장치 연결 실패 (무시하고 진행): {e}")
        mic_player = None

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                command = data.get("command")
                payload = data.get("payload", {})
                print(f"<- 수신: {command}")
                if command == "register":
                    role = data.get("payload", {}).get("role")
                    if role in clients:
                        clients[role] = websocket
                        print(f"{role} 등록 완료")
                    else:
                        print(f"알 수 없는 역할: {role}")

                if command == "motor_control":
                    print(f"모터 명령 수신: {data.get('payload')}")
                    pi2_ws = clients["pi2_hardware"]
                    if pi2_ws:
                        await pi2_ws.send(message)
                    else:
                        print("Pi 2가 연결되어 있지 않아 명령을 보낼 수 없습니다.")

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
        for role, ws in clients.items():
            if ws == websocket:
                clients[role] = None
                print(f"-> {role} 연결 해제됨")
                break
        await pc.close()
        if cam_track:
            try:
                cam_track.stop()
            except Exception as e:
                print(f"카메라 정리 중 에러: {e}")

        if mic_player:
            if mic_player in active_players:
                active_players.remove(mic_player)
            try:
                if hasattr(mic_player, 'stop'):
                    mic_player.stop()
                elif hasattr(mic_player, 'audio') and mic_player.audio:
                     mic_player.audio.stop()
            except Exception as e:
                print(f"오디오 정리 중 에러 (무시): {e}")

async def run_background_yolo():
    print("백그라운드 YOLO 감시 루프 시작...")
    while True:
        try:
            if global_picam2 and global_yolo:
                frame = await asyncio.to_thread(global_picam2.capture_array, "lores")
                bgr_frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
                result = await global_yolo.process_frame(bgr_frame)
                print(f"YOLO result: {result}")
                if result and clients["pi2_hardware"]:
                    msg = {
                        "source": "pi1_server",
                        "command": "yolo_detection",
                        "payload": result,
                    }
                    await clients["pi2_hardware"].send(json.dumps(msg))
                    print("YOLO 결과 전송 완료")

            await asyncio.sleep(1) 

        except Exception as e:
            print(f"YOLO 루프 에러: {e}")
            await asyncio.sleep(5) # 에러 시 5초 대기

async def main():
    logging.basicConfig(level=logging.INFO)
    global global_picam2, global_yolo
    global_picam2 = Picamera2()
    config_cam = global_picam2.create_video_configuration(
        main={"size": (640, 480)},
        lores={"size": (640, 480), "format": "YUV420"}
    )
    global_picam2.configure(config_cam)
    global_picam2.start()
    print("Picamera2 카메라 초기화 완료.")
    # 2. YOLO 초기화 (5초 간격 설정)
    global_yolo = YoloDetector(YOLO_MODEL_PATH, interval_seconds=YOLO_INTERVAL)

    # 3. 백그라운드 YOLO 태스크 시작 (서버와 동시에 돔)
    asyncio.create_task(run_background_yolo())

    try:
        async with websockets.serve(handler, "0.0.0.0", 8000):
            print("Pi 1 통합 WebSocket 서버가 8000번 포트에서 시작되었습니다.")
            await asyncio.Future()
    finally:
        global_picam2.stop()

if __name__ == "__main__":
    asyncio.run(main())
