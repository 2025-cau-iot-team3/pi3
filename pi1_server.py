import asyncio
import json
import logging
import websockets
import cv2
import os
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer, MediaRelay
import av
from picamera2 import Picamera2
from yolo_detector import YoloDetector

# ----------------------------------------------------
# 환경 설정
# ----------------------------------------------------
YOLO_MODEL_PATH = "/home/iotuser/yolov8n.onnx"
YOLO_INTERVAL = 5
EMOTION_INTERVAL = 1
SENSOR_FILE_PATH = "../pi2/sensor/sensor_state"

PULSE_DEVICE_NAME = "alsa_input.usb-TTGK_Technology_Hi-MAX_330212CA241009-00.mono-fallback"

config = RTCConfiguration(
    iceServers=[RTCIceServer("stun:stun.l.google.com:19302")]
)

clients = {
    "pi2_hardware": None,
    "pi3_controller": None,
    "pi4_controller": None,
}

# ----------------------------------------------------
# 전역 변수
# ----------------------------------------------------
global_picam2 = None
global_yolo = None
global_mic = None
global_relay = None

last_yolo_label = None


# ----------------------------------------------------
# 카메라 영상 스트림 트랙
# ----------------------------------------------------
class Picamera2VideoStreamTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        frame = await asyncio.to_thread(global_picam2.capture_array, "lores")
        video_frame = av.VideoFrame.from_ndarray(frame, format="yuv420p")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame


# ----------------------------------------------------
# 마이크 초기화 (없어도 서버 죽지 않음)
# ----------------------------------------------------
def create_pi_microphone_player():
    try:
        print(f"[AUDIO] Opening '{PULSE_DEVICE_NAME}'…")
        return MediaPlayer(PULSE_DEVICE_NAME, format="pulse", options={"rate": "8000", "channels": "1"})
    except Exception as e:
        print(f"[AUDIO ERROR] 마이크 사용 불가: {e}")
        return None


# ----------------------------------------------------
# 센서 파일 모니터링 (0.1초마다 읽기)
# ----------------------------------------------------
async def run_sensor_monitor():
    print("[SENSOR] 센서 모니터링 시작")

    while True:
        await asyncio.sleep(0.1)


# ----------------------------------------------------
# YOLO 비동기 루프 (5초마다 추론)
# ----------------------------------------------------
async def run_background_yolo():
    global last_yolo_label
    print("[YOLO] 백그라운드 루프 시작")

    while True:
        try:
            if clients["pi3_controller"] == None:
                print("[YOLO] 추론 시작…")

                def infer():
                    frame = global_picam2.capture_array("lores")
                    bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
                    return global_yolo._run_inference(bgr)

                result = await asyncio.to_thread(infer)

                print(f"YOLO result: {result}")

                if result and result.get("objects"):
                    best = max(result["objects"], key=lambda o: o["confidence"])
                    last_yolo_label = best["label"]
                    print(f"[YOLO] best label = {last_yolo_label}")
                else:
                    last_yolo_label = None

        except Exception as e:
            print(f"[YOLO ERROR] {e}")

        await asyncio.sleep(YOLO_INTERVAL)


# ----------------------------------------------------
# 감정 계산 함수 (하드코딩 규칙)
# ----------------------------------------------------
def compute_emotion(sensor, yolo_label):
    obj = sensor.get("obj")
    gyro = sensor.get("gyro", [0, 0, 0])
    dist = sensor.get("ultrasonic", -1)

    # 1) 자이로
    if max(abs(g) for g in gyro) > 50:
        return "dizzy"

    # 2) 위험 물체
    if yolo_label in ["knife", "scissors", "cat"]:
        return "scary"

    # 3) YOLO 감지
    if yolo_label == "person":
        return "happy"

    # 4) 거리 멀면 scary
    if dist >= 200:
        return "scary"

    return "neutral"


