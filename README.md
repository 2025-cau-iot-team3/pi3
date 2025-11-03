# 컨트롤러
## 구성
LCD패널, 조이스틱, 4버튼, 라즈베리파이
## 기능
영상/음성 송출

조이스틱을 통한 기기제어

## 통신 명세서
### 기본 JSON 구조
```json
{
  "source": "누가 보냈는지",
  "command": "명령어"
  "payload": {...}
}
```
영상 및 음성 송출은 WebRTC를 통해 그 외는 websocket으로 통신한다.

### Pi3 -> Pi1/Pi2 (모터 제어 등)

|command(명령어)|source(송신자)|payload(데이터)|설명| 
|---|---|---|---|
|motor_control|pi3|{"left:80,""right":80}| 컨트롤러가 보낸 실시간 모터 속도|
|theme_change|pi3|{"thema":"dark"}|테마 변경 요청|
|expression_change|pi3|{"emotion":"happy"}|컨트롤러에서 표정 변경을 요청|

### Pi3 <-> Pi1 (영상/음성 연결)
|순서|command(명령어)|source(송신자)|payload(데이터)|설명| 
|---|---|---|---|---|
|1단계|request_video_start|pi3|{"password: "...""}|영상 연결 요청|
|2단계|video_offer|pi1|{"sdp": "...", "type":"offer"}| webRTC를 준비한 후 offer를 보낸다.|
|3단계|video_answer|pi3|{"sdp": "...", "type":"answer"}| 확인 응답을 보낸다.|