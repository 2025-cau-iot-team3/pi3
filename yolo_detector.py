
import cv2
import json
import os
import time
import asyncio
from datetime import datetime
from ultralytics import YOLO
import numpy as np

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

# 실내에서 탐지할만한 객체만 허용
ALLOWED_NAME = {
    "person", "cat", "dog", "bottle", "cup", "bowl", "chair", "couch", "bed",
    "tv", "laptop", "mouse", "keyboard", "cell phone", "book", "clock",
    "toilet", "potted plant", "dining table", "refrigerator", "microwave",
    "oven", "toaster", "sink", "remote", "vase", "teddy bear", "hair drier",
    "toothbrush", "fork", "knife", "spoon", "wine glass", "cake", "pizza",
    "carrot", "apple", "banana", "broccoli", "orange", "handbag", "suitcase",
    "backpack", "umbrella"
}

COCO_NAMES = {
    0: 'person', 1: 'bicycle', 2: 'car', 3: 'motorcycle', 4: 'airplane', 5: 'bus', 6: 'train', 7: 'truck', 8: 'boat', 9: 'traffic light',
    10: 'fire hydrant', 11: 'stop sign', 12: 'parking meter', 13: 'bench', 14: 'bird', 15: 'cat', 16: 'dog', 17: 'horse', 18: 'sheep', 19: 'cow',
    20: 'elephant', 21: 'bear', 22: 'zebra', 23: 'giraffe', 24: 'backpack', 25: 'umbrella', 26: 'handbag', 27: 'tie', 28: 'suitcase', 29: 'frisbee',
    30: 'skis', 31: 'snowboard', 32: 'sports ball', 33: 'kite', 34: 'baseball bat', 35: 'baseball glove', 36: 'skateboard', 37: 'surfboard', 38: 'tennis racket', 39: 'bottle',
    40: 'wine glass', 41: 'cup', 42: 'fork', 43: 'knife', 44: 'spoon', 45: 'bowl', 46: 'banana', 47: 'apple', 48: 'sandwich', 49: 'orange',
    50: 'broccoli', 51: 'carrot', 52: 'hot dog', 53: 'pizza', 54: 'donut', 55: 'cake', 56: 'chair', 57: 'couch', 58: 'potted plant', 59: 'bed',
    60: 'dining table', 61: 'toilet', 62: 'tv', 63: 'laptop', 64: 'mouse', 65: 'remote', 66: 'keyboard', 67: 'cell phone', 68: 'microwave', 69: 'oven',
    70: 'toaster', 71: 'sink', 72: 'refrigerator', 73: 'book', 74: 'clock', 75: 'vase', 76: 'scissors', 77: 'teddy bear', 78: 'hair drier', 79: 'toothbrush'
}

class YoloDetector:
    def __init__(self, model_path, interval_seconds=5.0):
        print(f"YOLO 모델 로드 중... ({model_path})")
        self.model = YOLO(model_path, task="detect")
        self.class_names = COCO_NAMES
        self.interval = interval_seconds
        self.last_run_time = 0
        self.prev_gray = None # 이전 프레임 저장용
        self.motion_threshold = 30 # 민감도 (픽셀 변화량 기준)
        self.area_threshold = 200 # 얼마나 많은 영역이

        print("YOLO 모델 로드 완료.")

    def save_json(self, detection_data):
        """객체가 있을 때만 JSON 저장 + 일 단위 폴더 생성"""
        if len(detection_data["objects"]) == 0:
            return  # 빈 객체 → 저장 안 함

        today = datetime.now().strftime("%Y-%m-%d")
        folder = f"detections/{today}"

        if not os.path.exists(folder):
            os.makedirs(folder)

        timestamp = datetime.now().strftime("%H-%M-%S")
        filename = f"{folder}/detections_{timestamp}.json"

        with open(filename, "w") as f:
            json.dump(detection_data, f, indent=4)

        print(f"[YOLO] JSON 저장됨: {filename}")

    async def process_frame(self, frame):
        """
        프레임을 받아 추론하고 결과를 저장 (비동기 래퍼)
        CPU 부하를 줄이기 위해 실행 간격을 체크함.
        """
        current_time = time.time()

        # 설정된 간격(5초)이 지나지 않았으면 스킵
        if current_time - self.last_run_time < self.interval:
            return

        self.last_run_time = current_time

        # 추론 실행 (비동기 스레드에서 실행하여 서버 멈춤 방지)
        return await asyncio.to_thread(self._run_inference, frame)

    def _detect_motion(self, frame):
        """
        OpenCV를 사용하여 이전 프레임과 현재 프레임의 차이를 계산.
        변화가 크면 True, 아니면 False 반환.
        """
        # 1. 연산 속도를 위해 이미지 축소
        small_frame = cv2.resize(frame, (200, 150))

        # 2. 흑백 변환 (Grayscale) & 블러 (노이즈 제거)
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        # 첫 프레임이면 저장하고 통과 (기준점 설정)
        if self.prev_gray is None:
            self.prev_gray = gray
            return True # 첫 프레임은 일단 실행

        # 3. 차이 계산 (Absolute Difference)
        frame_delta = cv2.absdiff(self.prev_gray, gray)

        # 4. 임계값 적용 (Threshold) -> 차이가 큰 부분만 흰색(255)으로
        thresh = cv2.threshold(frame_delta, self.motion_threshold, 255, cv2.THRESH_BINARY)[1]
        
        # 5. 변화된 픽셀 수 계산
        changed_pixels = np.sum(thresh) / 255 # 흰색 픽셀 개수
        
        # 현재 프레임을 다음 비교를 위해 저장
        self.prev_gray = gray
        
        # 변화량이 기준치보다 큰지 확인
        if changed_pixels > self.area_threshold:
            return True
        else:
            return False

    def _run_inference(self, frame):
        """실제 추론 로직 (Blocking)"""
        try:
            if not self._detect_motion(frame):
                print("정지")
                return
            print(f"[YOLO] 추론 실행 중... (Time: {datetime.now().strftime('%H:%M:%S')})")
            # 원본 비율 유지, verbose=False로 로그 끔
            results = self.model(frame, verbose=False)[0]
            
            if not results.boxes:
                print("감지된 객체 없음 (Nothing detected)")
                return

            boxes = results.boxes.xyxy.cpu().numpy()
            scores = results.boxes.conf.cpu().numpy()
            classes = results.boxes.cls.cpu().numpy()
            names = self.class_names

            detection_data = {
                "timestamp": time.time(),
                "objects": []
            }
            print(f"   -> 총 {len(boxes)}개 객체 감지됨")

            for (x1, y1, x2, y2), score, cls in zip(boxes, scores, classes):
                label = names[int(cls)]
                print(label)
                if label in ALLOWED_NAME:
                    detection_data["objects"].append({
                        "label": label,
                        "confidence": round(float(score), 3),
                        "bbox": {
                            "x1": int(x1), "y1": int(y1),
                            "x2": int(x2), "y2": int(y2)
                        }
                    })

            # 결과가 있으면 저장
            print(detection_data)
            if detection_data["objects"]:
                return detection_data
            else:
                return None
        except Exception as e:
            print(f"[YOLO] 추론 중 에러: {e}")
