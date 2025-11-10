import asyncio
import json
import logging
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

# 라이브러리 설치
# pip install aiortc websockets

# 'av'가 'pulse'를 지원하도록 빌드하기 위한 종속성 설치 (Debian/Ubuntu 기준)
# sudo apt-get install libavdevice-dev
# pip uninstall av
# pip install av --no-binary av

# 'av'가 'pulse'를 지원하도록 재설치했기 때문에 이 코드가 작동해야 함

# 'pactl list sources'에서 찾은 마이크 이름
#PULSE_DEVICE_NAME = "alsa_input.usb-TTGK_Technology_Hi-MAX_330212CA241009-00.mono-fallback"
PULSE_DEVICE_NAME = "hw:2" 
VIDEO_PATH = "/dev/video0"
active_players = set()

def create_pi_microphone_player():
    print(f"오디오 장치 '{PULSE_DEVICE_NAME}' (PulseAudio)를 엽니다...")
    return MediaPlayer(PULSE_DEVICE_NAME, format="alsa",options={
    'channels': '1',
    'rate': '16000'
})

def create_pi_camera_player():
    return MediaPlayer(VIDEO_PATH, format="v4l2", options={
        "video_size": "640x480", "framerate": "30"
    })

# WebSocket 핸들러 함수
async def handler(websocket):
    print(f"클라이언트 연결됨: {websocket.remote_address}")
    pc = RTCPeerConnection()

    # ICE 연결 상태 변경 이벤트 핸들러
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        #print(f"[ICE] 연결 상태: {pc.connectionState}") # 로그 상세화
        if pc.connectionState == "failed":
            await pc.close()

# Pi 카메라 및 마이크 트랙 생성 및 추가
    cam_player = create_pi_camera_player()
    #mic_player = create_pi_microphone_player()
    if cam_player.video:
        active_players.add(cam_player) # 추적을 위해 세트에 추가
        pc.addTrack(cam_player.video)
    else:
        print("[에러] 비디오 트랙을 찾을 수 없습니다.")
    #if mic_player.audio:
    #    active_players.add(mic_player)
    #    pc.addTrack(mic_player.audio)
    #else:
    #    print("[에러] 오디오 트랙을 찾을 수 없습니다.")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                command = data.get("command")
                payload = data.get("payload", {})
                print(f"<- 수신: {command}")

                # Pi 2로 모터 제어 명령 전달 (추후 구현)
                if command == "motor_control":
                    pass # TODO: 모터 제어 명령 처리

                # WebRTC Offer/Answer 처리
                elif command == "video_offer": 
                    print("[WebRTC] Offer(1단계) 수신")
                    offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
                    await pc.setRemoteDescription(offer)
                    
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)
                    while pc.iceGatheringState != "complete":
                        await asyncio.sleep(0.1)
                    print("[ICE] (서버) 주소 수집 완료.")
                    # '주소'가 모두 포함된 최종 'Answer'를 전송
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
        if cam_player in active_players:
            active_players.remove(cam_player)

# WebSocket 서버 메인 함수
async def main():
    logging.basicConfig(level=logging.INFO)
    async with websockets.serve(handler, "0.0.0.0", 8000):# 웹소켓 서버 시작
        print("Pi 1 통합 WebSocket 서버가 8000번 포트에서 시작되었습니다.")
        await asyncio.Future() # 무한 대기

if __name__ == "__main__":
    asyncio.run(main())
