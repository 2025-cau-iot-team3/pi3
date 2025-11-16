import asyncio
import json
import logging
import cv2
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
import threading
import queue
import pyaudio

# 라이브러리 설치
# sudo apt-get install portaudio19-dev
# python3 -m venv venv --system-site-packages
# source venv/bin/activate
# pip install opencv-python aiortc websockets pyaudio

# Pi 1 서버의 IP 주소로 변경
PI_1_IP = "192.168.0.22" 
URI = f"ws://{PI_1_IP}:8000"

config = RTCConfiguration(
    iceServers=[
        RTCIceServer("stun:stun.l.google.com:19302")
    ]
)

pc = RTCPeerConnection(configuration=config)

frame_queue = queue.Queue()
active_recorders = set()
audio_stream = None

p = pyaudio.PyAudio()
CHUNK_SIZE = 1024

@pc.on("track")
def on_track(track):
    global audio_stream
    print(f"Pi 1로부터 트랙({track.kind}) 수신 시작!")
    # 비디오 트랙 수신 처리
    async def display_video(track):
        while True:
            try:
                frame = await track.recv()
                img = frame.to_ndarray(format="bgr24")
                if frame_queue.qsize() < 2:
                    frame_queue.put(img)
            except Exception as e:
                print(f"[영상 수신 에러] {e}")
                break

    if track.kind == "video":
        asyncio.create_task(display_video(track))
    if track.kind == "audio":
        async def play_audio(track):
            global audio_stream
            try:
                frame = await track.recv()
                audio_stream = p.open(
                    format=p.get_format_from_width(frame.format.bytes),
                    channels=len(frame.layout.channels),
                    rate=frame.sample_rate,
                    output=True,
                    frames_per_buffer=CHUNK_SIZE
                )
                print(f"[오디오] PyAudio 스피커({frame.sample_rate}Hz) 재생 시작.")
                while True:
                    frame = await track.recv()
                    await asyncio.to_thread(
                        audio_stream.write, 
                        frame.to_ndarray().tobytes()
                    )
            except Exception as e:
                print(f"   [오디오] PyAudio 재생 실패: {e}")
            finally:
                if audio_stream:
                    audio_stream.stop_stream()
                    audio_stream.close()
                print("   [오디오] PyAudio 스트림 종료.")

        asyncio.create_task(play_audio(track))


async def send_motor_commands(websocket):
    while True:
        try:
            command = {
                "source": "pi_3_controller", "command": "motor_control",
                "payload": {"left": 80, "right": 80}
            }
            await websocket.send(json.dumps(command))
            await asyncio.sleep(0.1)
        except websockets.exceptions.ConnectionClosed:
            print("[모터 전송 중단] WebSocket 연결 끊어짐")
            break

# Pi 1 서버와 시그널링 및 상태 업데이트를 처리하는 함수
async def handle_signaling_and_status(websocket):    
    print("[WebRTC] Offer(1단계) 생성 중...")
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    print("   [ICE] (클라이언트) 주소(Candidate) 수집 중...")
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)
    print("   [ICE] (클라이언트) 주소 수집 완료.")
    
    # '주소'가 모두 포함된 최종 'Offer'를 전송
    offer_command = {
        "source": "pi_3_controller",
        "command": "video_offer",
        "payload": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
    }
    await websocket.send(json.dumps(offer_command))
    print("   [WebRTC] Offer(1단계) 전송 완료.")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                command = data.get("command")
                if command == "video_answer":
                    print("[WebRTC] Answer(2단계) 수신")
                    payload = data.get("payload", {})
                    answer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
                    await pc.setRemoteDescription(answer)
                    print("[WebRTC] P2P 연결 수립 완료!")

                elif command == "status_update":
                    print(f"[상태 수신] {data.get('payload')}")

            except Exception as e:
                print(f"메시지 수신/처리 중 에러: {e}")

    except websockets.exceptions.ConnectionClosed:
        print("[시그널링 중단] WebSocket 연결 끊어짐")

# WebSocket 메인 함수
async def main_async():
    logging.basicConfig(level=logging.INFO)
    print(f"Pi 1 서버 ({URI})에 연결 시도 중...")
    try:
        async with websockets.connect(URI) as websocket:
            print("Pi 1 서버에 성공적으로 연결되었습니다.")
            motor_task = asyncio.create_task(send_motor_commands(websocket))
            signal_task = asyncio.create_task(handle_signaling_and_status(websocket))
            await asyncio.gather(motor_task, signal_task)
    except Exception as e:
        print(f"서버 연결 실패: {e}")
    finally:
        await pc.close()

# 네트워크 스레드 시작 함수
def start_network_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main_async())

# 메인 스레드: OpenCV GUI 실행
if __name__ == "__main__":    
    network_thread = threading.Thread(target=start_network_thread, daemon=True)
    network_thread.start()
    cv2.namedWindow("Pi 1 Camera Feed", cv2.WINDOW_AUTOSIZE)
    while True:
        try:
            img = frame_queue.get(timeout=1.0)
            cv2.imshow("Pi 1 Camera Feed", img)
        except queue.Empty:
            pass
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    print("종료")
    cv2.destroyAllWindows()