# ----------------------------------------------------
# 감정 1초마다 강제 계산 & Pi2 로 전송
# ----------------------------------------------------
async def emotion_forced_loop():
    print("[EMOTION] 강제 감정 루프 시작")

    while True:
        if clients["pi3_controller"] == None:
            try:
                sensor = {}
                if os.path.exists(SENSOR_FILE_PATH):
                    try:
                        with open(SENSOR_FILE_PATH, "r") as f:
                            sensor = json.load(f)
                    except:
                        sensor = {}

                emotion = compute_emotion(sensor, last_yolo_label)

                print(f"[EMOTION] → {emotion}")

                # Pi2 에 전송
                ws = clients.get("pi2_hardware")
                if ws:
                    try:
                        await ws.send(json.dumps({
                            "command": "emotion",
                            "payload": {"emo": emotion}
                        }))
                        print(f"[SEND] Emotion '{emotion}' → Pi2")
                    except Exception as e:
                        print(f"[SEND ERROR] Pi2 전송 실패: {e}")

            except Exception as e:
                print(f"[EMOTION ERROR] {e}")

        await asyncio.sleep(1)


# ----------------------------------------------------
# heartbeat
# ----------------------------------------------------
async def heartbeat_loop():
    while True:
        for role, ws in clients.items():
            if ws:
                try:
                    await ws.send(json.dumps({"command": "ping"}))
                except:
                    pass
        await asyncio.sleep(3)


# ----------------------------------------------------
# WebSocket handler
# ----------------------------------------------------
async def handler(websocket):
    print(f"[WS] 연결됨: {websocket.remote_address}")

    pc = RTCPeerConnection(configuration=config)

    cam_track = Picamera2VideoStreamTrack()
    pc.addTrack(cam_track)

    if global_mic and global_relay:
        pc.addTrack(global_relay.subscribe(global_mic.audio))

    try:
        async for msg in websocket:
            data = json.loads(msg)
            cmd = data.get("command")
            payload = data.get("payload", {})

            print(f"[WS] 수신: {cmd}")

            if cmd == "register":
                role = payload.get("role")
                if role in clients:
                    clients[role] = websocket
                    print(f"[WS] {role} 등록 완료")

            elif cmd == "motor_control":
                pi2 = clients.get("pi2_hardware")
                if pi2:
                    await pi2.send(msg)

            elif cmd == "video_offer":
                offer = RTCSessionDescription(
                    sdp=payload["sdp"],
                    type=payload["type"]
                )
                await pc.setRemoteDescription(offer)

                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                resp = {
                    "command": "video_answer",
                    "payload": {
                        "sdp": answer.sdp,
                        "type": answer.type,
                    },
                }
                await websocket.send(json.dumps(resp))

    except websockets.exceptions.ConnectionClosed:
        print(f"[WS] 연결 종료됨: {websocket.remote_address}")

    finally:
        for role, ws in clients.items():
            if ws == websocket:
                clients[role] = None
                print(f"[WS] {role} 연결 해제됨")


# ----------------------------------------------------
# 메인
# ----------------------------------------------------
async def main():
    logging.basicConfig(level=logging.INFO)

    global global_picam2, global_yolo, global_mic, global_relay

    global_picam2 = Picamera2()
    cfg = global_picam2.create_video_configuration(
        main={"size": (640, 480)},
        lores={"size": (640, 480), "format": "YUV420"}
    )
    global_picam2.configure(cfg)
    global_picam2.start()
    print("Picamera2 초기화 완료")

    global_mic = create_pi_microphone_player()
    global_relay = MediaRelay()

    global_yolo = YoloDetector(YOLO_MODEL_PATH, interval_seconds=YOLO_INTERVAL)

    asyncio.create_task(run_background_yolo())
    asyncio.create_task(emotion_forced_loop())
#    asyncio.create_task(heartbeat_loop())

    async with websockets.serve(handler, "0.0.0.0", 8000):
        print("Pi1 WebSocket 서버 시작: 8000")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
